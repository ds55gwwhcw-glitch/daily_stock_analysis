"""No-broker virtual account safeguards, offline deterministic tests."""
import importlib.util
import json
from pathlib import Path

import pandas as pd

PATH = Path(__file__).resolve().parents[1] / "scripts" / "paper_shadow_account_v1.py"
spec = importlib.util.spec_from_file_location("paper_shadow", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_limit_guards_and_monthly_frequency():
    assert mod.permitted("sh.600000")
    assert mod.permitted("sz.300750")
    assert not mod.permitted("sh.688001")
    assert not mod.permitted("bj.920001")
    assert mod.monthly_signal("2026-11-30")
    assert not mod.monthly_signal("2026-11-05")
    quote = {"last_quote_day": "2026-11-02", "open": 10.1,
             "close": 10.2, "preclose": 10.0, "volume": 10000,
             "tradestatus": "1", "isST": "0"}
    assert mod.eligible_open("sh.600000", quote, "2026-11-02", "buy")
    assert not mod.eligible_open("sh.600000", {**quote, "open": 11.0},
                                 "2026-11-02", "buy")
    assert not mod.eligible_open("sh.600000", {**quote, "isST": "1"},
                                 "2026-11-02", "buy")
    assert not mod.eligible_open("sh.600000", quote, "2026-11-03", "buy")


def test_first_paper_day_only_initializes_cash(tmp_path, monkeypatch):
    state = tmp_path / "paper"
    state.mkdir()
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"status": "PASS_RESEARCH_REFRESH",
                                 "asof": "2026-11-05"}))
    (state / "latest_status.json").write_text(json.dumps({
        "status": "CANDIDATES_PUBLISHED_RESEARCH_ONLY",
        "signal_asof_date": "2026-11-05"}))
    codes = [f"sz.{x:06d}" for x in range(300001, 300011)]
    pd.DataFrame({"code": codes}).to_csv(
        state / "latest_target_candidates.csv", index=False)
    monkeypatch.setattr(mod, "STATE", state)
    monkeypatch.setattr(mod, "DAILY", daily)
    monkeypatch.setattr(mod, "ACCOUNT", state / "account.json")
    monkeypatch.setattr(mod, "TRADES", state / "virtual_trades.csv")
    monkeypatch.setattr(mod, "EQUITY", state / "virtual_equity.csv")
    monkeypatch.setattr(mod, "AUDIT", state / "virtual_account_status.json")
    monkeypatch.setattr(mod, "fetch_raw_quotes", lambda codes, asof: ({}, {}))
    mod.process()
    obj = json.loads((state / "account.json").read_text())
    assert obj["cash_cny"] == 1_000_000
    assert obj["positions"] == {}
    assert not (state / "virtual_trades.csv").exists()
    assert not json.loads((state / "virtual_account_status.json").read_text())["real_broker_orders"]


def test_prev_month_signal_filled_only_next_session(tmp_path, monkeypatch):
    state = tmp_path / "paper"
    state.mkdir()
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"status": "PASS_RESEARCH_REFRESH",
                                 "asof": "2026-11-02"}))
    (state / "latest_status.json").write_text(json.dumps({
        "status": "CANDIDATES_PUBLISHED_RESEARCH_ONLY",
        "signal_asof_date": "2026-11-02"}))
    codes = [f"sz.{x:06d}" for x in range(300001, 300011)]
    pd.DataFrame({"code": codes}).to_csv(state / "latest_target_candidates.csv", index=False)
    (state / "account.json").write_text(json.dumps({
        "initial_cash_cny": 1_000_000, "cash_cny": 1_000_000,
        "positions": {}, "last_asof": "2026-10-30",
        "pending": {"signal_asof": "2026-10-30", "codes": codes}
    }))
    monkeypatch.setattr(mod, "STATE", state)
    monkeypatch.setattr(mod, "DAILY", daily)
    monkeypatch.setattr(mod, "ACCOUNT", state / "account.json")
    monkeypatch.setattr(mod, "TRADES", state / "virtual_trades.csv")
    monkeypatch.setattr(mod, "EQUITY", state / "virtual_equity.csv")
    monkeypatch.setattr(mod, "AUDIT", state / "virtual_account_status.json")
    q = {"last_quote_day": "2026-11-02", "open": 10, "close": 10,
         "preclose": 10, "volume": 10000, "tradestatus": "1", "isST": "0"}
    monkeypatch.setattr(mod, "fetch_raw_quotes",
                        lambda req, asof: ({x: q for x in req}, {}))
    mod.process()
    account = json.loads((state / "account.json").read_text())
    assert len(account["positions"]) == 10
    assert account["pending"] is None
    trades = pd.read_csv(state / "virtual_trades.csv")
    assert len(trades) == 10
    assert set(trades["side"]) == {"BUY"}
    assert all(trades["shares"] % 100 == 0)
    assert all(trades["status"] == "VIRTUAL_ASSUMED_NOT_BROKER_FILL")


