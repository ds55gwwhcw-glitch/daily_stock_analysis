"""Deterministic research guardrails with no network dependency."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "quant_walkforward_v1.py"
spec = importlib.util.spec_from_file_location("quant_walkforward", MODULE_PATH)
quant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quant)


def test_broker_prefix_allowlist():
    for code in ["sh.600000", "sh.601888", "sh.603000", "sh.605001",
                 "sz.000001", "sz.001234", "sz.002594", "sz.003005",
                 "sz.300750", "sz.301388", "sz.302132"]:
        assert quant.tradable_code(code), code
    for code in ["sh.688001", "sh.689009", "bj.920001", "bj.430001",
                 "sz.200001", "sh.900901", "sh.510300", "sz.159915"]:
        assert not quant.tradable_code(code), code


def test_ridge_learns_expected_sign_without_future_observations():
    rng = np.random.default_rng(2026)
    X = rng.normal(size=(200, len(quant.FACTORS)))
    y = X @ np.array([0.06, 0.02, -0.02, 0.05, 0.01]) + rng.normal(0, 0.02, 200)
    data = pd.DataFrame(X, columns=quant.FACTORS)
    data["forward_return_proxy"] = y
    intercept, beta = quant.ridge(data)
    assert np.isfinite(intercept) and np.isfinite(beta).all()
    assert beta[0] > 0 and beta[2] < 0


def test_oos_and_cost_guardrails():
    assert quant.OOS_START == pd.Timestamp("2023-01-01")
    assert quant.HOLDINGS == 10
    assert quant.BUY_COST > 0 and quant.SELL_COST > 0



def test_suspended_exit_is_marked_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(quant, "CACHE", tmp_path)
    dates = pd.bdate_range("2023-01-02", periods=360)
    prices = np.linspace(10.0, 20.0, len(dates))
    data = pd.DataFrame({
        "date": dates,
        "open": prices * 0.995,
        "close": prices,
        "amount": 100000.0,
        "volume": 10000.0,
        "tradestatus": "1",
        "isST": "0",
    })
    data.loc[301, "tradestatus"] = "0"
    data.loc[301, "open"] = np.nan
    data.to_parquet(tmp_path / "sh_600000.parquet", index=False)
    signals = pd.DatetimeIndex([dates[260]])
    entries = pd.DatetimeIndex([dates[261]])
    exits = pd.DatetimeIndex([dates[301]])
    part = quant.extract_stock(
        "sh.600000", "2000-01-01", "", signals, entries, exits)
    assert len(part) == 1
    assert bool(part["executed_buy_proxy"].iloc[0])
    assert bool(part["blocked_exit_mark_proxy"].iloc[0])
    assert np.isfinite(part["forward_return_proxy"].iloc[0])


def test_unfillable_buy_is_cash_not_survivorship_drop(tmp_path, monkeypatch):
    monkeypatch.setattr(quant, "CACHE", tmp_path)
    dates = pd.bdate_range("2023-01-02", periods=360)
    prices = np.linspace(10.0, 20.0, len(dates))
    data = pd.DataFrame({
        "date": dates,
        "open": prices,
        "close": prices,
        "amount": 100000.0,
        "volume": 10000.0,
        "tradestatus": "1",
        "isST": "0",
    })
    data.loc[261, "tradestatus"] = "0"
    data.loc[261, "open"] = np.nan
    data.to_parquet(tmp_path / "sh_600000.parquet", index=False)
    part = quant.extract_stock(
        "sh.600000", "2000-01-01", "",
        pd.DatetimeIndex([dates[260]]),
        pd.DatetimeIndex([dates[261]]),
        pd.DatetimeIndex([dates[301]]))
    assert len(part) == 1
    assert bool(part["blocked_buy_proxy"].iloc[0])
    assert part["forward_return_proxy"].iloc[0] == 0.0
