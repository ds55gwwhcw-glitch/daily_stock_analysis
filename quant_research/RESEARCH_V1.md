# SH/SZ Quant Research V1 — Data, Research, Virtual Account

## Verified historical baseline

- Frozen SH/SZ universe: **5,475 symbols**, including 252 delisted.
- Targeted V3 repair completed: **264/264 recovered**, **5,475/5,475 ok**, **0 errors**.
- Total historical raw-record rows: **11,155,363**. `repair_quality_gate.json` is PASS.
- Broker-permitted historical symbols: **4,854** (SH 600/601/603/605; SZ 000/001/002/003 and ChiNext 300/301/302).
- Active listed symbols eligible for daily incremental refresh as of 2026-11-04: **4,605**.
- BSE and STAR are excluded from broker-universe research. BSE historical coverage is NOT complete.

## Pipeline and operational safeguards

1. `.github/workflows/quant-full-history-v3.yml`: historical traversal completed; its 5,475 cursor does not change with incremental refresh.
2. `.github/workflows/quant-repair-v3-errors.yml`: targeted missing histories repaired. Three-round hard cap, strict zero-error gate, checkpointed state.
3. `.github/workflows/quant-walkforward-v1.yml`: expanding train-only monthly price/volume-factor research, OOS from 2023, benchmark comparison to same-universe equal weight. Runs tests; produces reproducible artifacts and writes lightweight gated research status to `state/paper_v1`.
4. `.github/workflows/quant-daily-incremental-v1.yml`: UTC 10:30 Mon-Fri (Beijing 18:30), 300-symbol batches, child-worker 25s hard timeout x 2 attempts; safe manifest commit and rolling cached-parquet save; self-dispatch until the selected as-of day completes. Retries failed symbols once at the end. Blocks new signals if errors or data freshness problems persist. This does not rescan 2016 onward.
5. `quant_research/scripts/publish_paper_snapshot_v1.py`: only publishes a candidate-watchlist after a complete daily refresh and fresh model signal. A blocked status is recorded otherwise.
6. `quant_research/scripts/paper_shadow_account_v1.py`: **isolated hypothetical virtual ledger**, with default CNY 1,000,000 fictitious starting cash. It queues new targets only at conservative weekday-based month-end, never assumes same-day execution of close-generated information, and uses later raw/unadjusted BaoStock quotes (`adjustflag=3`) to model next-session opening fills. Round lots, approximate limit-price/ST guards, cash constraints and cost assumptions are checked. Missing quotes mean no fill. Book and account updates never touch any real broker, QMT or PTrade.

## First sample out-of-sample research result, NOT executable PnL

- Period: 44 monthly OOS cycles, starting Jan 2023.
- Ten-stock multi-factor proxy cumulative return: **+48.02%**.
- Same-date equal-weight research proxy cumulative return: **+41.11%**.
- Strategy annualized return proxy: **+11.29%**.
- Max drawdown proxy: **-32.90%**, Sharpe proxy about **0.55**.
- These values depend on adjusted pricing, approximated trading costs and limited execution; they are **not** proven trading performance.

## Important unverified assumptions and hard stops

- BaoStock adjusted history is not a certified point-in-time corporate-action tape; the unadjusted price paper ledger also does not yet implement cash dividends, share splits, rights issues or complete exchange corporate events.
- Day-to-day virtual PnL and fills are **hypothetical** and can differ materially from market execution due to limit queues, actual liquidity, T+1, commission minima and tax/corporate-action rules. Approximate weekday month-end misses special exchange holidays rather than backdating a fill.
- Research candidate rows are not open portfolio positions. `latest_status.json` records BLOCKED if the daily snapshot has not passed its gate, and the virtual ledger does not fabricate positions during blocked states.
- No broker credentials, live orders or real account cash are used.
- Do not enable real QMT/PTrade until verified corporate actions, exchange calendar, independent benchmark, fee schedule, order-size constraints, walk-forward stability and separate out-of-sample paper validation are completed.

Never conflate a V3 raw coverage PASS, a daily freshness PASS, a research proxy and a broker-fill PASS.
