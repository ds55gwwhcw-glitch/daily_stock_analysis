"""Research-only SH/SZ A-share expanding walk-forward and provisional targets.

No broker orders, leverage, or portfolio fills. Historical adjusted prices are
NOT a verified point-in-time corporate-action tape. Outputs are preliminary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("quant_research")
STATE = ROOT / "state" / "full_v3"
CACHE = ROOT / "full_cache" / "daily"
OUT = ROOT / "results" / "walkforward_v1"
FACTORS = ["momentum_252_21", "momentum_63", "reversal_21", "low_vol_60", "log_liquidity_20"]
OOS_START = pd.Timestamp("2023-01-01")
HOLDINGS = 10
BUY_COST = 0.0015
SELL_COST = 0.0020


def tradable_code(code: str) -> bool:
    if code.startswith("sh."):
        return code[3:6] in {"600", "601", "603", "605"}
    if code.startswith("sz."):
        return code[3:6] in {"000", "001", "002", "003", "300", "301", "302"}
    return False


def json_write(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def audit_universe():
    OUT.mkdir(parents=True, exist_ok=True)
    progress = json.loads((STATE / "progress.json").read_text(encoding="utf-8"))
    gate_path = STATE / "repair_quality_gate.json"
    gate = (json.loads(gate_path.read_text(encoding="utf-8"))
            if gate_path.exists() else {"model_data_gate": "BLOCKED", "reason": "Repair not audited"})
    universe = pd.read_csv(STATE / "universe_shsz_frozen.csv", dtype=str)
    manifest = pd.read_csv(STATE / "history_manifest.csv", dtype={"code": str, "status": str})
    if (not progress.get("completed") or len(universe) != len(manifest)
            or universe["code"].duplicated().any() or manifest["code"].duplicated().any()):
        raise RuntimeError("Frozen universe/manifest integrity failure")
    if set(universe["code"]) != set(manifest["code"]):
        raise RuntimeError("Universe/manifest code sets differ")
    permitted = universe[universe["code"].map(tradable_code)].copy()
    permitted = permitted.merge(manifest[["code", "result"]], on="code", how="left", validate="one_to_one")
    permitted["broker_board"] = np.where(permitted["code"].str.startswith("sh."), "SH_MAIN",
                               np.where(permitted["code"].str[3:6].isin(["300", "301", "302"]),
                                        "CHINEXT", "SZ_MAIN"))
    permitted.to_csv(OUT / "permitted_universe.csv", index=False)
    stats = {
        "total_shsz_frozen": len(universe),
        "broker_permitted_code_count": len(permitted),
        "broker_permitted_error_count": int((permitted["result"] != "ok").sum()),
        "excluded_bse_and_star": True, "oos_start": str(OOS_START.date()),
        "data_gate": gate.get("model_data_gate", "BLOCKED"),
        "research_only": True, "point_in_time_corporate_actions_verified": False,
    }
    json_write(OUT / "universe_audit.json", stats)
    return permitted, gate, stats


def market_calendar():
    anchor_path = CACHE / "sh_600000.parquet"
    if not anchor_path.exists():
        raise RuntimeError("Anchor market-calendar cache sh_600000.parquet missing")
    x = pd.read_parquet(anchor_path, columns=["date", "tradestatus"]).copy()
    x["date"] = pd.to_datetime(x["date"])
    x = x[x["tradestatus"].astype(str) == "1"]
    sessions = pd.DatetimeIndex(sorted(x["date"].dropna().unique()))
    if len(sessions) < 2000:
        raise RuntimeError("Insufficient market calendar depth")
    month_ends = x.groupby(x["date"].dt.to_period("M"))["date"].max().sort_values().to_list()
    month_ends = pd.DatetimeIndex(month_ends)
    now_shanghai = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
    complete = month_ends[month_ends.to_period("M") < now_shanghai.to_period("M")]
    if len(complete) < 80:
        raise RuntimeError("Insufficient completed monthly signals")
    return sessions, complete, month_ends[-1]


def extract_stock(code: str, ipo: str, out: str, signals, entry_dates, exit_dates):
    path = CACHE / (code.replace(".", "_") + ".parquet")
    if not path.exists():
        raise RuntimeError(f"Missing parquet for {code}")
    cols = ["date", "open", "close", "amount", "volume", "tradestatus", "isST"]
    x = pd.read_parquet(path, columns=cols)
    if x.empty:
        return None
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date")
    for c in ["open", "close", "amount", "volume"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.set_index("date")
    close = x["close"].where(x["close"] > 0)
    returns = close.pct_change(fill_method=None)
    scores = pd.DataFrame(index=x.index)
    scores[FACTORS[0]] = close.shift(21) / close.shift(252) - 1
    scores[FACTORS[1]] = close / close.shift(63) - 1
    scores[FACTORS[2]] = -(close / close.shift(21) - 1)
    scores[FACTORS[3]] = -returns.rolling(60, min_periods=50).std()
    scores[FACTORS[4]] = np.log1p(
        x["amount"].where(x["amount"] >= 0).rolling(20, min_periods=18).mean())
    valid = (x["tradestatus"].astype(str) == "1") & (x["isST"].astype(str) != "1")
    valid &= (x["volume"] > 0) & (x["close"] > 0) & (x["open"] > 0)
    ipo_dt = pd.to_datetime(ipo, errors="coerce")
    out_dt = pd.to_datetime(out, errors="coerce")
    if pd.notna(ipo_dt):
        valid &= (x.index - ipo_dt).days >= 180
    if pd.notna(out_dt):
        valid &= x.index <= out_dt

    signal_view = scores.reindex(signals)
    signal_view["eligible"] = valid.reindex(signals).fillna(False).astype(bool).to_numpy()
    signal_view = signal_view[signal_view["eligible"]].drop(columns=["eligible"]).copy()
    signal_view = signal_view.replace([np.inf, -np.inf], np.nan).dropna(subset=FACTORS)
    if signal_view.empty:
        return None
    dates = pd.DatetimeIndex(signal_view.index)
    entry_map = pd.Series(entry_dates.values, index=signals)
    exit_map = pd.Series(exit_dates.values, index=signals)
    buy_dates = pd.to_datetime(entry_map.reindex(dates).to_numpy())
    sell_dates = pd.to_datetime(exit_map.reindex(dates).to_numpy())
    # Research-only mark-to-market proxy: never remove a suspension from OOS.
    # If the next-session buy cannot execute, keep that slot as cash (zero return).
    # If the scheduled exit cannot execute, mark at the last available positive
    # close and disclose the blocked exit. This does NOT free trapped capital or
    # simulate executable liquidation; model output remains non-tradable.
    valid_entry = pd.notna(buy_dates)
    valid_exit = pd.notna(sell_dates)
    exit_lookup = pd.DatetimeIndex([
        d if pd.notna(d) else x.index[-1] for d in sell_dates
    ])
    buys = x["open"].reindex(buy_dates).to_numpy(dtype=float)
    sells = x["open"].reindex(exit_lookup).to_numpy(dtype=float)
    buy_trade = ((x["tradestatus"].astype(str) == "1")
                 .reindex(buy_dates).fillna(False).to_numpy(dtype=bool))
    sell_trade = ((x["tradestatus"].astype(str) == "1")
                  .reindex(exit_lookup).fillna(False).to_numpy(dtype=bool))
    executed_buy = valid_entry & buy_trade & np.isfinite(buys) & (buys > 0)
    executed_sell = valid_exit & sell_trade & np.isfinite(sells) & (sells > 0)
    marked_close = (close.ffill().reindex(exit_lookup, method="ffill")
                    .to_numpy(dtype=float))
    exit_price = np.where(executed_sell, sells, marked_close)
    label = np.full(len(dates), np.nan, dtype=float)
    cash_slots = valid_entry & valid_exit & ~executed_buy
    label[cash_slots] = 0.0
    held = executed_buy & valid_exit & np.isfinite(exit_price) & (exit_price > 0)
    label[held] = exit_price[held] / buys[held] - 1
    signal_view["code"] = code
    signal_view["entry_date"] = buy_dates
    signal_view["exit_date"] = sell_dates
    signal_view["executed_buy_proxy"] = executed_buy
    signal_view["blocked_buy_proxy"] = cash_slots
    signal_view["blocked_exit_mark_proxy"] = held & ~executed_sell
    signal_view["forward_return_proxy"] = label
    signal_view.index.name = "signal_date"
    return signal_view.reset_index()


def prepare_panel(permitted):
    market_dates, monthly, latest_date = market_calendar()
    signals = monthly.append(pd.DatetimeIndex([latest_date])).unique().sort_values()
    pos = market_dates.get_indexer(signals)
    valid_pos = np.where((pos >= 0) & (pos + 1 < len(market_dates)), pos + 1, -1)
    entries = pd.DatetimeIndex([market_dates[i] if i >= 0 else pd.NaT for i in valid_pos])
    exits = entries[1:].append(pd.DatetimeIndex([pd.NaT]))
    parts, missing = [], []
    records = permitted[permitted["result"] == "ok"].to_dict("records")
    for i, row in enumerate(records, 1):
        try:
            part = extract_stock(row["code"], row.get("ipoDate", ""),
                                 row.get("outDate", ""), signals, entries, exits)
            if part is not None:
                parts.append(part)
        except Exception as exc:
            missing.append({"code": row["code"], "error": str(exc)[:240]})
        if i % 500 == 0:
            print(f"Feature extraction {i}/{len(records)}", flush=True)
    pd.DataFrame(missing, columns=["code", "error"]).to_csv(
        OUT / "missing_or_invalid_cache.csv", index=False)
    if missing:
        raise RuntimeError(f"{len(missing)} permitted symbols have invalid cache")
    if not parts:
        raise RuntimeError("No eligible model features")
    panel = pd.concat(parts, ignore_index=True)
    panel["signal_date"] = pd.to_datetime(panel["signal_date"])
    for _, g in panel.groupby("signal_date").groups.items():
        z = panel.loc[g, FACTORS].astype(float)
        z = z.clip(lower=z.quantile(.02), upper=z.quantile(.98), axis=1)
        sd = z.std(axis=0, ddof=0).replace(0, np.nan)
        panel.loc[g, FACTORS] = ((z - z.mean(axis=0)) / sd).fillna(0.0).to_numpy()
    return panel.sort_values(["signal_date", "code"]).reset_index(drop=True), latest_date


def ridge(train, alpha=100.0):
    X = train[FACTORS].to_numpy(dtype=np.float64)
    y = train["forward_return_proxy"].to_numpy(dtype=np.float64)
    xm, ym = X.mean(axis=0), y.mean()
    xc, yc = X - xm, y - ym
    beta = np.linalg.solve(xc.T @ xc + alpha * np.eye(len(FACTORS)), xc.T @ yc)
    return float(ym - xm @ beta), beta


def research(panel, latest_date):
    periods, prev, latest_scores = [], set(), None
    blocked_buys_total, blocked_exits_total = 0, 0
    for dt, group in panel.groupby("signal_date", sort=True):
        # Training labels must already have fully exited before this signal.
        train = panel[(panel["exit_date"] < dt) & panel["forward_return_proxy"].notna()]
        if len(train) < 15000 or train["signal_date"].nunique() < 24:
            continue
        intercept, coeff = ridge(train)
        group = group.copy()
        group["score"] = intercept + group[FACTORS].to_numpy(float) @ coeff
        group = group.sort_values("score", ascending=False)
        if dt == latest_date:
            latest_scores = group
        if dt < OOS_START or dt == latest_date or len(group) < HOLDINGS:
            continue
        top = group.head(HOLDINGS)
        if top["forward_return_proxy"].isna().any():
            # The final month has no realized future exit. Never invent a label.
            if top["exit_date"].isna().any():
                print(f"Incomplete future OOS window {dt.date()}", flush=True)
                continue
            raise RuntimeError(
                f"Unknown mark for OOS positions on {dt.date()}; "
                "do not silently drop securities")
        gross = float(top["forward_return_proxy"].mean())
        blocked_buys = int(top["blocked_buy_proxy"].sum())
        blocked_exits = int(top["blocked_exit_mark_proxy"].sum())
        blocked_buys_total += blocked_buys
        blocked_exits_total += blocked_exits
        codes = set(top["code"])
        turnover = 1.0 if not prev else len(codes - prev) / HOLDINGS
        net = gross - turnover * (BUY_COST + SELL_COST)
        periods.append({"signal_date": str(dt.date()), "gross_return": gross,
                        "net_return_proxy": net, "turnover_fraction": turnover,
                        "holdings": HOLDINGS, "blocked_buy_cash_slots": blocked_buys,
                        "blocked_exit_mark_slots": blocked_exits})
        prev = codes
    if not periods:
        raise RuntimeError("No strict out-of-sample periods")
    perf = pd.DataFrame(periods)
    perf["equity"] = (1 + perf["net_return_proxy"]).cumprod()
    perf["running_max"] = perf["equity"].cummax()
    dd = perf["equity"] / perf["running_max"] - 1
    ret = perf["net_return_proxy"]
    metrics = {
        "mode": "RESEARCH_PROXY_NOT_LIVE_OR_PIT_VALIDATED",
        "oos_from": str(OOS_START.date()), "oos_periods": int(len(perf)),
        "factor_columns": FACTORS, "holdings_per_period": HOLDINGS,
        "net_cumulative_return_proxy": float(perf["equity"].iloc[-1] - 1),
        "annualized_return_proxy": float(perf["equity"].iloc[-1] ** (12 / len(perf)) - 1),
        "annualized_vol_proxy": float(ret.std(ddof=1) * np.sqrt(12)) if len(ret) > 1 else None,
        "annualized_sharpe_proxy": (float(ret.mean() / ret.std(ddof=1) * np.sqrt(12))
                                   if len(ret) > 1 and ret.std(ddof=1) > 0 else None),
        "max_drawdown_proxy": float(dd.min()),
        "monthly_hit_rate": float((ret > 0).mean()),
        "buy_cost_assumption": BUY_COST, "sell_cost_assumption": SELL_COST,
        "trade_limit_t_plus_one_and_minimum_lot_fully_simulated": False,
        "realistic_suspension_limit_price_fills_verified": False,
        "blocked_buy_cash_slots": blocked_buys_total,
        "blocked_exit_mark_slots": blocked_exits_total,
        "blocked_exit_capital_carry_verified": False,
        "methodology_warning": "Blocked buys are cash; blocked exits use stale marked close. Backtest ignores trapped-capital overlap; NOT an executable return.",
        "point_in_time_adjustment_and_fundamentals_verified": False,
    }
    perf.to_csv(OUT / "oos_equity_curve.csv", index=False)
    json_write(OUT / "oos_metrics.json", metrics)
    if latest_scores is not None:
        now_sh = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
        stale = (now_sh - latest_date).days > 3
        latest = latest_scores.head(HOLDINGS)[["code", "score"]].copy()
        latest["target_weight"] = 1 / HOLDINGS
        latest["signal_asof_date"] = str(latest_date.date())
        latest["action"] = "WATCHLIST_ONLY_NOT_EXECUTABLE"
        latest.to_csv(OUT / "paper_target_candidates.csv", index=False)
        json_write(OUT / "paper_status.json", {
            "status": "STALE_NO_TRADING" if stale else "RESEARCH_WATCHLIST_ONLY",
            "signal_asof_date": str(latest_date.date()),
            "asof_calendar_days_old": int((now_sh - latest_date).days),
            "cash_or_broker_orders_changed": False,
            "auto_execution": False,
            "require_independent_fresh_quote_and_human_review": True,
        })
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["validate", "research"], default="validate")
    args = parser.parse_args()
    permitted, gate, stats = audit_universe()
    print(json.dumps(stats, ensure_ascii=False), flush=True)
    if args.mode == "validate":
        return
    if gate.get("model_data_gate") != "PASS":
        json_write(OUT / "model_status.json", {
            "status": "BLOCKED_BY_REPAIR",
            "reason": gate.get("reason", "Missing quality gate"),
            "remaining_errors": gate.get("error_codes"),
            "broker_permitted_errors": stats["broker_permitted_error_count"],
        })
        raise RuntimeError("Cannot research with unresolved historical errors")
    if stats["broker_permitted_error_count"]:
        raise RuntimeError("Permitted universe still has missing symbols")
    panel, latest_date = prepare_panel(permitted)
    metrics = research(panel, latest_date)
    json_write(OUT / "model_status.json", {
        "status": "RESEARCH_COMPLETED_UNVERIFIED",
        "latest_data_date": str(latest_date.date()),
        "oos_periods": metrics["oos_periods"],
        "next_gates": ["point_in_time_adjustment", "daily_refresh",
                       "exchange_limit_rules", "T+1", "execution_liquidity",
                       "broker_fee_and_min_lot", "independent_benchmark"],
    })
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
