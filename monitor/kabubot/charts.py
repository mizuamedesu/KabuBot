from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import yfinance as yf

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
            if self.render_symbol_chart(signal.symbol, path, name=signal.name, currency=signal.currency):
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

            start = float(close.iloc[0])
            end = float(close.iloc[-1])
            change = ((end / start) - 1.0) * 100.0 if start else 0.0

            unit = currency or "quote currency"
            label = _chart_label(symbol, name)
            fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
            color = "#0f766e" if change >= 0 else "#dc2626"
            ax.plot(close.index, close.values, color=color, linewidth=2.2)
            ax.fill_between(close.index, close.values, close.min(), color=color, alpha=0.08)
            ax.scatter(close.index[-1], end, color=color, s=24, zorder=3)
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
