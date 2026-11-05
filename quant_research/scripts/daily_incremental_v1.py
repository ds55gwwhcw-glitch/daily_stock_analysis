"""Incremental post-close SH/SZ cache refresh, no historical reset.

Research data only. A day's close is not certified by the clock alone; a
freshness gate also checks multiple anchor symbols from the restored cache.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import full_history_v3_hard_timeout as hard

base = hard.base
STATE = Path("quant_research/state/daily_v1")
BATCH = int(os.getenv("DAILY_CHUNK_SIZE", "300"))
MAX_ERRORS = 0
MANIFEST = STATE / "daily_manifest.csv"
PROGRESS = STATE / "progress.json"
GATE = STATE / "daily_quality_gate.json"


def local_asof():
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    day = now.date()
    if now.time() < time(18, 15):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def eligible(df):
    # Only user-broker permitted boards and currently listed securities.
    def allowed(code):
        if code.startswith("sh."):
            return code[3:6] in {"600", "601", "603", "605"}
        if code.startswith("sz."):
            return code[3:6] in {"000", "001", "002", "003",
                                   "300", "301", "302"}
        return False
    return df[(df["status"] == "1") & df["code"].map(allowed)].sort_values("code").reset_index(drop=True)


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest():
    if MANIFEST.exists():
        x = pd.read_csv(MANIFEST, dtype={"code": str})
        if x["code"].duplicated().any():
            raise RuntimeError("Duplicate daily manifest rows")
        return x
    return pd.DataFrame(columns=["code", "result", "max_date", "error", "asof"])


def patch_manifest(manifest, changes):
    if not changes:
        return manifest
    newer = pd.DataFrame(changes)
    merged = pd.concat([manifest, newer], ignore_index=True)
    return merged.drop_duplicates("code", keep="last").sort_values("code").reset_index(drop=True)


def latest_anchor_date():
    available = []
    for code in ("sh.600000", "sz.000001", "sz.300750"):
        p = base.CACHE_DIR / (code.replace(".", "_") + ".parquet")
        if not p.exists():
            continue
        x = pd.read_parquet(p, columns=["date", "tradestatus"])
        dates = pd.to_datetime(x.loc[x["tradestatus"].astype(str) == "1", "date"], errors="coerce")
        if dates.notna().any():
            available.append(str(dates.max().date()))
    if not available:
        return None
    return max(available)


def main():
    base.ensure_dirs()
    STATE.mkdir(parents=True, exist_ok=True)
    if BATCH < 1 or BATCH > 500:
        raise ValueError("DAILY_CHUNK_SIZE must be between 1 and 500")
    full = json.loads(base.PROGRESS_PATH.read_text(encoding="utf-8"))
    repair_path = base.STATE_DIR / "repair_quality_gate.json"
    repair = json.loads(repair_path.read_text(encoding="utf-8"))
    if not full.get("completed") or repair.get("model_data_gate") != "PASS":
        raise RuntimeError("Cannot refresh until historical data are fully repaired")
    universe = pd.read_csv(base.UNIVERSE_PATH, dtype=str).fillna("")
    selection = eligible(universe)
    selected_codes = selection["code"].tolist()
    asof = local_asof()
    state = (json.loads(PROGRESS.read_text(encoding="utf-8"))
             if PROGRESS.exists() else {})
    if state.get("asof") != asof:
        state = {"asof": asof, "phase": "initial", "cursor": 0,
                 "retry_cursor": 0, "terminal": False,
                 "selected_codes": len(selection)}
        if MANIFEST.exists():
            MANIFEST.unlink()
        retry_file = STATE / "retry_codes.csv"
        retry_file.unlink(missing_ok=True)
    if state.get("selected_codes") != len(selection):
        raise RuntimeError("Changed daily eligible universe mid-refresh")
    manifest = load_manifest()
    if state.get("terminal"):
        print("Completed daily snapshot", asof, flush=True)
        return

    if state["phase"] == "initial":
        start = int(state["cursor"])
        take = selected_codes[start:start+BATCH]
    elif state["phase"] == "retry":
        retry_file = STATE / "retry_codes.csv"
        retry = pd.read_csv(retry_file, dtype={"code": str})["code"].tolist()
        start = int(state["retry_cursor"])
        take = retry[start:start+BATCH]
    else:
        raise RuntimeError("Invalid daily phase")

    by_code = selection.set_index("code", drop=False)
    updates = []
    for n, code in enumerate(take, 1):
        row = by_code.loc[code]
        try:
            r = base.collect_one(row)
            updates.append({
                "code": code, "result": r["result"],
                "max_date": r["max_date"], "error": r["error"], "asof": asof
            })
        except Exception as exc:
            updates.append({"code": code, "result": "error", "max_date": "",
                            "error": str(exc)[:350], "asof": asof})
        if n % 50 == 0 or n == len(take):
            print(f"DAILY {state['phase']} {n}/{len(take)} {code}", flush=True)
    manifest = patch_manifest(manifest, updates)
    manifest.to_csv(MANIFEST, index=False)

    if state["phase"] == "initial":
        state["cursor"] = start + len(take)
        if state["cursor"] >= len(selection):
            if len(manifest) != len(selection):
                raise RuntimeError("Daily manifest incomplete after initial scan")
            retry_codes = manifest.loc[manifest["result"] != "ok", "code"].tolist()
            if retry_codes:
                pd.DataFrame({"code": retry_codes}).to_csv(STATE / "retry_codes.csv", index=False)
                state["phase"] = "retry"
                state["retry_cursor"] = 0
            else:
                state["terminal"] = True
    else:
        state["retry_cursor"] = start + len(take)
        retry_count = len(pd.read_csv(STATE / "retry_codes.csv", dtype=str))
        if state["retry_cursor"] >= retry_count:
            state["terminal"] = True

    error_count = int((manifest["result"] != "ok").sum())
    anchor = latest_anchor_date() if state["terminal"] else None
    fresh = (anchor is not None and
             0 <= (pd.Timestamp(asof) - pd.Timestamp(anchor)).days <= 5)
    gate = {
        "asof": asof, "eligible_codes": len(selection),
        "processed_codes": len(manifest),
        "updated_codes": int((manifest["result"] == "ok").sum()),
        "error_codes": error_count, "anchor_latest_trade_date": anchor,
        "anchor_fresh_within_five_days": bool(fresh),
        "terminal": bool(state["terminal"]),
        "status": ("PASS_RESEARCH_REFRESH"
                   if state["terminal"] and error_count == MAX_ERRORS and fresh
                   else "BLOCKED"),
        "warning": "Anchor freshness is a heuristic; holidays, corporate actions, limit-up/down and T+1 remain unverified.",
    }
    save_json(PROGRESS, state)
    save_json(GATE, gate)
    print(json.dumps(gate, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
