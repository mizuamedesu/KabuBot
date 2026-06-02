# grok-x-live-search

Use this skill when KabuBot needs real-time X narrative context through Grok X Search.

## Tool Contract

- Use xAI Responses API with the built-in `x_search` tool.
- Constrain searches with `from_date` and `to_date` when a recent market narrative is needed.
- Return citations when the API provides them.
- Never present X chatter as verified fact. Treat it as social signal, rumor, sentiment, or narrative evidence.

## Search Targets

- Ticker cashtags such as `$CRM` and plain ticker/name mentions.
- AI/LLM disruption fear, "software is dead" style claims, analyst downgrades, influencer pile-ons, short campaigns, and rumor-driven selloffs.
- Pumping or coordinated hype, especially when the price move is detached from fundamentals.

## Summary Rules

- Separate observed social narrative from verified business events.
- Highlight uncertainty and missing citations.
- Assign a 0-100 hype/fear score only from the supplied X-search result, not from price action alone.

