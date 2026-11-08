"""Offline tests for the read-only hypothetical performance report."""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "paper_performance_report_v1.py"
spec = importlib.util.spec_from_file_location("paper_performance_report", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_report_no_fills_and_drawdown(tmp_path):
    (tmp_path / "virtual_account_status.json").write_text(json.dumps({
        "real_account_connected": False, "real_broker_orders": 0,
        "initial_cash_cny": 1000000, "equity_cny": 990000,
        "position_count": 2, "asof": "2026-11-10"
    }))
    pd.DataFrame([
        {"asof": "2026-11-09", "equity_cny": 1010000},
        {"asof": "2026-11-10", "equity_cny": 990000},
    ]).to_csv(tmp_path / "virtual_equity.csv", index=False)
    report = mod.build_report(tmp_path)
    assert report["total_return_pct"] == -1.0
    assert report["max_drawdown_pct"] == round((990000 / 1010000 - 1) * 100, 4)
    assert report["hypothetical_trade_rows"] == 0
    assert report["real_broker_orders"] == 0


def test_refuse_connected_broker(tmp_path):
    (tmp_path / "virtual_account_status.json").write_text(json.dumps({
        "real_account_connected": True, "real_broker_orders": 0,
        "initial_cash_cny": 1000000, "equity_cny": 1000000,
        "position_count": 0
    }))
    with pytest.raises(ValueError, match="Broker isolation"):
        mod.build_report(tmp_path)
