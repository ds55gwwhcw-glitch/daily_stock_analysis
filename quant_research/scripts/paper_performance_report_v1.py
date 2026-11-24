"""Read-only paper-account performance report. No order routing or broker access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path("quant_research/state/paper_v1")


def build_report(root: Path) -> dict:
    status_path = root / "virtual_account_status.json"
    if not status_path.exists():
        raise FileNotFoundError(status_path)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("real_account_connected") is not False or status.get("real_broker_orders") != 0:
        raise ValueError("Broker isolation not confirmed; refusing performance report")
    initial = float(status["initial_cash_cny"])
    if initial <= 0:
        raise ValueError("Invalid initial capital")
    equity_path = root / "virtual_equity.csv"
    trades_path = root / "virtual_trades.csv"
    equity = pd.read_csv(equity_path) if equity_path.exists() else pd.DataFrame()
    trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
    if not equity.empty:
        equity = equity.sort_values("asof").drop_duplicates("asof", keep="last")
        values = pd.to_numeric(equity["equity_cny"], errors="raise")
        if values.isna().any() or (values <= 0).any():
            raise ValueError("Invalid equity history")
        max_drawdown = float((values / values.cummax() - 1).min())
        latest_equity = float(values.iloc[-1])
        latest_date = str(equity.iloc[-1]["asof"])
    else:
        max_drawdown = None
        latest_equity = float(status["equity_cny"])
        latest_date = status.get("asof")
    if abs(latest_equity - float(status["equity_cny"])) > 0.011:
        raise ValueError("Ledger and status equity disagree")
    return {
        "status": "HYPOTHETICAL_PAPER_ONLY_NOT_BROKER_VALIDATED",
        "asof": latest_date,
        "initial_cash_cny": initial,
        "equity_cny": round(latest_equity, 2),
        "total_return_pct": round((latest_equity / initial - 1) * 100, 4),
        "max_drawdown_pct": None if max_drawdown is None else round(max_drawdown * 100, 4),
        "equity_observations": int(len(equity)),
        "hypothetical_trade_rows": int(len(trades)),
        "position_count": int(status["position_count"]),
        "benchmark_comparison": "NOT_VERIFIED",
        "corporate_actions_and_execution": "NOT_VERIFIED",
        "real_broker_orders": 0,
        "warning": "Research-only virtual ledger; returns are not executable or real profits.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "performance_report.json")
    args = parser.parse_args()
    result = build_report(args.state_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
