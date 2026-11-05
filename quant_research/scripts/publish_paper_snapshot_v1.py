"""Publish auditable research watchlist only after a complete fresh daily refresh.

No simulated or real orders. Never fabricate fills, cash or executable holdings.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path("quant_research")
INP = ROOT / "results" / "walkforward_v1"
OUT = ROOT / "state" / "paper_v1"
DAILY = ROOT / "state" / "daily_v1" / "daily_quality_gate.json"


def check_board(code):
    if code.startswith("sh."):
        return code[3:6] in {"600", "601", "603", "605"}
    if code.startswith("sz."):
        return code[3:6] in {"000", "001", "002", "003", "300", "301", "302"}
    return False


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    model = json.loads((INP / "model_status.json").read_text(encoding="utf-8"))
    paper = json.loads((INP / "paper_status.json").read_text(encoding="utf-8"))
    metrics = json.loads((INP / "oos_metrics.json").read_text(encoding="utf-8"))
    daily = (json.loads(DAILY.read_text(encoding="utf-8"))
             if DAILY.exists() else {"status": "MISSING"})

    asof = str(paper.get("signal_asof_date", ""))
    target = pd.read_csv(INP / "paper_target_candidates.csv", dtype={"code": str})
    target_codes = target["code"].astype(str).tolist()
    if (len(target) != 10 or target["code"].duplicated().any() or
            not all(map(check_board, target_codes))):
        raise RuntimeError("Invalid or non-permitted model target snapshot")

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    age = (now.date() - pd.Timestamp(asof).date()).days
    daily_ok = daily.get("status") == "PASS_RESEARCH_REFRESH"
    asof_ok = asof == daily.get("asof") or (
        daily_ok and 0 <= (pd.Timestamp(daily["asof"]) - pd.Timestamp(asof)).days <= 3)
    status = ("CANDIDATES_PUBLISHED_RESEARCH_ONLY"
              if daily_ok and asof_ok and age <= 3
              and model.get("status") == "RESEARCH_COMPLETED_UNVERIFIED"
              and paper.get("status") == "RESEARCH_WATCHLIST_ONLY"
              else "BLOCKED_BY_DAILY_FRESHNESS")
    report = {
        "status": status, "generated_at_china": now.isoformat(),
        "signal_asof_date": asof, "daily_data_gate": daily.get("status"),
        "daily_data_asof": daily.get("asof"),
        "model_oos_periods": metrics.get("oos_periods"),
        "candidate_count": len(target),
        "adjusted_price_series": True,
        "account_cash_changed": False,
        "broker_or_paper_order_sent": False,
        "executed_positions": False,
        "warning": "Research candidates, not executable positions or broker-validated trades.",
    }
    (OUT / "latest_status.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if status == "CANDIDATES_PUBLISHED_RESEARCH_ONLY":
        target.to_csv(OUT / "latest_target_candidates.csv", index=False)
        target.to_csv(OUT / f"candidates_{asof}.csv", index=False)
        shutil.copy2(INP / "oos_metrics.json", OUT / "latest_oos_metrics.json")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
