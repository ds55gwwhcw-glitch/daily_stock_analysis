"""Offline safety tests for verified-session month-end decisions."""
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts" / "paper_exchange_calendar_v1.py"
spec = importlib.util.spec_from_file_location("paper_calendar", path)
calendar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calendar)


def test_month_end_requires_actual_next_session():
    sessions = ["2026-09-29", "2026-09-30", "2026-10-09", "2026-10-12"]
    assert calendar.month_end_signal("2026-09-30", sessions)
    assert not calendar.month_end_signal("2026-09-29", sessions)
    assert not calendar.month_end_signal("2026-10-01", sessions)


@pytest.mark.parametrize("sessions", [
    [], ["2026-09-30", "2026-09-30"],
    ["2026-10-09", "2026-09-30"],
    ["2026-09-30", "2026-10-03"],
    ["2026-09-30", "not-a-date"],
])
def test_invalid_calendar_fails_closed(sessions):
    with pytest.raises(ValueError):
        calendar.month_end_signal("2026-09-30", sessions)


def test_truncated_calendar_fails_closed():
    with pytest.raises(ValueError, match="missing next"):
        calendar.month_end_signal("2026-09-30", ["2026-09-29", "2026-09-30"])


def test_long_gap_fails_closed():
    with pytest.raises(ValueError, match="gap"):
        calendar.month_end_signal("2026-09-30", ["2026-09-30", "2026-10-30"])


def test_year_end_rollover_uses_verified_next_session():
    sessions = ["2026-12-30", "2026-12-31", "2027-01-04"]
    assert calendar.month_end_signal("2026-12-31", sessions)
    assert not calendar.month_end_signal("2026-12-30", sessions)


def test_missing_holiday_session_never_invents_fill_day():
    sessions = ["2026-09-30", "2026-10-09", "2026-10-12"]
    assert not calendar.month_end_signal("2026-10-01", sessions)
    assert calendar.month_end_signal("2026-09-30", sessions)


def test_invalid_signal_date_fails_closed():
    with pytest.raises(ValueError):
        calendar.month_end_signal("2026-10-03", ["2026-09-30", "2026-10-09"])
