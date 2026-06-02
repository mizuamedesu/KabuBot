# yfinance-stock

Use this skill when KabuBot needs current or historical stock data.

## Data Rules

- Prefer explicit ticker symbols when the user supplies them.
- For natural-language sector requests, map the phrase to a broad Yahoo sector and then refine by company name, industry, and default sector universes.
- Use yfinance-derived prices and history only as market data. Do not invent prices, market caps, ratios, or volume.
- When metadata is missing or stale, say so and keep the uncertainty visible.

## Signal Features

- Daily, five-day, twenty-day, and sixty-day percentage moves.
- Drawdown from the recent sixty-day high.
- Volume relative to the previous twenty trading days.
- Twenty-day price z-score.
- Basic valuation context such as market cap, PE, forward PE, and price-to-sales when available.

## Output Bias

Focus on anomalous downside moves, not generic gainers. KabuBot is especially interested in software stocks where the market may be overreacting to AI or LLM disruption narratives.
