"""Deterministic daily freshness regression tests; no network and no real trades."""
import importlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
daily = importlib.import_module("daily_incremental_v1")


def test_completed_market_asof_pinned_and_future_bar_blocks_gate(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    hist.mkdir()
    day = tmp_path / "daily"
    day.mkdir()
    (hist / "progress.json").write_text(
        json.dumps({"completed": True, "cursor": 5475, "universe_size": 5475}))
    (hist / "repair_quality_gate.json").write_text(
        json.dumps({"model_data_gate": "PASS"}))
    pd.DataFrame([
        {"code": "sh.600000", "status": "1", "ipoDate": "1999-11-10",
         "outDate": "", "code_name": "Test Equity"},
    ]).to_csv(hist / "universe_shsz_frozen.csv", index=False)
    monkeypatch.setattr(daily, "STATE", day)
    monkeypatch.setattr(daily, "MANIFEST", day / "daily_manifest.csv")
    monkeypatch.setattr(daily, "PROGRESS", day / "progress.json")
    monkeypatch.setattr(daily, "GATE", day / "daily_quality_gate.json")
    monkeypatch.setattr(daily, "local_asof", lambda: "2026-11-04")
    monkeypatch.setattr(daily.base, "PROGRESS_PATH", hist / "progress.json")
    monkeypatch.setattr(daily.base, "STATE_DIR", hist)
    monkeypatch.setattr(daily.base, "UNIVERSE_PATH",
                        hist / "universe_shsz_frozen.csv")
    monkeypatch.setattr(daily.base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(daily, "latest_anchor_date", lambda: "2026-11-04")
    def fake_collect(_):
        assert daily.base.END_DATE == "2026-11-04"
        return {"result": "ok", "max_date": "2026-11-05", "error": ""}
    monkeypatch.setattr(daily.base, "collect_one", fake_collect)
    daily.main()
    audit = json.loads((day / "daily_quality_gate.json").read_text())
    assert audit["status"] == "BLOCKED"
    assert audit["error_codes"] == 1
    assert audit["terminal"]
    manifest = pd.read_csv(day / "daily_manifest.csv")
    assert manifest.iloc[0]["result"] == "error"
    assert "future_date_in_cache" in manifest.iloc[0]["error"]


def test_weekend_asof_picks_previous_friday(monkeypatch):
    class FrozenDate:
        @classmethod
        def now(cls, tz):
            # Saturday in Shanghai before any new session.
            from datetime import datetime
            return datetime(2026, 11, 7, 9, 0, tzinfo=tz)
    monkeypatch.setattr(daily, "datetime", FrozenDate)
    assert daily.local_asof() == "2026-11-06"
