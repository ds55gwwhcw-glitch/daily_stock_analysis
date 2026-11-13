"""Conservative paper-account journal. NEVER routes real or broker orders.

Hypothetical open-price executions use raw (unadjusted, BaoStock adjustflag=3)
quotes obtained AFTER the date's close for orders decided the prior month end.
No claim of actual available liquidity, taxes, dividends, corporate actions or
real broker fill. If a quote is missing, no fill is invented.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path("quant_research")
STATE = ROOT / "state" / "paper_v1"
DAILY = ROOT / "state" / "daily_v1" / "daily_quality_gate.json"
WORKER = ROOT / "scripts" / "baostock_fetch_one.py"
ACCOUNT = STATE / "account.json"
TRADES = STATE / "virtual_trades.csv"
EQUITY = STATE / "virtual_equity.csv"
AUDIT = STATE / "virtual_account_status.json"
INITIAL_CNY = float(os.getenv("PAPER_INITIAL_CNY", "1000000"))
BUY_FEE = 0.0015
SELL_FEE = 0.002
TIMEOUT = 45
LOT = 100
# A deliberately conservative daily-volume participation cap for assumed fills.
MAX_VOLUME_PARTICIPATION = 0.01


def volume_capped_lot(q):
    """Maximum assumed executable shares; malformed volume fails closed."""
    try:
        volume = float(q["volume"])
    except (TypeError, ValueError, OverflowError, KeyError):
        return 0
    if not np.isfinite(volume) or volume <= 0:
        return 0
    return math.floor(volume * MAX_VOLUME_PARTICIPATION / LOT) * LOT


def store_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def permitted(code):
    if code.startswith("sh."):
        return code[3:6] in {"600", "601", "603", "605"}
    if code.startswith("sz."):
        return code[3:6] in {"000", "001", "002", "003", "300", "301", "302"}
    return False


def fetch_raw_quotes(codes, asof):
    quotes, errors = {}, {}
    start = (pd.Timestamp(asof) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    for code in sorted(set(codes)):
        if not permitted(code):
            errors[code] = "board_not_permitted"
            continue
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "raw.parquet"
            cmd = [sys.executable, str(WORKER), "--code", code, "--start", start,
                   "--end", asof, "--output", str(output), "--adjustflag", "3"]
            try:
                run = subprocess.run(cmd, capture_output=True, text=True,
                                     timeout=TIMEOUT, check=False)
                if run.returncode or not output.exists():
                    raise RuntimeError((run.stderr or run.stdout or "No raw output")[-240:])
                x = pd.read_parquet(output)
                if x.empty:
                    raise RuntimeError("Empty raw price history")
                x["date"] = pd.to_datetime(x["date"])
                x = x.sort_values("date")
                last = x.iloc[-1]
                q = {"last_quote_day": str(last["date"].date())}
                for f in ("open", "close", "preclose", "volume"):
                    q[f] = float(pd.to_numeric(last[f], errors="coerce"))
                q["tradestatus"] = str(last["tradestatus"])
                q["isST"] = str(last["isST"])
                quotes[code] = q
            except Exception as exc:
                errors[code] = str(exc)[:240]
    return quotes, errors


def eligible_open(code, q, asof, side):
    # Unknown actions must fail closed; never interpret them as sells.
    if side not in {"buy", "sell"} or not isinstance(code, str) or not permitted(code):
        return False
    if not isinstance(q, dict) or q.get("last_quote_day") != asof:
        return False
    # Missing, malformed or non-finite quotes must block assumed fills,
    # rather than crashing the account or treating a bad price as tradable.
    try:
        open_price = float(q.get("open"))
        prev = float(q.get("preclose"))
        volume = float(q.get("volume"))
        close_price = float(q.get("close"))
    except (TypeError, ValueError, OverflowError):
        return False
    if not (np.isfinite(open_price) and open_price > 0 and
            np.isfinite(prev) and prev > 0 and
            np.isfinite(volume) and volume > 0 and
            np.isfinite(close_price) and close_price > 0 and
            q.get("tradestatus") == "1"):
        return False
    # Unknown ST status changes the applicable price limit; fail closed.
    if q.get("isST") not in {"0", "1"}:
        return False
    is_st = q["isST"] == "1"
    limit = 0.05 if is_st else (0.20 if code.startswith("sz.30") else 0.10)
    # Very conservative approximate opening-price limit guard, not an exchange
    # matching-engine simulator; mainboard/ST rules and IPO days need validation.
    if side == "buy":
        if is_st:
            return False
        return open_price < prev * (1 + limit - 0.003)
    return open_price > prev * (1 - limit + 0.003)


def settled_for_sale(position, asof):
    """Fail closed for T+1: missing or same-day acquisition cannot be sold."""
    if not isinstance(position, dict):
        return False
    entry = position.get("entry_asof")
    try:
        return bool(entry) and pd.Timestamp(entry) < pd.Timestamp(asof)
    except (TypeError, ValueError):
        return False


def append_csv(path, rows, columns):
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=columns)
    merged = pd.concat([old, pd.DataFrame(rows, columns=columns)],
                       ignore_index=True)
    merged.to_csv(path, index=False)


def monthly_signal(date_string):
    d = pd.Timestamp(date_string)
    # Weekends cannot be exchange sessions; reject them even when the
    # weekday-based month-end approximation would otherwise trigger.
    if d.weekday() >= 5:
        return False
    # Conservative weekday approximation; special exchange holidays remain
    # unverified and may cause a missed (never backfilled) month-end signal.
    return (d + pd.offsets.BDay(1)).month != d.month


def process():
    STATE.mkdir(parents=True, exist_ok=True)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    gate = (json.loads(DAILY.read_text(encoding="utf-8"))
            if DAILY.exists() else {"status": "MISSING"})
    pubpath = STATE / "latest_status.json"
    published = (json.loads(pubpath.read_text(encoding="utf-8"))
                 if pubpath.exists() else {"status": "MISSING"})
    asof = gate.get("asof")
    if (gate.get("status") != "PASS_RESEARCH_REFRESH" or
            published.get("status") != "CANDIDATES_PUBLISHED_RESEARCH_ONLY" or
            not asof or published.get("signal_asof_date") != asof):
        store_json(AUDIT, {
            "status": "BLOCKED_BY_FRESHNESS_OR_SIGNAL",
            "asof": asof,
            "daily_gate": gate.get("status"),
            "signal_state": published.get("status"),
            "real_orders": False, "hypothetical_fills": False,
        })
        return

    target_file = STATE / "latest_target_candidates.csv"
    candidates = pd.read_csv(target_file, dtype={"code": str})
    targets = candidates["code"].astype(str).tolist()
    if (len(targets) != 10 or len(set(targets)) != 10
            or not all(permitted(c) for c in targets)):
        raise RuntimeError("Invalid candidate count/board eligibility")

    if ACCOUNT.exists():
        account = json.loads(ACCOUNT.read_text(encoding="utf-8"))
    else:
        if not np.isfinite(INITIAL_CNY) or INITIAL_CNY < 10000:
            raise RuntimeError("Invalid initial paper capital")
        account = {"initial_cash_cny": INITIAL_CNY, "cash_cny": INITIAL_CNY,
                   "positions": {}, "last_asof": None, "pending": None}

    if account.get("last_asof") == asof:
        print("Paper account day already processed", asof, flush=True)
        return
    if account.get("last_asof") and asof < account["last_asof"]:
        raise RuntimeError("Refusing to reverse paper-account chronology")

    # Persisted cash and holdings are untrusted; reject corrupt balances before
    # fetching quotes or generating any assumed fills.
    try:
        cash = float(account["cash_cny"])
        initial = float(account["initial_cash_cny"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Invalid persisted paper cash balance") from exc
    if not (np.isfinite(cash) and cash >= 0 and
            np.isfinite(initial) and initial >= 10000):
        raise RuntimeError("Invalid persisted paper cash balance")
    if not isinstance(account.get("positions"), dict):
        raise RuntimeError("Invalid persisted paper positions")
    for code, position in account["positions"].items():
        if not permitted(code) or not isinstance(position, dict):
            raise RuntimeError("Invalid persisted paper position")
        shares = position.get("shares")
        if (isinstance(shares, bool) or not isinstance(shares, int) or
                shares <= 0 or shares % LOT != 0):
            raise RuntimeError("Invalid persisted paper position shares")
        try:
            mark = float(position["last_close"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("Invalid persisted paper position mark") from exc
        if not np.isfinite(mark) or mark <= 0:
            raise RuntimeError("Invalid persisted paper position mark")

    positions = account["positions"]
    pending = account.get("pending")
    # Persisted pending orders are untrusted state. Corrupt targets must not fill.
    if pending is not None:
        pending_codes = pending.get("codes") if isinstance(pending, dict) else None
        signal_date = pending.get("signal_asof") if isinstance(pending, dict) else None
        try:
            valid_signal_date = (bool(signal_date) and
                                 pd.Timestamp(signal_date) <= pd.Timestamp(asof))
        except (TypeError, ValueError, OverflowError):
            valid_signal_date = False
        if (not isinstance(pending_codes, list) or len(pending_codes) != 10 or
                len(set(pending_codes)) != 10 or
                not all(isinstance(c, str) and permitted(c) for c in pending_codes) or
                not valid_signal_date or not monthly_signal(signal_date)):
            raise RuntimeError("Invalid persisted pending target; refusing paper execution")
    needed = set(positions)
    if pending is not None:
        needed.update(pending.get("codes", []))
    raw_quotes, quote_errors = fetch_raw_quotes(needed, asof)
    trades, blocked, stale_marks = [], [], []

    # Expire a stale target before attempting any assumed fill.
    if pending and (pd.Timestamp(asof) - pd.Timestamp(pending["signal_asof"])).days > 7:
        blocked.append({"action": "rebalance", "reason": "stale pending signal >7 calendar days"})
        pending = None

    # Pending targets are decided at an earlier month-end close. Never
    # execute a signal at an open which occurred before that signal.
    if pending and asof > pending["signal_asof"]:
        desired = set(pending["codes"])
        nav_open = float(account["cash_cny"])
        for code, pos in positions.items():
            quote = raw_quotes.get(code)
            price = quote.get("open") if quote and quote.get("last_quote_day") == asof else None
            if price is None or not np.isfinite(price) or price <= 0:
                price = float(pos.get("last_close", 0.0))
            nav_open += int(pos["shares"]) * price

        for code in list(positions):
            if code in desired:
                continue
            # A-share T+1: shares acquired today cannot be sold today.
            # Unknown entry dates are not assumed to be settled.
            if not settled_for_sale(positions[code], asof):
                blocked.append({"code": code, "action": "sell",
                                "reason": "T+1/unknown acquisition date"})
                continue
            quote = raw_quotes.get(code)
            if not eligible_open(code, quote, asof, "sell"):
                blocked.append({"code": code, "action": "sell", "reason": "suspension/limit/no raw quote"})
                continue
            shares = int(positions[code]["shares"])
            if shares > volume_capped_lot(quote):
                blocked.append({"code": code, "action": "sell",
                                "reason": "position exceeds 1% daily-volume cap; partial sale not modeled"})
                continue
            gross = shares * quote["open"]
            fee = gross * SELL_FEE
            account["cash_cny"] += gross - fee
            trades.append({"asof": asof, "code": code, "side": "SELL",
                           "shares": shares, "raw_open": quote["open"],
                           "gross_cny": gross, "fee_cny": fee,
                           "status": "VIRTUAL_ASSUMED_NOT_BROKER_FILL"})
            del positions[code]

        for code in pending["codes"]:
            if code in positions:
                continue
            quote = raw_quotes.get(code)
            if not eligible_open(code, quote, asof, "buy"):
                blocked.append({"code": code, "action": "buy", "reason": "ST/suspension/limit/no raw quote"})
                continue
            budget = min(account["cash_cny"], nav_open * 0.1)
            shares = math.floor(budget / (quote["open"] * (1 + BUY_FEE)) / LOT) * LOT
            shares = min(shares, volume_capped_lot(quote))
            if shares < LOT:
                blocked.append({"code": code, "action": "buy", "reason": "no 100-share lot funding"})
                continue
            gross = shares * quote["open"]
            fee = gross * BUY_FEE
            account["cash_cny"] -= gross + fee
            positions[code] = {"shares": shares, "entry_asof": asof,
                               "last_close": quote["close"],
                               "last_quote_day": asof}
            trades.append({"asof": asof, "code": code, "side": "BUY",
                           "shares": shares, "raw_open": quote["open"],
                           "gross_cny": gross, "fee_cny": fee,
                           "status": "VIRTUAL_ASSUMED_NOT_BROKER_FILL"})
        completed = (set(positions) == desired)
        expired = (pd.Timestamp(asof) - pd.Timestamp(pending["signal_asof"])).days > 7
        if completed or expired:
            pending = None

    nav_close = float(account["cash_cny"])
    for code, pos in positions.items():
        q = raw_quotes.get(code)
        if q and q.get("last_quote_day") == asof and np.isfinite(q["close"]) and q["close"] > 0:
            pos["last_close"] = q["close"]
            pos["last_quote_day"] = asof
        else:
            stale_marks.append(code)
        nav_close += int(pos["shares"]) * float(pos.get("last_close", 0.0))

    # New month-end target is only queued, not filled on the same day.
    if monthly_signal(asof):
        pending = {"signal_asof": asof, "codes": targets}

    account["pending"] = pending
    account["positions"] = positions
    account["last_asof"] = asof
    account["last_equity_cny"] = round(nav_close, 2)
    store_json(ACCOUNT, account)
    if trades:
        append_csv(TRADES, trades, ["asof", "code", "side", "shares",
                                   "raw_open", "gross_cny", "fee_cny", "status"])
    append_csv(EQUITY, [{
        "asof": asof, "cash_cny": round(account["cash_cny"], 2),
        "equity_cny": round(nav_close, 2), "position_count": len(positions),
        "hypothetical_fills": len(trades), "blocked_orders": len(blocked),
        "stale_price_marks": len(stale_marks),
        "status": "VIRTUAL_RESEARCH_LEDGER_NOT_LIVE",
    }], ["asof", "cash_cny", "equity_cny", "position_count",
         "hypothetical_fills", "blocked_orders", "stale_price_marks", "status"])
    store_json(AUDIT, {
        "status": "VIRTUAL_RESEARCH_LEDGER_NOT_LIVE", "asof": asof,
        "initial_cash_cny": account["initial_cash_cny"],
        "equity_cny": round(nav_close, 2),
        "cash_cny": round(account["cash_cny"], 2),
        "position_count": len(positions),
        "virtual_fills_today": len(trades),
        "blocked_orders": blocked, "stale_price_marks": stale_marks,
        "quote_errors": quote_errors,
        "next_monthly_target_pending": bool(pending),
        "real_broker_orders": 0, "real_account_connected": False,
        "raw_price_corporate_actions_and_execution_verified": False,
        "limit_guard_is_conservative_approximation": True,
        "warning": "Virtual price-based ledger only, not tradable PnL or real fills.",
    })
    print(json.dumps({"asof": asof, "equity_cny": round(nav_close, 2),
                      "positions": len(positions), "virtual_fills": len(trades),
                      "blocked": len(blocked)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    process()
