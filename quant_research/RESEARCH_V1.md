# SH/SZ Quant Research V1 — Research Only

The original V3 traversal covered 5,475 SH/SZ symbols, but 264 were error rows. Its old \`READY_FOR_MODEL\` mark meant only a 95% collection rate. **It was not an acceptable data-quality or live-trading gate.**

## Execution

1. \`A-share V3 Targeted Error Repair\` operates from the frozen 5,475-code universe and existing cumulative historical cache. It freezes the failed-code list per round, repairs 40 codes per run, self-dispatches with checkpoints in GitHub's committed state, and has a hard limit of three rounds. No historical reset, no force push, no fabricated successes.
2. The new \`repair_quality_gate.json\` requires **zero** failed/empty symbols before allowing quantitative research. Exhausted repair with errors is clearly \`BLOCKED\`. The authoritative V3 progress cursor stays 5,475 and result counts are reconciled.
3. \`quant_walkforward_v1.py --mode validate\` audits stock-code prefixes and emits \`permitted_universe.csv\`. Shanghai main board \`600/601/603/605\`; Shenzhen main board \`000/001/002/003\`; ChiNext \`300/301/302\`. Beijing, STAR, B-shares, ETFs and other categories excluded. Broker availability and daily ST/suspension must still be checked by date.
4. After repair passes, the repair workflow dispatches \`quant-walkforward-v1.yml\` in \`research\` mode. The model uses monthly momentum, reversal, low-volatility and liquidity features; expanding ridge fit, and **2023 onward** out-of-sample periods. Only training labels whose exit date was already observed prior to the test signal are eligible. Research uses next-session opening prices and 15bp buy / 20bp sell cost assumptions; produces inspectable GitHub Actions artifacts.

## Not ready for live or genuine daily simulated positions

- BaoStock forward-adjusted prices are **not** independently certified point-in-time corporate-action histories.
- Corporate action effects, delistings, suspended exits, limit-up/limit-down executions, T+1, round lots, minimum commissions and other slippage need broker-grade replay. Price-only factors do not validate a fundamental PIT model.
- These reports are a research proxy. A \`paper_target_candidates.csv\` is a **non-executable watchlist**, not an account position or confirmed fill. Outdated source data is marked \`STALE_NO_TRADING\`.
- Historical data need a separate validated **daily incremental refresh**; that job is not yet installed. For that reason no automated recurring simulated-holdings run or broker order has been enabled. Enable it only after verified fresh data, custody/account ledger and actual fill rules.

Keep \`progress.json\`, \`history_manifest.csv\`, repair gate, raw parquet cache, and model reports mutually consistent. Never treat \`cursor=5475\` as proof that all 5,475 historical datasets were downloaded correctly.