def test_non_month_end_candidate_does_not_create_trade(tmp_path, monkeypatch):
    """Daily research candidates must not trigger an unscheduled rebalance."""
    state = tmp_path / "paper"
    state.mkdir()
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"status": "PASS_RESEARCH_REFRESH",
                                 "asof": "2026-11-10"}))
    (state / "latest_status.json").write_text(json.dumps({
        "status": "CANDIDATES_PUBLISHED_RESEARCH_ONLY",
        "signal_asof_date": "2026-11-10"}))
    codes = [f"sz.{x:06d}" for x in range(300001, 300011)]
    pd.DataFrame({"code": codes}).to_csv(
        state / "latest_target_candidates.csv", index=False)
    monkeypatch.setattr(mod, "STATE", state)
    monkeypatch.setattr(mod, "DAILY", daily)
    monkeypatch.setattr(mod, "ACCOUNT", state / "account.json")
    monkeypatch.setattr(mod, "TRADES", state / "virtual_trades.csv")
    monkeypatch.setattr(mod, "EQUITY", state / "virtual_equity.csv")
    monkeypatch.setattr(mod, "AUDIT", state / "virtual_account_status.json")
    monkeypatch.setattr(mod, "fetch_raw_quotes", lambda codes, asof: ({}, {}))

    mod.process()
    first = json.loads((state / "account.json").read_text())
    assert first["pending"] is None
    assert first["positions"] == {}
    assert first["cash_cny"] == 1_000_000
    assert not (state / "virtual_trades.csv").exists()
    assert len(pd.read_csv(state / "virtual_equity.csv")) == 1

    # Re-running the same date must not append duplicate account entries.
    mod.process()
    assert len(pd.read_csv(state / "virtual_equity.csv")) == 1


def test_blocked_daily_gate_never_creates_orders(tmp_path, monkeypatch):
    """Research publication cannot bypass a blocked daily quality gate."""
    state = tmp_path / "paper"
    state.mkdir()
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"status": "BLOCKED",
                                 "asof": "2026-11-10"}))
    (state / "latest_status.json").write_text(json.dumps({
        "status": "CANDIDATES_PUBLISHED_RESEARCH_ONLY",
        "signal_asof_date": "2026-11-10"}))
    monkeypatch.setattr(mod, "STATE", state)
    monkeypatch.setattr(mod, "DAILY", daily)
    monkeypatch.setattr(mod, "ACCOUNT", state / "account.json")
    monkeypatch.setattr(mod, "TRADES", state / "virtual_trades.csv")
    monkeypatch.setattr(mod, "EQUITY", state / "virtual_equity.csv")
    monkeypatch.setattr(mod, "AUDIT", state / "virtual_account_status.json")

    mod.process()
    audit = json.loads((state / "virtual_account_status.json").read_text())
    assert audit["status"] == "BLOCKED_BY_FRESHNESS_OR_SIGNAL"
    assert audit["hypothetical_fills"] is False
    assert audit["real_orders"] is False
    assert not (state / "account.json").exists()
    assert not (state / "virtual_trades.csv").exists()


def test_pending_month_end_signal_not_filled_on_signal_day(tmp_path, monkeypatch):
    """An existing month-end target cannot fill at that same day's open."""
    state = tmp_path / "paper"
    state.mkdir()
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"status": "PASS_RESEARCH_REFRESH",
                                 "asof": "2026-11-30"}))
    (state / "latest_status.json").write_text(json.dumps({
        "status": "CANDIDATES_PUBLISHED_RESEARCH_ONLY",
        "signal_asof_date": "2026-11-30"}))
    codes = [f"sz.{x:06d}" for x in range(300001, 300011)]
    pd.DataFrame({"code": codes}).to_csv(
        state / "latest_target_candidates.csv", index=False)
    (state / "account.json").write_text(json.dumps({
        "initial_cash_cny": 1_000_000, "cash_cny": 1_000_000,
        "positions": {}, "last_asof": "2026-11-27",
        "pending": None
    }))
    monkeypatch.setattr(mod, "STATE", state)
    monkeypatch.setattr(mod, "DAILY", daily)
    monkeypatch.setattr(mod, "ACCOUNT", state / "account.json")
    monkeypatch.setattr(mod, "TRADES", state / "virtual_trades.csv")
    monkeypatch.setattr(mod, "EQUITY", state / "virtual_equity.csv")
    monkeypatch.setattr(mod, "AUDIT", state / "virtual_account_status.json")
    monkeypatch.setattr(mod, "fetch_raw_quotes", lambda codes, asof: ({}, {}))
    mod.process()
    account = json.loads((state / "account.json").read_text())
    assert account["pending"] == {
        "signal_asof": "2026-11-30", "codes": codes
    }
    assert account["positions"] == {}
    assert account["cash_cny"] == 1_000_000
    assert not (state / "virtual_trades.csv").exists()


