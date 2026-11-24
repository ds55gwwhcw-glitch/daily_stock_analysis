"""Additional fail-closed paper liquidity safeguards (no brokerage orders)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "paper_shadow_volume", ROOT / "scripts" / "paper_shadow_account_v1.py"
)
paper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paper)


def test_volume_cap_is_one_percent_rounded_to_a_share_lots():
    assert paper.volume_capped_lot({"volume": 1_000_000}) == 10_000
    assert paper.volume_capped_lot({"volume": 19_999}) == 100
    assert paper.volume_capped_lot({"volume": 9_999}) == 0
    assert paper.volume_capped_lot({"volume": 0}) == 0


def test_volume_cap_rejects_missing_or_malformed_volume():
    for value in (None, "", "bad", -100, float("nan"), float("inf"),
                  float("-inf"), 0):
        assert paper.volume_capped_lot({"volume": value}) == 0
    assert paper.volume_capped_lot({}) == 0


def test_no_assumed_execution_without_minimum_liquidity():
    date = "2026-11-12"
    q = {"last_quote_day": date, "open": 10.0, "close": 10.0,
         "preclose": 10.0, "volume": 9_999,
         "tradestatus": "1", "isST": "0"}
    # A valid price is not enough to establish a minimum 100-share fill.
    assert paper.eligible_open("sh.600000", q, date, "buy")
    assert paper.volume_capped_lot(q) == 0
