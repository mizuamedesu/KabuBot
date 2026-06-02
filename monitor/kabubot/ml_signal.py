from __future__ import annotations

import math

import numpy as np
from sklearn.ensemble import IsolationForest

from .types import PriceSignal


def enrich_anomaly_scores(signals: list[PriceSignal]) -> list[PriceSignal]:
    if not signals:
        return signals

    custom_scores = np.array([_custom_downside_score(signal) for signal in signals], dtype=float)
    model_scores = _isolation_scores(signals)
    combined = 0.7 * _normalize(custom_scores) + 0.3 * model_scores

    enriched: list[PriceSignal] = []
    for signal, score in zip(signals, combined, strict=False):
        if not _has_downside_pressure(signal):
            score = min(float(score), 12.0)
        notes = list(signal.notes)
        if score >= 75:
            notes.append("price action is statistically extreme versus recent history")
        enriched.append(signal.model_copy(update={"anomaly_score": round(float(score), 2), "notes": notes}))
    return sorted(enriched, key=lambda item: item.anomaly_score, reverse=True)


def _custom_downside_score(signal: PriceSignal) -> float:
    day_drop = max(0.0, -(signal.day_change_pct or 0.0))
    five_drop = max(0.0, -(signal.five_day_change_pct or 0.0))
    twenty_drop = max(0.0, -(signal.twenty_day_change_pct or 0.0))
    drawdown = max(0.0, -(signal.drawdown_from_60d_high_pct or 0.0))
    volume = max(0.0, (signal.volume_ratio_20d or 0.0) - 1.0)
    z_drop = max(0.0, -(signal.price_zscore_20d or 0.0))
    downside = day_drop + five_drop * 0.7 + twenty_drop * 0.35 + drawdown * 0.2 + z_drop * 3.0
    volume_bonus = volume * 8.0 if downside > 0 else 0.0
    return day_drop * 3.0 + five_drop * 1.6 + twenty_drop * 0.8 + drawdown * 0.45 + volume_bonus + z_drop * 9.0


def _has_downside_pressure(signal: PriceSignal) -> bool:
    return any([
        (signal.day_change_pct or 0.0) < 0.0,
        (signal.five_day_change_pct or 0.0) <= -3.0,
        (signal.twenty_day_change_pct or 0.0) <= -6.0,
        (signal.drawdown_from_60d_high_pct or 0.0) <= -10.0,
        (signal.price_zscore_20d or 0.0) <= -1.0,
    ])


def _isolation_scores(signals: list[PriceSignal]) -> np.ndarray:
    if len(signals) < 8:
        return _normalize(np.array([_custom_downside_score(signal) for signal in signals], dtype=float))

    rows = []
    for signal in signals:
        rows.append([
            signal.day_change_pct or 0.0,
            signal.five_day_change_pct or 0.0,
            signal.twenty_day_change_pct or 0.0,
            signal.sixty_day_change_pct or 0.0,
            signal.drawdown_from_60d_high_pct or 0.0,
            signal.volume_ratio_20d or 0.0,
            signal.price_zscore_20d or 0.0,
            _safe_log(signal.market_cap),
            signal.forward_pe or signal.trailing_pe or 0.0,
            signal.price_to_sales or 0.0,
        ])

    matrix = np.nan_to_num(np.array(rows, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    try:
        model = IsolationForest(n_estimators=150, contamination="auto", random_state=42)
        model.fit(matrix)
        raw = -model.decision_function(matrix)
        return _normalize(raw)
    except Exception:
        return _normalize(np.array([_custom_downside_score(signal) for signal in signals], dtype=float))


def _normalize(values: np.ndarray) -> np.ndarray:
    if len(values) == 0:
        return values
    finite = np.array([value for value in values if math.isfinite(float(value))], dtype=float)
    if len(finite) == 0:
        return np.zeros_like(values, dtype=float)
    low = float(np.percentile(finite, 5))
    high = float(np.percentile(finite, 95))
    if high <= low:
        return np.full_like(values, 50.0, dtype=float)
    return np.clip((values - low) / (high - low) * 100.0, 0.0, 100.0)


def _safe_log(value: float | None) -> float:
    if value is None or value <= 0:
        return 0.0
    return math.log(value)
