from __future__ import annotations

import argparse
import sys
from pathlib import Path

import baostock as bs
import pandas as pd

FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,turn,tradestatus,pctChg,isST,"
    "peTTM,pbMRQ,psTTM,pcfNcfTTM"
)


def rs_to_df(rs):
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"{rs.error_code}: {rs.error_msg}")
    return pd.DataFrame(rows, columns=rs.fields)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--adjustflag", choices=["1", "2", "3"], default="2")
    args = parser.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    logged_in = False
    try:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"login {lg.error_code}: {lg.error_msg}")
        logged_in = True

        rs = bs.query_history_k_data_plus(
            args.code,
            FIELDS,
            start_date=args.start,
            end_date=args.end,
            frequency="d",
            adjustflag=args.adjustflag,
        )
        raw = rs_to_df(rs)
        raw.to_parquet(out, index=False)
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        if logged_in:
            try:
                bs.logout()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
