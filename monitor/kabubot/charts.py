from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import yfinance as yf

from .fx import convert_price_series_to_usd
from .types import PriceSignal, ScanReport

logger = logging.getLogger(__name__)


class ChartRenderer:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "charts"
        self.root.mkdir(parents=True, exist_ok=True)

    def render_report_charts(self, report: ScanReport, limit: int = 10) -> list[Path]:
        output_dir = self.root / report.id
        output_dir.mkdir(parents=True, exist_ok=True)
        return self.render_signal_charts(report.top_signals, output_dir, limit)

    def render_signal_charts(self, signals: list[PriceSignal], output_dir: Path, limit: int = 10) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for signal in signals[:limit]:
            path = output_dir / f"{_safe_name(signal.symbol)}.png"
            source_currency = signal.original_currency or signal.currency
            if self.render_symbol_chart(signal.symbol, path, name=signal.name, currency=source_currency):
                paths.append(path)
        return paths

    def render_symbols(self, symbols: list[str], limit: int = 10) -> list[Path]:
        output_dir = self.root / "quotes"
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for symbol in symbols[:limit]:
            path = output_dir / f"{_safe_name(symbol)}.png"
            name, currency = _ticker_context(symbol)
            if self.render_symbol_chart(symbol, path, name=name, currency=currency):
                paths.append(path)
        return paths

    def render_symbol_chart(
        self,
        symbol: str,
        path: Path,
        name: str | None = None,
        currency: str | None = None,
    ) -> bool:
        try:
            frame = yf.download(
                tickers=symbol,
                period="3mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=False,
            )
            if frame.empty:
                return False
            close = _close_series(frame, symbol)
            if close.empty:
                return False
            close, converted_to_usd = convert_price_series_to_usd(close, currency, period="3mo")
            if close.empty:
                return False

            start = float(close.iloc[0])
            end = float(close.iloc[-1])
            change = ((end / start) - 1.0) * 100.0 if start else 0.0

            unit = "USD" if converted_to_usd else currency or "quote currency"
            label = _chart_label(symbol, name)
            fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
            color = "#0f766e" if change >= 0 else "#dc2626"
            ax.plot(close.index, close.values, color=color, linewidth=2.2)
            ax.fill_between(close.index, close.values, close.min(), color=color, alpha=0.08)
            ax.scatter(close.index[-1], end, color=color, s=24, zorder=3)
            _annotate_extrema(ax, close)
            ax.set_title(
                f"{label}\n3M close: {end:.2f} {unit} ({change:+.1f}%)",
                loc="left",
                fontsize=11,
                weight="bold",
            )
            ax.set_ylabel(f"Adjusted close ({unit})")
            ax.grid(True, axis="y", alpha=0.24)
            ax.grid(False, axis="x")
            fig.autofmt_xdate()
            fig.tight_layout()
            fig.savefig(path, format="png")
            plt.close(fig)
            return True
        except Exception as error:
            logger.warning("chart render failed symbol=%s: %s", symbol, error)
            try:
                plt.close("all")
            except Exception:
                pass
            return False


def _safe_name(symbol: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in symbol)


def _chart_label(symbol: str, name: str | None) -> str:
    clean_name = _clean_name(name)
    if not clean_name:
        return symbol
    label = f"{clean_name} ({symbol})"
    return label if len(label) <= 70 else f"{label[:67]}..."


def _clean_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = name.strip()
    if cleaned.upper() in {"EQUITY", "ETF", "MUTUALFUND"}:
        return None
    return cleaned


def _ticker_context(symbol: str) -> tuple[str | None, str | None]:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.get_info() or {}
        name = info.get("longName") or info.get("shortName")
        currency = info.get("currency")
        try:
            currency = ticker.fast_info.get("currency") or currency
        except Exception:
            pass
        return name, currency
    except Exception:
        return None, None


def _annotate_extrema(ax, close) -> None:
    peaks = _extrema_points(close, kind="peak", count=3)
    troughs = _extrema_points(close, kind="trough", count=3, excluded={index for index, _ in peaks})
    for index, value in peaks:
        ax.scatter(index, value, color="#334155", s=18, zorder=4)
        ax.annotate(
            f"{value:.2f}",
            xy=(index, value),
            xytext=(0, 9),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=7,
            color="#0f172a",
            bbox={"boxstyle": "round,pad=0.18", "fc": "white", "ec": "#cbd5e1", "alpha": 0.88},
        )
    for index, value in troughs:
        ax.scatter(index, value, color="#334155", s=18, zorder=4)
        ax.annotate(
            f"{value:.2f}",
            xy=(index, value),
            xytext=(0, -11),
            textcoords="offset points",
            ha="center",
            va="top",
            fontsize=7,
            color="#0f172a",
            bbox={"boxstyle": "round,pad=0.18", "fc": "white", "ec": "#cbd5e1", "alpha": 0.88},
        )


def _extrema_points(close, kind: str, count: int, excluded: set | None = None) -> list[tuple[object, float]]:
    excluded = excluded or set()
    values = list(close.values)
    indexes = list(close.index)
    candidates: list[tuple[object, float]] = []
    for position in range(1, len(values) - 1):
        left = float(values[position - 1])
        current = float(values[position])
        right = float(values[position + 1])
        if kind == "peak" and current >= left and current >= right and (current > left or current > right):
            candidates.append((indexes[position], current))
        if kind == "trough" and current <= left and current <= right and (current < left or current < right):
            candidates.append((indexes[position], current))

    reverse = kind == "peak"
    candidates = sorted(candidates, key=lambda item: item[1], reverse=reverse)
    selected = _unique_extrema(candidates, count, excluded)
    if len(selected) >= count:
        return sorted(selected, key=lambda item: item[0])

    fallback = sorted(
        [(index, float(value)) for index, value in zip(indexes, values) if index not in excluded],
        key=lambda item: item[1],
        reverse=reverse,
    )
    selected = _unique_extrema(selected + fallback, count, excluded)
    return sorted(selected, key=lambda item: item[0])


def _unique_extrema(points: list[tuple[object, float]], count: int, excluded: set) -> list[tuple[object, float]]:
    selected: list[tuple[object, float]] = []
    seen = set(excluded)
    for index, value in points:
        if index in seen:
            continue
        seen.add(index)
        selected.append((index, value))
        if len(selected) >= count:
            break
    return selected


def _close_series(frame, symbol: str):
    if "Close" in frame.columns:
        return frame["Close"].dropna()
    if getattr(frame.columns, "nlevels", 1) > 1:
        top_level = frame.columns.get_level_values(0)
        if symbol in top_level:
            return frame[symbol]["Close"].dropna()
        price_level = frame.columns.get_level_values(-1)
        if "Close" in price_level:
            return frame.xs("Close", axis=1, level=-1).iloc[:, 0].dropna()
    return frame.iloc[:, 0].dropna()
