"""Read-only BaoStock calendar provider for isolated paper research."""
from datetime import date, timedelta
from paper_exchange_calendar_v1 import validated_sessions, month_end_signal


def fetch_sessions(start, end):
    import baostock as bs
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first or (last-first).days > 366:
        raise ValueError("Invalid calendar request")
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError("Calendar provider login failed")
    try:
        result = bs.query_trade_dates(start_date=start, end_date=end)
        rows = []
        while result.error_code == "0" and result.next():
            rows.append(dict(zip(result.fields, result.get_row_data())))
        if result.error_code != "0":
            raise RuntimeError("Calendar query failed")
        return sessions_from_rows(rows, first, last)
    finally:
        bs.logout()


def sessions_from_rows(rows, first, last):
    expected = [(first+timedelta(days=i)).isoformat()
                for i in range((last-first).days+1)]
    if [r.get("calendar_date") for r in rows] != expected:
        raise ValueError("Missing, duplicate or unordered calendar days")
    if any(r.get("is_trading_day") not in {"0", "1"} for r in rows):
        raise ValueError("Unknown trading day classification")
    sessions = [r["calendar_date"] for r in rows if r["is_trading_day"] == "1"]
    validated_sessions(sessions)
    return sessions
