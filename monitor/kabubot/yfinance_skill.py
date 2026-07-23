from __future__ import annotations

import logging
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from .fx import usd_rate
from .sectors import SectorSpec, sector_spec
from .types import PriceSignal

logger = logging.getLogger(__name__)


class YFinanceSkill:
    def __init__(self, threads: bool = True) -> None:
        self.threads = threads

    def scan_sector(
        self,
        sector_query: str,
        seed_symbols: list[str],
        max_candidates: int,
    ) -> tuple[list[PriceSignal], list[str]]:
        spec = sector_spec(sector_query)
        warnings: list[str] = []
        symbols = self._candidate_symbols(spec, seed_symbols, max_candidates, warnings)
        signals = self.quote_symbols(symbols, spec)
        filtered = [signal for signal in signals if _matches_sector(signal, spec)]
        if len(filtered) < min(8, len(signals)):
            warnings.append("sector metadata was sparse, so fallback candidates were kept")
            filtered = signals
        return filtered[:max_candidates], warnings

    def quote_symbols(self, symbols: list[str], spec: SectorSpec | None = None) -> list[PriceSignal]:
        normalized = _unique_symbols(symbols)
        if not normalized:
            return []

        history = self._download_history(normalized)
        metadata = self._fetch_metadata(normalized)
        signals: list[PriceSignal] = []
        for symbol in normalized:
            frame = _frame_for_symbol(history, symbol)
            if frame is None or frame.empty:
                signals.append(PriceSignal(symbol=symbol, notes=["no price history returned by yfinance"]))
                continue
            signals.append(_build_signal(symbol, frame, metadata.get(symbol, {}), spec))
        return signals

    def _candidate_symbols(
        self,
        spec: SectorSpec,
        seed_symbols: list[str],
        max_candidates: int,
        warnings: list[str],
    ) -> list[str]:
        symbols: OrderedDict[str, None] = OrderedDict()
        for symbol in seed_symbols:
            symbols[_normalize_symbol(symbol)] = None
        for symbol in spec.default_tickers:
            symbols[_normalize_symbol(symbol)] = None

        for symbol in self._screen_symbols(spec, max_candidates=max(60, max_candidates * 2), warnings=warnings):
            symbols[_normalize_symbol(symbol)] = None

        cleaned = [symbol for symbol in symbols if symbol]
        return cleaned[: max(max_candidates, len(seed_symbols))]

    def _screen_symbols(self, spec: SectorSpec, max_candidates: int, warnings: list[str]) -> list[str]:
        symbols: list[str] = []

        if spec.yahoo_sector:
            try:
                from yfinance import EquityQuery

                query = EquityQuery("and", [
                    EquityQuery("eq", ["sector", spec.yahoo_sector]),
                    EquityQuery("gt", ["intradayprice", 1]),
                ])
                response = yf.screen(query, size=max_candidates, sortField="percentchange", sortAsc=True)
                symbols.extend(_symbols_from_screen(response))
            except Exception as error:
                warnings.append(f"Yahoo sector screener failed: {error}")

        for predefined in ["day_losers", "most_actives", "growth_technology_stocks"]:
            try:
                response = yf.screen(predefined, count=min(max_candidates, 100))
                symbols.extend(_symbols_from_screen(response))
            except Exception:
                continue
        return _unique_symbols(symbols)

    def _download_history(self, symbols: list[str]) -> pd.DataFrame:
        try:
            return yf.download(
                tickers=symbols,
                period="6mo",
                interval="1d",
                auto_adjust=False,
                actions=True,
                progress=False,
                group_by="ticker",
                threads=self.threads,
            )
        except Exception as error:
            logger.warning("yfinance download failed: %s", error)
            return pd.DataFrame()

    def _fetch_metadata(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        workers = min(8, max(1, len(symbols)))
        if len(symbols) == 1:
            return {symbols[0]: _ticker_metadata(symbols[0])}

        metadata: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_ticker_metadata, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    metadata[symbol] = future.result()
                except Exception as error:
                    metadata[symbol] = {"_warning": str(error)}
        return metadata


def _ticker_metadata(symbol: str) -> dict[str, Any]:
    ticker = yf.Ticker(symbol)
    info: dict[str, Any] = {}
    try:
        info.update(ticker.get_info() or {})
    except Exception as error:
        info["_info_warning"] = str(error)
    try:
        fast_info = ticker.fast_info
        info.update({
            "currency": _safe_get(fast_info, "currency") or info.get("currency"),
            "lastPrice": _safe_get(fast_info, "last_price") or info.get("currentPrice"),
            "marketCap": _safe_get(fast_info, "market_cap") or info.get("marketCap"),
        })
    except Exception:
        pass
    return info


def _build_signal(
    symbol: str,
    frame: pd.DataFrame,
    meta: dict[str, Any],
    spec: SectorSpec | None,
) -> PriceSignal:
    close = _numeric_series(frame.get("Close"))
    volume = _numeric_series(frame.get("Volume"))
    dividends = _numeric_series(frame.get("Dividends"))
    notes: list[str] = []

    name = meta.get("longName") or meta.get("shortName")
    currency = meta.get("currency")
    conversion_rate = usd_rate(currency)
    display_currency = "USD" if conversion_rate is not None else currency

    if len(close) < 2:
        return PriceSignal(
            symbol=symbol,
            name=name,
            currency=display_currency,
            original_currency=currency,
            notes=["not enough price points"],
        )

    price = _convert_to_usd(_safe_float(close.iloc[-1]), conversion_rate)
    market_cap = _convert_to_usd(_safe_float(meta.get("marketCap")), conversion_rate)
    day_change_pct = _pct_change(close, 1)
    five_day_change_pct = _pct_change(close, 5)
    twenty_day_change_pct = _pct_change(close, 20)
    sixty_day_change_pct = _pct_change(close, 60)
    drawdown = _drawdown_from_high(close, 60)
    volume_ratio = _volume_ratio(volume, 20)
    zscore = _zscore(close, 20)
    dividend_per_share_source = _value_at(dividends, close.index[-1])
    is_ex_dividend_date = bool(dividend_per_share_source and dividend_per_share_source > 0)
    ex_dividend_date = _date_string(close.index[-1]) if is_ex_dividend_date else None
    dividend_yield = _dividend_yield_pct(close, dividend_per_share_source)
    adjusted_day_change = _ex_dividend_adjusted_change_pct(close, dividend_per_share_source)
    dividend_per_share = _convert_to_usd(dividend_per_share_source, conversion_rate)

    if day_change_pct is not None and day_change_pct <= -5:
        notes.append("large one-day drop")
    if five_day_change_pct is not None and five_day_change_pct <= -12:
        notes.append("sharp five-day selloff")
    if drawdown is not None and drawdown <= -25:
        notes.append("deep drawdown from recent high")
    if volume_ratio is not None and volume_ratio >= 2.0:
        notes.append("volume is materially above 20-day average")
    if is_ex_dividend_date:
        notes.append(
            "ex-dividend date"
            f"; dividend={_fmt(dividend_per_share)} {display_currency or 'quote currency'}"
            f"; theoretical drop={_fmt(dividend_yield)}%"
            f"; ex-dividend-adjusted day={_fmt(adjusted_day_change)}%"
        )

    warning = meta.get("_info_warning") or meta.get("_warning")
    if warning:
        notes.append(f"metadata warning: {warning}")
    if currency and conversion_rate is not None and currency.upper() != "USD":
        notes.append(f"monetary values converted from {currency} to USD")
    if currency and conversion_rate is None:
        notes.append(f"USD conversion unavailable for {currency}; monetary values remain in source currency")

    return PriceSignal(
        symbol=symbol,
        name=name,
        sector=meta.get("sector") or spec.yahoo_sector if spec else meta.get("sector"),
        industry=meta.get("industry"),
        currency=display_currency,
        original_currency=currency,
        price=price,
        market_cap=market_cap,
        trailing_pe=_safe_float(meta.get("trailingPE")),
        forward_pe=_safe_float(meta.get("forwardPE")),
        price_to_sales=_safe_float(meta.get("priceToSalesTrailing12Months")),
        day_change_pct=day_change_pct,
        five_day_change_pct=five_day_change_pct,
        twenty_day_change_pct=twenty_day_change_pct,
        sixty_day_change_pct=sixty_day_change_pct,
        drawdown_from_60d_high_pct=drawdown,
        volume_ratio_20d=volume_ratio,
        price_zscore_20d=zscore,
        is_ex_dividend_date=is_ex_dividend_date,
        ex_dividend_date=ex_dividend_date,
        dividend_per_share=dividend_per_share if is_ex_dividend_date else None,
        dividend_yield_on_previous_close_pct=dividend_yield,
        ex_dividend_adjusted_day_change_pct=adjusted_day_change,
        notes=notes,
    )


def _frame_for_symbol(history: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    if history.empty:
        return None
    if isinstance(history.columns, pd.MultiIndex):
        first_level = set(str(value) for value in history.columns.get_level_values(0))
        second_level = set(str(value) for value in history.columns.get_level_values(1))
        if symbol in first_level:
            return history[symbol].dropna(how="all")
        if symbol in second_level:
            return history.xs(symbol, axis=1, level=1).dropna(how="all")
        return None
    return history.dropna(how="all")


def _matches_sector(signal: PriceSignal, spec: SectorSpec) -> bool:
    if not spec.yahoo_sector and not spec.keywords:
        return True
    text = " ".join(filter(None, [signal.sector, signal.industry, signal.name])).lower()
    if spec.yahoo_sector and (signal.sector or "").lower() == spec.yahoo_sector.lower():
        return True
    return any(keyword.lower() in text for keyword in spec.keywords)


def _symbols_from_screen(response: Any) -> list[str]:
    if not isinstance(response, dict):
        return []
    quotes = response.get("quotes") or response.get("finance", {}).get("result", [{}])[0].get("quotes", [])
    symbols = []
    for quote in quotes:
        if isinstance(quote, dict) and quote.get("symbol"):
            symbols.append(str(quote["symbol"]))
    return symbols


def _unique_symbols(symbols: list[str]) -> list[str]:
    unique: OrderedDict[str, None] = OrderedDict()
    for symbol in symbols:
        cleaned = _normalize_symbol(symbol)
        if cleaned:
            unique[cleaned] = None
    return list(unique.keys())


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().removeprefix("$")


def _numeric_series(value: Any) -> pd.Series:
    if value is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(value, errors="coerce").dropna()


def _pct_change(series: pd.Series, periods: int) -> float | None:
    if len(series) <= periods:
        return None
    start = _safe_float(series.iloc[-periods - 1])
    end = _safe_float(series.iloc[-1])
    if start is None or end is None or start == 0:
        return None
    return round((end / start - 1.0) * 100.0, 2)


def _value_at(series: pd.Series, index: Any) -> float | None:
    if series.empty or index not in series.index:
        return None
    return _safe_float(series.loc[index])


def _dividend_yield_pct(close: pd.Series, dividend_per_share: float | None) -> float | None:
    if not dividend_per_share or dividend_per_share <= 0 or len(close) < 2:
        return None
    previous_close = _safe_float(close.iloc[-2])
    if previous_close is None or previous_close == 0:
        return None
    return round(dividend_per_share / previous_close * 100.0, 2)


def _ex_dividend_adjusted_change_pct(
    close: pd.Series,
    dividend_per_share: float | None,
) -> float | None:
    if not dividend_per_share or dividend_per_share <= 0 or len(close) < 2:
        return None
    previous_close = _safe_float(close.iloc[-2])
    current_close = _safe_float(close.iloc[-1])
    if previous_close is None or current_close is None or previous_close == 0:
        return None
    return round(((current_close + dividend_per_share) / previous_close - 1.0) * 100.0, 2)


def _date_string(value: Any) -> str:
    try:
        return pd.Timestamp(value).date().isoformat()
    except Exception:
        return str(value)


def _drawdown_from_high(series: pd.Series, window: int) -> float | None:
    if series.empty:
        return None
    subset = series.tail(window)
    high = _safe_float(subset.max())
    last = _safe_float(subset.iloc[-1])
    if high is None or last is None or high == 0:
        return None
    return round((last / high - 1.0) * 100.0, 2)


def _volume_ratio(series: pd.Series, window: int) -> float | None:
    if len(series) <= 2:
        return None
    last = _safe_float(series.iloc[-1])
    baseline = _safe_float(series.tail(window + 1).iloc[:-1].mean())
    if last is None or baseline is None or baseline == 0:
        return None
    return round(last / baseline, 2)


def _zscore(series: pd.Series, window: int) -> float | None:
    subset = series.tail(window)
    if len(subset) < 5:
        return None
    mean = _safe_float(subset.mean())
    std = _safe_float(subset.std())
    last = _safe_float(subset.iloc[-1])
    if mean is None or std is None or last is None or std == 0:
        return None
    return round((last - mean) / std, 2)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if np.isnan(number) or np.isinf(number):
            return None
        return round(number, 4)
    except Exception:
        return None


def _convert_to_usd(value: float | None, rate: float | None) -> float | None:
    if value is None:
        return None
    if rate is None:
        return value
    return round(value * rate, 4)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _safe_get(obj: Any, key: str) -> Any:
    try:
        return obj[key]
    except Exception:
        try:
            return getattr(obj, key)
        except Exception:
            return None
