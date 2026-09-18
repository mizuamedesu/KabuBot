# ml-signal-model

Use this skill when KabuBot needs generic anomaly detection or ranking across stock candidates.

## Baseline Model

- Combine a deterministic downside score with an IsolationForest anomaly score.
- Features include recent returns, drawdown, volume ratio, price z-score, market cap log, and valuation proxies.
- Rank by "research urgency", not by expected profit.

## Interpretation

- High anomaly score means "worth investigating now".
- It does not mean the stock is cheap, safe, or likely to rebound.
- Prefer explanations that name the exact feature causing the score.

## CPU-only execution

All statistical calculations, IsolationForest and Matplotlib rendering run on CPU.
Do not introduce GPU runtimes, CUDA, local language models or accelerator requirements.
The rank is relative to scanned candidates, not a probability or a hypothesis-test p-value.
Daily total-return Z compares against the previous 20 observations and adjusts cash dividends.
Peer comparisons exclude the subject and require the same date, source currency and industry (sector fallback).
