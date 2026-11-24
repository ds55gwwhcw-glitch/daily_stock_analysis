"""Fail-closed corporate action review for research-only execution.

A prior raw close vs the provider's current preclose is a discontinuity
screen, not a complete corporate-action feed. An explicit evidence record
is required to authorize ordinary accounting. No dividend/split is guessed.
"""
from decimal import Decimal, InvalidOperation
import datetime


def price(value):
    try:
        x = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid raw price")
    if not x.is_finite() or x <= 0:
        raise ValueError("Invalid raw price")
    return x


def corporate_action_review(position, quote, asof, evidence):
    """Return reason for blocking, or None for an explicitly cleared interval."""
    if not isinstance(position, dict) or not isinstance(quote, dict):
        return "missing position or raw quote"
    if quote.get("last_quote_day") != asof:
        return "stale raw quote"
    previous = position.get("last_quote_day")
    try:
        if datetime.date.fromisoformat(previous) >= datetime.date.fromisoformat(asof):
            return "invalid prior mark chronology"
        old = price(position.get("last_close"))
        reference = price(quote.get("preclose"))
    except (ValueError, TypeError):
        return "missing prior raw close or reference price"
    if not isinstance(evidence, dict) or evidence.get("start") != previous or evidence.get("end") != asof:
        return "corporate action coverage missing"
    if evidence.get("verified") is not True or evidence.get("events") != []:
        return "corporate action unresolved"
    if abs(old-reference) > Decimal("0.01"):
        return "raw price discontinuity"
    return None
