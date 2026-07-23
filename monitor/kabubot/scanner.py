from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from .charts import ChartRenderer
from .codex_client import CodexClient
from .config import Settings
from .grok_x_skill import GrokXSkill
from .ml_signal import enrich_anomaly_scores
from .notifier import Notifier
from .storage import ReportStore
from .types import GrokNarrative, PriceSignal, ScanReport
from .watch import WatchStore
from .yfinance_skill import YFinanceSkill

logger = logging.getLogger(__name__)

EXTREME_ONE_DAY_DROP_EXCLUSION_PCT = -40.0
ANALYSIS_SIGNAL_LIMIT = 10


class Scanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.yfinance = YFinanceSkill(threads=settings.yfinance_threads)
        self.grok = GrokXSkill(settings.xai_api_key, settings.grok_model, settings.grok_query_days)
        self.codex = CodexClient(settings.codex_runner_url, settings.codex_model)
        discord_private_ready = bool(
            settings.discord_bot_token
            and settings.discord_allowed_guild_id
            and settings.discord_allowed_channel_id
            and settings.discord_allowed_user_ids
        )
        self.notifier = Notifier(
            settings.discord_webhook_url,
            settings.slack_webhook_url,
            settings.discord_bot_token if discord_private_ready else None,
            settings.discord_allowed_channel_id if discord_private_ready else None,
        )
        self.store = ReportStore(settings.data_dir)
        self.watch = WatchStore(settings.data_dir, settings.sector_query, [])
        self.charts = ChartRenderer(settings.data_dir)

    async def scan(
        self,
        sector_query: str | None = None,
        symbols: list[str] | None = None,
        max_candidates: int | None = None,
        notify: bool = True,
    ) -> ScanReport:
        watch_state = self.watch.get()
        sector = sector_query or watch_state.sector_query or self.settings.sector_query
        requested_symbols = symbols or []
        if not requested_symbols and watch_state.symbol_mode == "only":
            requested_symbols = watch_state.symbols
        watch_symbols = _unique_symbols(watch_state.symbols)
        limit = max_candidates or self.settings.max_candidates
        logger.info("starting scan sector=%s symbols=%s", sector, requested_symbols)

        if requested_symbols:
            price_signals = self.yfinance.quote_symbols(requested_symbols)
            yf_warnings: list[str] = []
        else:
            seed_symbols = watch_state.symbols if watch_state.symbol_mode == "augment" else []
            price_signals, yf_warnings = self.yfinance.scan_sector(sector, seed_symbols, limit)
            if watch_symbols:
                watch_price_signals = self.yfinance.quote_symbols(watch_symbols)
                price_signals = _merge_signals(price_signals, watch_price_signals)
        logger.info("yfinance complete sector=%s candidates=%d warnings=%d", sector, len(price_signals), len(yf_warnings))

        scored = _mark_watch_signals(enrich_anomaly_scores(price_signals), watch_symbols)
        search_candidates, extreme_excluded = _exclude_extreme_one_day_drops(scored)
        logger.info(
            "ml scoring complete sector=%s candidates=%d extreme_excluded=%d",
            sector,
            len(search_candidates),
            len(extreme_excluded),
        )
        top_for_x = _compose_report_signals(search_candidates, watch_symbols, ANALYSIS_SIGNAL_LIMIT)
        logger.info("x search candidates ready sector=%s top_for_x=%d", sector, len(top_for_x))
        x_narrative = await self.grok.analyze(sector, top_for_x)
        logger.info(
            "grok complete sector=%s enabled=%s citations=%d warnings=%d",
            sector,
            x_narrative.enabled,
            len(x_narrative.citations),
            len(x_narrative.warnings),
        )
        boosted = _apply_social_boost(search_candidates, x_narrative)[:limit]
        delivery_limit = max(ANALYSIS_SIGNAL_LIMIT, len(watch_symbols), len(requested_symbols))
        report_signals = _compose_report_signals(boosted, watch_symbols, delivery_limit)
        logger.info("codex summary start sector=%s boosted=%d", sector, len(boosted))
        summary, generated_by_codex = await self.codex.summarize(
            sector,
            report_signals[:ANALYSIS_SIGNAL_LIMIT],
            x_narrative,
            self.settings.report_language,
        )
        logger.info("codex summary complete sector=%s generated=%s", sector, generated_by_codex)

        report = ScanReport(
            id=f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}",
            created_at=datetime.now(UTC),
            sector_query=sector,
            symbols_requested=requested_symbols,
            candidates_scanned=len(price_signals),
            top_signals=report_signals,
            x_narrative=x_narrative,
            codex_summary=summary,
            generated_by_codex=generated_by_codex,
            warnings=yf_warnings + _exclusion_warnings(extreme_excluded) + x_narrative.warnings,
            metadata={
                "max_candidates": limit,
                "codex_runner_url": self.settings.codex_runner_url,
                "report_language": self.settings.report_language,
                "watch": watch_state.model_dump(mode="json"),
                "registered_watch_symbols": watch_symbols,
                "extreme_one_day_drop_exclusion_pct": EXTREME_ONE_DAY_DROP_EXCLUSION_PCT,
                "extreme_one_day_drop_excluded": [
                    {
                        "symbol": signal.symbol,
                        "name": signal.name,
                        "day_change_pct": signal.day_change_pct,
                    }
                    for signal in extreme_excluded
                ],
            },
        )
        self.store.save(report)
        if notify:
            chart_paths = await asyncio.to_thread(self.charts.render_report_charts, report)
            await self.notifier.send(report, chart_paths=chart_paths)
        logger.info("scan complete report=%s codex=%s", report.id, generated_by_codex)
        return report

    async def quote(self, symbols: list[str]) -> list[PriceSignal]:
        return enrich_anomaly_scores(self.yfinance.quote_symbols(symbols))


