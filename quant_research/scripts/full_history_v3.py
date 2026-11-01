from __future__ import annotations

import json
import os
import signal
import time
from datetime import date
from pathlib import Path

import baostock as bs
import pandas as pd

START_DATE = os.getenv("START_DATE", "2016-01-01")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "200"))
CHECKPOINT_EVERY = int(os.getenv("CHECKPOINT_EVERY", "50"))
END_DATE = date.today().isoformat()

ROOT = Path("quant_research")
STATE_DIR = ROOT / "state" / "full_v3"
CACHE_ROOT = ROOT / "full_cache"
CACHE_DIR = CACHE_ROOT / "daily"
CHECKPOINT_DIR = CACHE_ROOT / "v3_checkpoint"

UNIVERSE_PATH = STATE_DIR / "universe_shsz_frozen.csv"
META_PATH = STATE_DIR / "universe_meta.json"
BSE_AUDIT_PATH = STATE_DIR / "bse_coverage_audit.json"
PROGRESS_PATH = STATE_DIR / "progress.json"
MANIFEST_PATH = STATE_DIR / "history_manifest.csv"
CHECKPOINT_PATH = CHECKPOINT_DIR / "checkpoint.json"
CHECKPOINT_MANIFEST_PATH = CHECKPOINT_DIR / "partial_manifest.csv"
CHUNK_PATH = STATE_DIR / (
    f"chunk_{os.getenv('GITHUB_RUN_NUMBER', '0')}_"
    f"{os.getenv('GITHUB_RUN_ATTEMPT', '1')}.csv"
)

FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,turn,tradestatus,pctChg,isST,"
    "peTTM,pbMRQ,psTTM,pcfNcfTTM"
)


class QueryTimeout(Exception):
    pass


def alarm_handler(signum, frame):
    raise QueryTimeout("query exceeded 60 seconds")


signal.signal(signal.SIGALRM, alarm_handler)


def ensure_dirs():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def login():
    last = None
    for attempt in range(6):
        try:
            signal.alarm(25)
            lg = bs.login()
            signal.alarm(0)
            if lg.error_code == "0":
                return
            last = RuntimeError(f"{lg.error_code}: {lg.error_msg}")
        except Exception as exc:
            signal.alarm(0)
            last = exc
        time.sleep(min(15, 1 + attempt * 2))
    raise RuntimeError(f"BaoStock login failed: {last}")


def logout():
    try:
        bs.logout()
    except Exception:
        pass


def rs_to_df(rs):
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"{rs.error_code}: {rs.error_msg}")
    return pd.DataFrame(rows, columns=rs.fields)


def get_basic_with_retry():
    last = None
    for attempt in range(8):
        try:
            login()
            df = rs_to_df(bs.query_stock_basic())
            logout()
            return df
        except Exception as exc:
            last = exc
            logout()
            time.sleep(min(30, 2**attempt))
    raise RuntimeError(f"query_stock_basic failed after retries: {last}")


def build_or_reuse_universe():
    start = pd.Timestamp(START_DATE)
    end = pd.Timestamp(END_DATE)

    if not UNIVERSE_PATH.exists():
        raw = get_basic_with_retry()
        s = raw[raw["type"].astype(str) == "1"].copy()
        for c in ["ipoDate", "outDate", "status"]:
            s[c] = s[c].fillna("").astype(str)
        s["ipo_dt"] = pd.to_datetime(s["ipoDate"], errors="coerce")
        s["out_dt"] = pd.to_datetime(s["outDate"], errors="coerce")
        s = s[
            (s["ipo_dt"].isna() | (s["ipo_dt"] <= end))
            & (s["out_dt"].isna() | (s["out_dt"] >= start))
        ].copy()
        s = s[s["code"].str.match(r"^(sh|sz)\.\d{6}$", na=False)]
        s = s.sort_values(["status", "ipo_dt", "code"]).reset_index(drop=True)
        s.drop(columns=["ipo_dt", "out_dt"], errors="ignore").to_csv(
            UNIVERSE_PATH, index=False
        )
        meta = {
            "frozen_at": date.today().isoformat(),
            "period_start": str(start.date()),
            "period_end": str(end.date()),
            "shsz_total": int(len(s)),
            "active": int((s["status"] == "1").sum()),
            "delisted": int((s["status"] == "0").sum()),
            "source": "BaoStock query_stock_basic",
            "scope": "SH/SZ only; BSE deliberately audited separately",
        }
        META_PATH.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(meta, ensure_ascii=False), flush=True)
    else:
        print(f"Reusing frozen universe: {UNIVERSE_PATH}", flush=True)

    if not BSE_AUDIT_PATH.exists():
        audit = {
            "checked_at": date.today().isoformat(),
            "status": "UNKNOWN",
            "current_bse_count": None,
            "source": "AKShare stock_info_bj_name_code",
            "historical_delisted_bse_complete": False,
            "note": (
                "BSE historical/delisted coverage must be completed before "
                "labeling the model 'full A-share'."
            ),
        }
        try:
            import akshare as ak

            bj = ak.stock_info_bj_name_code()
            bj.to_csv(STATE_DIR / "bse_current_snapshot.csv", index=False)
            audit["status"] = "CURRENT_LIST_OK"
            audit["current_bse_count"] = int(len(bj))
        except Exception as exc:
            audit["status"] = "CURRENT_LIST_FAILED"
            audit["error"] = str(exc)[:500]
        BSE_AUDIT_PATH.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(audit, ensure_ascii=False), flush=True)
    else:
        print(f"Reusing BSE audit: {BSE_AUDIT_PATH}", flush=True)


