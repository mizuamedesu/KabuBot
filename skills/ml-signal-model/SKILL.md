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

## Future GPU Extension

If a GPU VM is available, this skill can be extended with sequence models, embeddings over filings/news, or custom finetuned classifiers. The default production path should remain lightweight and auditable.

