"""Repair failed SH/SZ history without resetting the original V3 universe/cache."""
from __future__ import annotations

import json
import os
from datetime import date

import pandas as pd

import full_history_v3_hard_timeout as hard

base = hard.base
ROOT = base.STATE_DIR
REPAIR_STATE = ROOT / "repair_progress.json"
REPORT = ROOT / "repair_quality_gate.json"
MAX_CODES = int(os.getenv("REPAIR_CHUNK_SIZE", "40"))
MAX_ROUNDS = int(os.getenv("REPAIR_MAX_ROUNDS", "3"))


def tradable_code(code: str) -> bool:
    if code.startswith("sh."):
        return code[3:6] in {"600", "601", "603", "605"}
    if code.startswith("sz."):
        return code[3:6] in {"000", "001", "002", "003", "300", "301", "302"}
    return False


def load_csv(path):
    df = pd.read_csv(path, dtype={"code": str, "status": str})
    if df["code"].duplicated().any():
        raise RuntimeError(f"Duplicate code in {path}")
    return df


def audit(manifest, progress, state):
    expected = int(progress["universe_size"])
    if len(manifest) != expected or manifest["code"].duplicated().any():
        raise RuntimeError("Manifest mismatch or duplicate codes")
    counts = manifest["result"].value_counts().to_dict()
    errors = manifest[manifest["result"] == "error"]
    permitted = manifest[manifest["code"].map(tradable_code)]
    tradable_errors = permitted[permitted["result"] == "error"]
    ok = manifest[manifest["result"] == "ok"]
    ready = len(errors) == 0 and len(tradable_errors) == 0 and int(counts.get("empty", 0)) == 0
    report = {
        "status": "REPAIRED" if ready else "NEEDS_REPAIR",
        "updated_at": date.today().isoformat(),
        "universe_size": expected,
        "ok_codes": int(counts.get("ok", 0)),
        "error_codes": int(counts.get("error", 0)),
        "empty_codes": int(counts.get("empty", 0)),
        "tradeable_universe_codes": int(len(permitted)),
        "tradeable_error_codes": int(len(tradable_errors)),
        "rows": int(pd.to_numeric(ok["rows"], errors="coerce").fillna(0).sum()),
        "round": int(state["round"]),
        "repair_terminal": bool(state["terminal"]),
        "model_data_gate": "PASS" if ready else "BLOCKED",
        "reason": "Strict zero-error gate; historical prices remain research-only."
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    final_path = ROOT / "final_quality_summary.json"
    if final_path.exists():
        final = json.loads(final_path.read_text(encoding="utf-8"))
        for k in ("ok_codes", "error_codes", "empty_codes", "rows"):
            final[k] = report[k]
        final["status"] = "READY_FOR_RESEARCH_REVIEW" if ready else "NEEDS_REPAIR"
        final["note"] = "Raw-data gate only; PIT corporate actions, execution costs and OOS remain unverified."
        final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    base.ensure_dirs()
    progress = json.loads(base.PROGRESS_PATH.read_text(encoding="utf-8"))
    if not progress.get("completed") or int(progress.get("cursor", 0)) != int(progress["universe_size"]):
        raise RuntimeError("V3 initial traversal must be complete before repair")
    universe = load_csv(base.UNIVERSE_PATH).set_index("code", drop=False)
    manifest = load_csv(base.MANIFEST_PATH)
    if set(universe.index) != set(manifest["code"]):
        raise RuntimeError("Frozen universe and manifest differ")
    if MAX_CODES < 1 or MAX_ROUNDS < 1:
        raise ValueError("Invalid repair size/round count")
    if REPAIR_STATE.exists():
        state = json.loads(REPAIR_STATE.read_text(encoding="utf-8"))
    else:
        state = {"round": 1, "cursor": 0, "terminal": False,
                 "started_at": date.today().isoformat(), "attempted": 0, "recovered": 0}

    if state.get("terminal"):
        print(json.dumps(audit(manifest, progress, state), ensure_ascii=False), flush=True)
        return

    round_no = int(state["round"])
    candidates_path = ROOT / f"repair_candidates_round_{round_no}.csv"
    if not candidates_path.exists():
        pd.DataFrame({"code": sorted(manifest.loc[manifest["result"] == "error", "code"])}).to_csv(
            candidates_path, index=False)
    candidates = pd.read_csv(candidates_path, dtype=str)["code"].tolist()
    cursor = int(state["cursor"])
    if cursor < 0 or cursor > len(candidates):
        raise RuntimeError("Invalid repair cursor")
    stop = min(cursor + MAX_CODES, len(candidates))
    changes = []
    for i, code in enumerate(candidates[cursor:stop], start=cursor + 1):
        if manifest.loc[manifest["code"] == code, "result"].iloc[0] == "ok":
            continue
        row = universe.loc[code]
        try:
            result = base.collect_one(row)
        except Exception as exc:
            result = base.error_row(row, exc)
        changes.append(result)
        state["attempted"] += 1
        state["recovered"] += int(result["result"] == "ok")
        if i % 10 == 0 or i == stop:
            print(f"REPAIR {i}/{len(candidates)} {code} {result['result']}", flush=True)

    if changes:
        manifest = pd.concat([manifest, pd.DataFrame(changes)], ignore_index=True)
        manifest = manifest.drop_duplicates("code", keep="last").sort_values("code")
        manifest.to_csv(base.MANIFEST_PATH, index=False)
    state["cursor"] = stop
    if stop >= len(candidates):
        remaining = int((manifest["result"] == "error").sum())
        if remaining == 0 or round_no >= MAX_ROUNDS:
            state["terminal"] = True
        else:
            state["round"] = round_no + 1
            state["cursor"] = 0
    state["updated_at"] = date.today().isoformat()
    REPAIR_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    report = audit(manifest, progress, state)

    # The traversal cursor remains at 5475; all result totals remain consistent.
    progress["ok_codes"] = report["ok_codes"]
    progress["error_codes"] = report["error_codes"]
    progress["empty_codes"] = report["empty_codes"]
    progress["downloaded_codes"] = int(len(manifest))
    progress["repair_last_run_number"] = os.getenv("GITHUB_RUN_NUMBER", "")
    progress["updated_at"] = date.today().isoformat()
    base.PROGRESS_PATH.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