def _apply_social_boost(signals: list[PriceSignal], narrative: GrokNarrative) -> list[PriceSignal]:
    boosted: list[PriceSignal] = []
    for signal in signals:
        hype_score = narrative.hype_score_by_symbol.get(signal.symbol, 0.0)
        if hype_score <= 0:
            boosted.append(signal)
            continue
        notes = list(signal.notes)
        notes.append(f"X hype/fear score {hype_score:.0f}")
        boosted.append(
            signal.model_copy(
                update={
                    "anomaly_score": round(min(100.0, signal.anomaly_score + hype_score * 0.18), 2),
                    "notes": notes,
                }
            )
        )
    return sorted(boosted, key=lambda item: item.anomaly_score, reverse=True)


def _exclude_extreme_one_day_drops(signals: list[PriceSignal]) -> tuple[list[PriceSignal], list[PriceSignal]]:
    included: list[PriceSignal] = []
    excluded: list[PriceSignal] = []
    for signal in signals:
        effective_change = (
            signal.ex_dividend_adjusted_day_change_pct
            if signal.is_ex_dividend_date and signal.ex_dividend_adjusted_day_change_pct is not None
            else signal.day_change_pct
        )
        if effective_change is not None and effective_change <= EXTREME_ONE_DAY_DROP_EXCLUSION_PCT:
            excluded.append(signal)
            continue
        included.append(signal)
    return included, excluded


def _exclusion_warnings(signals: list[PriceSignal]) -> list[str]:
    if not signals:
        return []
    labels = ", ".join(_signal_label(signal) for signal in signals[:8])
    suffix = "" if len(signals) <= 8 else f", ... +{len(signals) - 8}"
    return [
        f"single-day drops <= {abs(EXTREME_ONE_DAY_DROP_EXCLUSION_PCT):.0f}% were excluded from X search/report: "
        f"{labels}{suffix}"
    ]


def _signal_label(signal: PriceSignal) -> str:
    return f"{signal.name} ({signal.symbol})" if signal.name else signal.symbol


def _merge_signals(primary: list[PriceSignal], secondary: list[PriceSignal]) -> list[PriceSignal]:
    merged: dict[str, PriceSignal] = {}
    order: list[str] = []
    for signal in primary + secondary:
        symbol = signal.symbol.upper()
        if symbol not in merged:
            order.append(symbol)
        merged[symbol] = signal
    return [merged[symbol] for symbol in order]


def _mark_watch_signals(signals: list[PriceSignal], watch_symbols: list[str]) -> list[PriceSignal]:
    watch_set = set(watch_symbols)
    marked: list[PriceSignal] = []
    for signal in signals:
        if signal.symbol not in watch_set:
            marked.append(signal)
            continue
        notes = list(signal.notes)
        if "registered watch" not in notes:
            notes.append("registered watch")
        marked.append(signal.model_copy(update={"notes": notes}))
    return marked


def _compose_report_signals(signals: list[PriceSignal], watch_symbols: list[str], limit: int) -> list[PriceSignal]:
    watch_order = {symbol: index for index, symbol in enumerate(watch_symbols)}
    watched = sorted(
        [signal for signal in signals if signal.symbol in watch_order],
        key=lambda signal: watch_order[signal.symbol],
    )
    others = [signal for signal in signals if signal.symbol not in watch_order]
    return _merge_signals(watched, others)[:limit]


def _unique_symbols(symbols: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        cleaned = symbol.strip().upper().removeprefix("$")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result
