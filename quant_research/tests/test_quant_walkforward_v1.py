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
