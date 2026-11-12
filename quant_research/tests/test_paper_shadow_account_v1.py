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


def test_unknown_st_and_nonpositive_close_fail_closed():
    """Unknown price-limit classification or bad closing marks prohibit fills."""
    asof = "2026-11-12"
    valid = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
             "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    for changes in (
        {"isST": None}, {"isST": "unknown"}, {"isST": 0},
        {"close": 0}, {"close": -1}, {"preclose": 0}, {"open": -1},
    ):
        quote = {**valid, **changes}
        assert not mod.eligible_open("sh.600000", quote, asof, "buy")
        assert not mod.eligible_open("sh.600000", quote, asof, "sell")


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


def test_board_limit_and_suspension_guards_block_assumed_fills():
    """Quote checks are only conservative guards, not exchange fill proof."""
    asof = "2026-11-12"
    q = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
         "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    # Mainboard 10% opening limit: refuse to assume a buy at the upper limit.
    assert not mod.eligible_open("sh.600000", {**q, "open": 11.0}, asof, "buy")
    # ChiNext 20% limit: a 15% move remains inside the approximate guard.
    assert mod.eligible_open("sz.300750", {**q, "open": 11.5}, asof, "buy")
    assert not mod.eligible_open("sz.300750", {**q, "open": 12.0}, asof, "buy")
    assert not mod.eligible_open("sz.300750", {**q, "open": 8.0}, asof, "sell")
    # No trading when suspended, without volume, or on an outdated quote.
    for bad in (
        {**q, "tradestatus": "0"},
        {**q, "volume": 0},
        {**q, "last_quote_day": "2026-11-11"},
    ):
        assert not mod.eligible_open("sh.600000", bad, asof, "buy")
        assert not mod.eligible_open("sh.600000", bad, asof, "sell")


def test_unsupported_board_never_eligible_for_assumed_execution():
    asof = "2026-11-12"
    quote = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
             "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    for code in ("sh.688001", "bj.920001", "sz.200001", "", None):
        assert not mod.eligible_open(code, quote, asof, "buy")
        assert not mod.eligible_open(code, quote, asof, "sell")


def test_mismatched_signal_date_blocks_all_paper_activity(tmp_path, monkeypatch):
    """A passing data gate does not authorize yesterday's candidate snapshot."""
    state = tmp_path / "paper"
    state.mkdir()
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"status": "PASS_RESEARCH_REFRESH",
                                 "asof": "2026-11-11"}))
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
    assert audit["asof"] == "2026-11-11"
    assert audit["signal_state"] == "CANDIDATES_PUBLISHED_RESEARCH_ONLY"
    assert audit["hypothetical_fills"] is False
    assert not (state / "account.json").exists()
    assert not (state / "virtual_trades.csv").exists()
    assert not (state / "virtual_equity.csv").exists()


def test_unknown_st_status_blocks_assumed_fills():
    """Never apply a normal price limit when ST status is unknown."""
    asof = "2026-11-12"
    q = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
         "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    assert mod.eligible_open("sh.600000", q, asof, "buy")
    for unknown in (None, "", "unknown", "2"):
        quote = {**q, "isST": unknown}
        assert not mod.eligible_open("sh.600000", quote, asof, "buy")
        assert not mod.eligible_open("sh.600000", quote, asof, "sell")
    missing = {k: v for k, v in q.items() if k != "isST"}
    assert not mod.eligible_open("sh.600000", missing, asof, "buy")


def test_quote_day_and_trading_status_fail_closed_for_both_sides():
    """No assumed fill when session evidence or tradability is missing."""
    asof = "2026-11-12"
    q = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
         "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    assert mod.eligible_open("sh.600000", q, asof, "buy")
    for bad in (
        {**q, "last_quote_day": None},
        {**q, "last_quote_day": "2026-11-11"},
        {**q, "last_quote_day": "2026-11-13"},
        {**q, "tradestatus": None},
        {**q, "tradestatus": ""},
        {**q, "tradestatus": "2"},
        {**q, "preclose": 0},
        {**q, "close": -1},
    ):
        assert not mod.eligible_open("sh.600000", bad, asof, "buy")
        assert not mod.eligible_open("sh.600000", bad, asof, "sell")


def test_malformed_quote_and_invalid_side_fail_closed():
    """Bad market-data payloads or unknown actions cannot create assumed fills."""
    asof = "2026-11-12"
    valid = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
             "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    assert mod.eligible_open("sh.600000", valid, asof, "buy")
    for bad_quote in (None, [], "bad", {}, {**valid, "open": None},
                      {**valid, "open": "bad"}, {**valid, "volume": float("nan")},
                      {**valid, "preclose": float("inf")}):
        assert not mod.eligible_open("sh.600000", bad_quote, asof, "buy")
        assert not mod.eligible_open("sh.600000", bad_quote, asof, "sell")
    for invalid_side in ("", "BUY", "hold", None, 1):
        assert not mod.eligible_open("sh.600000", valid, asof, invalid_side)

def test_raw_quote_trade_halts_and_price_limit_guard_fail_closed():
    """Suspensions, zero liquidity, stale quotes and opening limits never fill."""
    asof = "2026-11-12"
    quote = {"last_quote_day": asof, "open": 10.0, "close": 10.0,
             "preclose": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    assert mod.eligible_open("sh.600000", quote, asof, "buy")
    assert mod.eligible_open("sh.600000", quote, asof, "sell")
    for bad in (
        {**quote, "tradestatus": "0"},
        {**quote, "volume": 0},
        {**quote, "volume": -1},
        {**quote, "last_quote_day": "2026-11-11"},
    ):
        assert not mod.eligible_open("sh.600000", bad, asof, "buy")
        assert not mod.eligible_open("sh.600000", bad, asof, "sell")
    assert not mod.eligible_open("sh.600000", {**quote, "open": 11.0}, asof, "buy")
    assert not mod.eligible_open("sh.600000", {**quote, "open": 9.0}, asof, "sell")


def test_stale_quotes_and_unlisted_boards_never_assume_fills():
    """A raw quote from an earlier session or unsupported board is not executable."""
    asof = "2026-11-12"
    q = {"last_quote_day": asof, "open": 10.0, "preclose": 10.0,
         "close": 10.0, "volume": 10000, "tradestatus": "1", "isST": "0"}
    for side in ("buy", "sell"):
        assert not mod.eligible_open("sh.600000", {**q, "last_quote_day": "2026-11-11"}, asof, side)
        assert not mod.eligible_open("sh.688001", q, asof, side)
        assert not mod.eligible_open("bj.920001", q, asof, side)
        assert not mod.eligible_open("invalid", q, asof, side)
