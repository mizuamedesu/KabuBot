from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

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
        limit = max_candidates or self.settings.max_candidates
        logger.info("starting scan sector=%s symbols=%s", sector, requested_symbols)

        if requested_symbols:
            price_signals = self.yfinance.quote_symbols(requested_symbols)
            yf_warnings: list[str] = []
        else:
            seed_symbols = watch_state.symbols if watch_state.symbol_mode == "augment" else []
            price_signals, yf_warnings = self.yfinance.scan_sector(sector, seed_symbols, limit)

        scored = enrich_anomaly_scores(price_signals)
        top_for_x = scored[: min(15, len(scored))]
        x_narrative = await self.grok.analyze(sector, top_for_x)
        boosted = _apply_social_boost(scored, x_narrative)[:limit]
        summary, generated_by_codex = await self.codex.summarize(
            sector,
            boosted[:12],
            x_narrative,
            self.settings.report_language,
        )

        report = ScanReport(
            id=f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}",
            created_at=datetime.now(UTC),
            sector_query=sector,
            symbols_requested=requested_symbols,
            candidates_scanned=len(price_signals),
            top_signals=boosted[:12],
            x_narrative=x_narrative,
            codex_summary=summary,
            generated_by_codex=generated_by_codex,
            warnings=yf_warnings + x_narrative.warnings,
            metadata={
                "max_candidates": limit,
                "codex_runner_url": self.settings.codex_runner_url,
                "report_language": self.settings.report_language,
                "watch": watch_state.model_dump(mode="json"),
            },
        )
        self.store.save(report)
        if notify:
            await self.notifier.send(report)
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
