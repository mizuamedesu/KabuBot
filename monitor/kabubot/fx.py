from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

PENCE_CODES = {"GBp", "GBX"}


def convert_value_to_usd(value: float | None, currency: str | None) -> float | None:
    if value is None:
        return None
    rate = usd_rate(currency)
    if rate is None:
        return value
    return round(value * rate, 4)


def convert_price_series_to_usd(series: pd.Series, currency: str | None, period: str = "3mo") -> tuple[pd.Series, bool]:
    if _normalize_currency(currency) == "USD":
        return series, True
    fx = usd_rate_series(currency, period)
    if fx is None or fx.empty:
        rate = usd_rate(currency)
        if rate is not None:
            return series * rate, True
        logger.warning("USD FX conversion unavailable for currency=%s", currency)
        return series, False
    aligned = fx.reindex(series.index).ffill().bfill()
    return (series * aligned).dropna(), True


@lru_cache(maxsize=128)
def usd_rate(currency: str | None) -> float | None:
    code = _normalize_currency(currency)
    if not code or code == "USD":
        return 1.0
    if code in PENCE_CODES:
        gbp_rate = usd_rate("GBP")
        return None if gbp_rate is None else gbp_rate / 100.0

    direct = _latest_close(f"{code}USD=X")
    if direct:
        return direct

    inverse = _latest_close(f"USD{code}=X")
    if inverse:
        return 1.0 / inverse

    logger.warning("USD FX rate unavailable for currency=%s", currency)
    return None


@lru_cache(maxsize=128)
def usd_rate_series(currency: str | None, period: str = "3mo") -> pd.Series | None:
    code = _normalize_currency(currency)
    if not code or code == "USD":
        return None
    if code in PENCE_CODES:
        gbp = usd_rate_series("GBP", period)
        return None if gbp is None else gbp / 100.0

    direct = _download_close(f"{code}USD=X", period)
    if direct is not None and not direct.empty:
        return direct

    inverse = _download_close(f"USD{code}=X", period)
    if inverse is not None and not inverse.empty:
        return 1.0 / inverse

    logger.warning("USD FX series unavailable for currency=%s", currency)
    return None


def _normalize_currency(currency: str | None) -> str | None:
    if not currency:
        return None
    code = currency.strip()
    if code in PENCE_CODES:
        return code
    return code.upper()


def _latest_close(ticker: str) -> float | None:
    series = _download_close(ticker, "5d")
    if series is None or series.empty:
        return None
    value = float(series.iloc[-1])
    return value if value > 0 else None


def _download_close(ticker: str, period: str) -> pd.Series | None:
    try:
        frame = yf.download(
            tickers=ticker,
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as error:
        logger.warning("FX download failed ticker=%s: %s", ticker, error)
        return None
    if frame.empty:
        return None
    if "Close" in frame.columns:
        return pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if getattr(frame.columns, "nlevels", 1) > 1:
        price_level = frame.columns.get_level_values(-1)
        if "Close" in price_level:
            return pd.to_numeric(frame.xs("Close", axis=1, level=-1).iloc[:, 0], errors="coerce").dropna()
    return pd.to_numeric(frame.iloc[:, 0], errors="coerce").dropna()
