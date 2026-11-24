from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_corporate_action_guard_v1 import corporate_action_review

POSITION = {"last_close": 10, "last_quote_day": "2026-11-20"}
QUOTE = {"preclose": 10, "last_quote_day": "2026-11-23"}
EVIDENCE = {"start": "2026-11-20", "end": "2026-11-23", "verified": True, "events": []}


def test_verified_no_action_interval():
    assert corporate_action_review(POSITION, QUOTE, "2026-11-23", EVIDENCE) is None


def test_missing_or_unresolved_actions_block():
    for evidence in (None, {}, {**EVIDENCE, "verified": False},
                     {**EVIDENCE, "events": [{"kind": "dividend"}]},
                     {**EVIDENCE, "start": "2026-11-21"}):
        assert corporate_action_review(POSITION, QUOTE, "2026-11-23", evidence)


def test_price_discontinuity_and_stale_data_block():
    for quote in ({**QUOTE, "preclose": 9.5}, {**QUOTE, "preclose": "nan"},
                  {**QUOTE, "last_quote_day": "2026-11-20"}):
        assert corporate_action_review(POSITION, quote, "2026-11-23", EVIDENCE)