def normalize(df):
    if df.empty:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    numeric = [
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "amount",
        "turn",
        "pctChg",
        "peTTM",
        "pbMRQ",
        "psTTM",
        "pcfNcfTTM",
    ]
    for c in numeric:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["tradestatus", "isST", "adjustflag"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str)
    return (
        df.dropna(subset=["date"])
        .drop_duplicates(["date", "code"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def fetch_history(code, start_s, end_s):
    last = None
    for attempt in range(4):
        try:
            signal.alarm(60)
            rs = bs.query_history_k_data_plus(
                code,
                FIELDS,
                start_date=start_s,
                end_date=end_s,
                frequency="d",
                adjustflag="2",
            )
            raw = rs_to_df(rs)
            signal.alarm(0)
            raw_dup = 0
            if not raw.empty and "date" in raw.columns:
                keys = ["date"] + (["code"] if "code" in raw.columns else [])
                raw_dup = int(raw.duplicated(keys, keep=False).sum())
            return normalize(raw), raw_dup
        except Exception as exc:
            signal.alarm(0)
            last = exc
            logout()
            time.sleep(min(12, 2 + attempt * 2))
            login()
    raise RuntimeError(str(last))


def effective_start(row):
    ipo = pd.to_datetime(row.get("ipoDate", ""), errors="coerce")
    s = pd.Timestamp(START_DATE)
    return (max(s, ipo) if pd.notna(ipo) else s).strftime("%Y-%m-%d")


def effective_end(row):
    out = pd.to_datetime(row.get("outDate", ""), errors="coerce")
    e = pd.Timestamp(END_DATE)
    return (min(e, out) if pd.notna(out) else e).strftime("%Y-%m-%d")


def quality(df):
    if df.empty:
        return {
            "duplicate_dates": 0,
            "actual_nonpositive_close": 0,
            "missing_close_trading": 0,
            "missing_close_suspended": 0,
            "bad_ohlc_rows": 0,
        }
    trad = (
        df["tradestatus"].astype(str)
        if "tradestatus" in df
        else pd.Series("", index=df.index)
    )
    close = df["close"]
    actual_nonpos = int((close.notna() & (close <= 0)).sum())
    missing_trading = int(((trad == "1") & close.isna()).sum())
    missing_suspended = int(((trad == "0") & close.isna()).sum())
    valid = (trad == "1") & df[["open", "high", "low", "close"]].notna().all(axis=1)
    high_ref = df.loc[valid, ["open", "close", "low"]].max(axis=1)
    low_ref = df.loc[valid, ["open", "close", "high"]].min(axis=1)
    bad = int(
        (
            (df.loc[valid, "high"] < high_ref)
            | (df.loc[valid, "low"] > low_ref)
        ).sum()
    )
    return {
        "duplicate_dates": int(df["date"].duplicated().sum()),
        "actual_nonpositive_close": actual_nonpos,
        "missing_close_trading": missing_trading,
        "missing_close_suspended": missing_suspended,
        "bad_ohlc_rows": bad,
    }


def load_progress(universe_size):
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    return {
        "cursor": 0,
        "completed": False,
        "started_at": date.today().isoformat(),
        "universe_size": int(universe_size),
    }


def clear_checkpoint():
    CHECKPOINT_PATH.unlink(missing_ok=True)
    CHECKPOINT_MANIFEST_PATH.unlink(missing_ok=True)


def load_checkpoint(repo_cursor):
    if not CHECKPOINT_PATH.exists() or not CHECKPOINT_MANIFEST_PATH.exists():
        return repo_cursor, pd.DataFrame()

    try:
        cp = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        cp_cursor = int(cp.get("cursor", 0))
        if cp_cursor <= repo_cursor:
            clear_checkpoint()
            return repo_cursor, pd.DataFrame()
        partial = pd.read_csv(CHECKPOINT_MANIFEST_PATH, dtype={"status": str})
        print(
            f"Resuming from cache checkpoint cursor={cp_cursor} "
            f"(repository cursor={repo_cursor}, carried_rows={len(partial)})",
            flush=True,
        )
        return cp_cursor, partial
    except Exception as exc:
        print(f"Ignoring invalid checkpoint: {exc}", flush=True)
        clear_checkpoint()
        return repo_cursor, pd.DataFrame()


def write_checkpoint(cursor_value, carried, rows):
    current = pd.DataFrame(rows)
    parts = [x for x in [carried, current] if not x.empty]
    partial = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not partial.empty:
        partial = partial.drop_duplicates("code", keep="last")
        partial.to_csv(CHECKPOINT_MANIFEST_PATH, index=False)
    checkpoint = {
        "cursor": int(cursor_value),
        "updated_at": date.today().isoformat(),
        "run_number": os.getenv("GITHUB_RUN_NUMBER", ""),
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "partial_codes": int(len(partial)),
    }
    CHECKPOINT_PATH.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"CHECKPOINT saved cursor={cursor_value} partial_codes={len(partial)}",
        flush=True,
    )


def collect_one(row):
    code = row["code"]
    path = CACHE_DIR / f"{code.replace('.', '_')}.parquet"

    if path.exists():
        df = normalize(pd.read_parquet(path))
        if not df.empty:
            last_dt = df["date"].max()
            next_dt = (last_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            end_s = effective_end(row)
            if pd.Timestamp(next_dt) <= pd.Timestamp(end_s):
                inc, raw_dup = fetch_history(code, next_dt, end_s)
                if not inc.empty:
                    df = normalize(pd.concat([df, inc], ignore_index=True))
                    df.to_parquet(path, index=False)
            else:
                raw_dup = 0
        else:
            raw_dup = 0
        source = "cache+incremental"
    else:
        start_s = effective_start(row)
        end_s = effective_end(row)
        if pd.Timestamp(start_s) <= pd.Timestamp(end_s):
            df, raw_dup = fetch_history(code, start_s, end_s)
        else:
            df, raw_dup = pd.DataFrame(), 0
        if not df.empty:
            df.to_parquet(path, index=False)
        source = "baostock"

    q = quality(df)
    return {
        "code": code,
        "code_name": row.get("code_name", ""),
        "status": row.get("status", ""),
        "ipoDate": row.get("ipoDate", ""),
        "outDate": row.get("outDate", ""),
        "rows": int(len(df)),
        "min_date": "" if df.empty else str(df["date"].min().date()),
        "max_date": "" if df.empty else str(df["date"].max().date()),
        "result": "ok" if len(df) else "empty",
        "source": source,
        "error": "",
        "raw_duplicate_rows": int(raw_dup),
        **q,
    }


def error_row(row, exc):
    return {
        "code": row["code"],
        "code_name": row.get("code_name", ""),
        "status": row.get("status", ""),
        "ipoDate": row.get("ipoDate", ""),
        "outDate": row.get("outDate", ""),
        "rows": 0,
        "min_date": "",
        "max_date": "",
        "result": "error",
        "source": "baostock",
        "error": str(exc)[:500],
        "raw_duplicate_rows": 0,
        "duplicate_dates": 0,
        "actual_nonpositive_close": 0,
        "missing_close_trading": 0,
        "missing_close_suspended": 0,
        "bad_ohlc_rows": 0,
    }


def merge_manifest(partial):
    if MANIFEST_PATH.exists():
        old = pd.read_csv(MANIFEST_PATH, dtype={"status": str})
        merged = pd.concat([old, partial], ignore_index=True)
        merged = merged.drop_duplicates("code", keep="last").sort_values("code")
    else:
        merged = partial.sort_values("code")
    merged.to_csv(MANIFEST_PATH, index=False)
    return merged


def write_progress(progress, merged, end_idx, universe_size):
    progress["cursor"] = int(end_idx)
    progress["completed"] = bool(end_idx >= universe_size)
    progress["universe_size"] = int(universe_size)
    progress["downloaded_codes"] = int(len(merged))
    progress["ok_codes"] = int((merged["result"] == "ok").sum())
    progress["error_codes"] = int((merged["result"] == "error").sum())
    progress["empty_codes"] = int((merged["result"] == "empty").sum())
    progress["last_run_number"] = os.getenv("GITHUB_RUN_NUMBER", "")
    progress["last_run_attempt"] = os.getenv("GITHUB_RUN_ATTEMPT", "")
    progress["updated_at"] = date.today().isoformat()
    PROGRESS_PATH.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_final_quality(progress, merged, universe):
    if not progress["completed"]:
        print(json.dumps(progress, ensure_ascii=False, indent=2), flush=True)
        return

    ok = merged[merged["result"] == "ok"]
    final = {
        "status": (
            "READY_FOR_MODEL"
            if len(ok) >= int(len(universe) * 0.95)
            else "NEEDS_REPAIR"
        ),
        "scope": "SH/SZ historical universe only",
        "universe_size": int(len(universe)),
        "ok_codes": int(len(ok)),
        "error_codes": int((merged["result"] == "error").sum()),
        "empty_codes": int((merged["result"] == "empty").sum()),
        "delisted_total": int((universe["status"].astype(str) == "0").sum()),
        "delisted_ok": int(
            (
                (merged["status"].astype(str) == "0")
                & (merged["result"] == "ok")
            ).sum()
        ),
        "rows": int(ok["rows"].sum()),
        "raw_duplicate_rows": int(ok["raw_duplicate_rows"].sum()),
        "normalized_duplicate_dates": int(ok["duplicate_dates"].sum()),
        "actual_nonpositive_close": int(ok["actual_nonpositive_close"].sum()),
        "missing_close_trading": int(ok["missing_close_trading"].sum()),
        "missing_close_suspended": int(ok["missing_close_suspended"].sum()),
        "bad_ohlc_rows": int(ok["bad_ohlc_rows"].sum()),
        "bse_included": False,
        "note": (
            "BSE remains a separately tracked coverage gap; "
            "do not label this full A-share yet."
        ),
    }
    (STATE_DIR / "final_quality_summary.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(final, ensure_ascii=False, indent=2), flush=True)


def main():
    ensure_dirs()
    build_or_reuse_universe()

    universe = pd.read_csv(UNIVERSE_PATH, dtype=str)
    progress = load_progress(len(universe))
    repo_cursor = int(progress.get("cursor", 0))
    cursor, carried = load_checkpoint(repo_cursor)

    if cursor >= len(universe):
        progress["completed"] = True
        if MANIFEST_PATH.exists():
            merged = pd.read_csv(MANIFEST_PATH, dtype={"status": str})
            write_progress(progress, merged, len(universe), len(universe))
            write_final_quality(progress, merged, universe)
        clear_checkpoint()
        return

    end_idx = min(cursor + CHUNK_SIZE, len(universe))
    batch = universe.iloc[cursor:end_idx].copy()
    print(
        f"Processing SH/SZ frozen universe [{cursor}:{end_idx}] / {len(universe)} "
        f"chunk_size={CHUNK_SIZE} checkpoint_every={CHECKPOINT_EVERY}",
        flush=True,
    )

    rows = []
    login()
    try:
        for pos, (_, row) in enumerate(batch.iterrows(), start=1):
            code = row["code"]
            try:
                rows.append(collect_one(row))
            except Exception as exc:
                rows.append(error_row(row, exc))

            absolute_cursor = cursor + pos
            if pos == 1 or pos % 25 == 0 or pos == len(batch):
                ok = sum(x["result"] == "ok" for x in rows)
                err = sum(x["result"] == "error" for x in rows)
                print(
                    f"{pos}/{len(batch)} absolute_cursor={absolute_cursor} "
                    f"ok={ok} error={err} latest={code}",
                    flush=True,
                )

            if pos % CHECKPOINT_EVERY == 0 or pos == len(batch):
                write_checkpoint(absolute_cursor, carried, rows)

            time.sleep(0.05)
    finally:
        logout()

    current = pd.DataFrame(rows)
    pieces = [x for x in [carried, current] if not x.empty]
    partial = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    partial = partial.drop_duplicates("code", keep="last")
    partial.to_csv(CHUNK_PATH, index=False)

    merged = merge_manifest(partial)
    write_progress(progress, merged, end_idx, len(universe))
    write_final_quality(progress, merged, universe)

    # Repository state is now authoritative. Remove the cache checkpoint so the
    # next run starts from progress.json, while the parquet cache remains cumulative.
    clear_checkpoint()


if __name__ == "__main__":
    main()
