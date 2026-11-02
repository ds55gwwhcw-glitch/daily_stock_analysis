from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

import full_history_v3 as base

HELPER = Path(__file__).with_name("baostock_fetch_one.py")
SINGLE_STOCK_TIMEOUT = int(os.getenv("SINGLE_STOCK_TIMEOUT", "90"))
SINGLE_STOCK_MAX_ATTEMPTS = int(os.getenv("SINGLE_STOCK_MAX_ATTEMPTS", "2"))


def hard_fetch_history(code: str, start_s: str, end_s: str):
    last: Exception | None = None

    for attempt in range(1, SINGLE_STOCK_MAX_ATTEMPTS + 1):
        with tempfile.TemporaryDirectory(dir=base.CACHE_ROOT) as tmpdir:
            output = Path(tmpdir) / "raw.parquet"
            cmd = [
                sys.executable,
                str(HELPER),
                "--code",
                code,
                "--start",
                start_s,
                "--end",
                end_s,
                "--output",
                str(output),
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=SINGLE_STOCK_TIMEOUT,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                last = RuntimeError(
                    f"hard timeout after {SINGLE_STOCK_TIMEOUT}s "
                    f"for {code} attempt {attempt}/{SINGLE_STOCK_MAX_ATTEMPTS}"
                )
                print(f"HARD_TIMEOUT {last}", flush=True)
                time.sleep(min(3, attempt))
                continue

            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "unknown child-process error").strip()
                detail = detail[-1000:]
                last = RuntimeError(
                    f"isolated fetch failed for {code} "
                    f"attempt {attempt}/{SINGLE_STOCK_MAX_ATTEMPTS}: {detail}"
                )
                print(f"FETCH_ERROR {last}", flush=True)
                time.sleep(min(3, attempt))
                continue

            if not output.exists():
                last = RuntimeError(f"isolated fetch produced no output for {code}")
                print(f"FETCH_ERROR {last}", flush=True)
                continue

            raw = pd.read_parquet(output)
            raw_dup = 0
            if not raw.empty and "date" in raw.columns:
                keys = ["date"] + (["code"] if "code" in raw.columns else [])
                raw_dup = int(raw.duplicated(keys, keep=False).sum())

            return base.normalize(raw), raw_dup

    raise RuntimeError(str(last) if last else f"isolated fetch failed for {code}")


base.fetch_history = hard_fetch_history


if __name__ == "__main__":
    base.main()