def test_t_plus_one_blocks_same_day_or_unknown_entry():
    """Exercise the actual sale guard rather than a tautological date assertion."""
    asof = "2026-11-12"
    assert mod.settled_for_sale({"entry_asof": "2026-11-11"}, asof)
    for position in (
        {"entry_asof": "2026-11-12"},
        {"entry_asof": "2026-11-13"},
        {"entry_asof": None},
        {},
        {"entry_asof": "invalid"},
        None,
    ):
        assert not mod.settled_for_sale(position, asof)


def test_malformed_raw_quotes_fail_closed_without_crashing():
    """No virtual fill is allowed from absent or malformed price fields."""
    asof = "2026-11-12"
    valid = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
             "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    for bad in (
        None, {}, {**valid, "close": None}, {**valid, "close": float("nan")},
        {**valid, "open": None}, {**valid, "open": "bad"},
        {**valid, "open": float("nan")}, {**valid, "preclose": None},
        {**valid, "volume": None}, {**valid, "volume": "unknown"},
        {**valid, "volume": float("inf")}, {**valid, "volume": 0},
        {**valid, "tradestatus": "0"},
    ):
        assert not mod.eligible_open("sh.600000", bad, asof, "buy")
        assert not mod.eligible_open("sh.600000", bad, asof, "sell")
    assert mod.eligible_open("sh.600000", valid, asof, "buy")


def test_unknown_order_side_fails_closed():
    asof = "2026-11-12"
    quote = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
             "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    assert mod.eligible_open("sh.600000", quote, asof, "buy")
    assert mod.eligible_open("sh.600000", quote, asof, "sell")
    for side in ("BUY", "SELL", "hold", "", None):
        assert not mod.eligible_open("sh.600000", quote, asof, side)


def test_stale_pending_target_expires_before_any_fill(tmp_path, monkeypatch):
    """A month-end target older than seven days must not trade after an outage."""
    state = tmp_path / "paper"
    state.mkdir()
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"status": "PASS_RESEARCH_REFRESH",
                                 "asof": "2026-12-10"}))
    (state / "latest_status.json").write_text(json.dumps({
        "status": "CANDIDATES_PUBLISHED_RESEARCH_ONLY",
        "signal_asof_date": "2026-12-10"}))
    codes = [f"sz.{x:06d}" for x in range(300001, 300011)]
    pd.DataFrame({"code": codes}).to_csv(
        state / "latest_target_candidates.csv", index=False)
    (state / "account.json").write_text(json.dumps({
        "initial_cash_cny": 1_000_000, "cash_cny": 1_000_000,
        "positions": {}, "last_asof": "2026-11-30",
        "pending": {"signal_asof": "2026-11-30", "codes": codes}
    }))
    monkeypatch.setattr(mod, "STATE", state)
    monkeypatch.setattr(mod, "DAILY", daily)
    monkeypatch.setattr(mod, "ACCOUNT", state / "account.json")
    monkeypatch.setattr(mod, "TRADES", state / "virtual_trades.csv")
    monkeypatch.setattr(mod, "EQUITY", state / "virtual_equity.csv")
    monkeypatch.setattr(mod, "AUDIT", state / "virtual_account_status.json")
    quote = {"last_quote_day": "2026-12-10", "open": 10,
             "close": 10, "preclose": 10, "volume": 10000,
             "tradestatus": "1", "isST": "0"}
    monkeypatch.setattr(mod, "fetch_raw_quotes",
                        lambda req, asof: ({code: quote for code in req}, {}))
    mod.process()
    account = json.loads((state / "account.json").read_text())
    audit = json.loads((state / "virtual_account_status.json").read_text())
    assert account["pending"] is None
    assert account["positions"] == {}
    assert account["cash_cny"] == 1_000_000
    assert not (state / "virtual_trades.csv").exists()
    assert audit["virtual_fills_today"] == 0
    assert any("stale pending signal" in x["reason"]
               for x in audit["blocked_orders"])
