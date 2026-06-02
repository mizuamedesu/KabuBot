from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PriceSignal(BaseModel):
    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    currency: str | None = None
    price: float | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_sales: float | None = None
    day_change_pct: float | None = None
    five_day_change_pct: float | None = None
    twenty_day_change_pct: float | None = None
    sixty_day_change_pct: float | None = None
    drawdown_from_60d_high_pct: float | None = None
    volume_ratio_20d: float | None = None
    price_zscore_20d: float | None = None
    anomaly_score: float = 0.0
    notes: list[str] = Field(default_factory=list)
    source: str = "yfinance"


class GrokNarrative(BaseModel):
    enabled: bool
    model: str | None = None
    query_window: str | None = None
    summary: str = ""
    raw_text: str = ""
    citations: list[str] = Field(default_factory=list)
    tickers_discussed: list[str] = Field(default_factory=list)
    hype_score_by_symbol: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ScanRequest(BaseModel):
    sector_query: str | None = None
    symbols: list[str] = Field(default_factory=list)
    max_candidates: int | None = None
    notify: bool = True


class WatchState(BaseModel):
    sector_query: str
    symbols: list[str] = Field(default_factory=list)
    symbol_mode: Literal["augment", "only"] = "augment"
    notes: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class WatchUpdateRequest(BaseModel):
    message: str
    apply: bool = True


class WatchUpdateResult(BaseModel):
    reply: str
    actions: list[str]
    state: WatchState


class ScanReport(BaseModel):
    id: str
    created_at: datetime
    sector_query: str
    symbols_requested: list[str] = Field(default_factory=list)
    candidates_scanned: int
    top_signals: list[PriceSignal]
    x_narrative: GrokNarrative
    codex_summary: str
    generated_by_codex: bool
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
