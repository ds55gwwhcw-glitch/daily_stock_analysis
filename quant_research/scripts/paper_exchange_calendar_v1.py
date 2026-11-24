"""Strict exchange-session calendar validation for research-only paper execution.

This module deliberately does not fetch calendars or send orders. A caller must
provide a verified list of actual exchange sessions. Missing coverage fails closed.
"""
from __future__ import annotations

from datetime import date, timedelta


def _parse_session(value):
    if not isinstance(value, str):
        raise ValueError("Session must be an ISO date string")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or parsed.weekday() >= 5:
        raise ValueError("Invalid exchange session")
    return parsed


def validated_sessions(values):
    """Return strictly ordered sessions; reject duplicates and bad dates."""
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("Verified exchange sessions required")
    dates = [_parse_session(v) for v in values]
    if dates != sorted(set(dates)):
        raise ValueError("Sessions must be unique and ascending")
    return dates


def month_end_signal(asof, session_values):
    """True only when verified sessions cover this month and the next month.

    Requires the next session, so truncated calendars cannot create a false
    month-end signal. This is a validation helper, not an execution switch.
    """
    day = _parse_session(asof)
    sessions = validated_sessions(session_values)
    if day not in sessions:
        return False
    idx = sessions.index(day)
    if idx + 1 >= len(sessions):
        raise ValueError("Calendar missing next verified session")
    following = sessions[idx + 1]
    if following - day > timedelta(days=14):
        raise ValueError("Unverified calendar gap")
    return following.month != day.month or following.year != day.year
