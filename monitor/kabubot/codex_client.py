from __future__ import annotations

import json
import logging

import httpx

from .types import GrokNarrative, PriceSignal, ScanReport

logger = logging.getLogger(__name__)


class CodexClient:
    def __init__(self, base_url: str, model: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def auth_status(self) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.base_url}/auth/status",
            )
            response.raise_for_status()
            return response.json()

    async def auth_start(self) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self.base_url}/auth/start")
            response.raise_for_status()
            return response.json()

    async def summarize(
        self,
        sector_query: str,
        signals: list[PriceSignal],
        x_narrative: GrokNarrative,
        language: str,
    ) -> tuple[str, bool]:
        prompt = _summary_prompt(sector_query, signals, x_narrative, language)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
                response = await client.post(
                    f"{self.base_url}/chat",
                    json={"prompt": prompt, "model": self.model},
                )
                response.raise_for_status()
                data = response.json()
                text = str(data.get("text") or "").strip()
                if text:
                    return text, True
        except Exception as error:
            logger.warning("codex summary failed: %s", error)
        return fallback_summary(sector_query, signals, x_narrative), False


def _summary_prompt(
    sector_query: str,
    signals: list[PriceSignal],
    x_narrative: GrokNarrative,
    language: str,
) -> str:
    payload = {
        "sector_query": sector_query,
        "price_signals": [signal.model_dump() for signal in signals],
        "x_narrative": x_narrative.model_dump(),
    }
    output_hint = "Japanese" if language == "ja" else language
    return (
        "Use /workspace/skills/yfinance-stock/SKILL.md, "
        "/workspace/skills/grok-x-live-search/SKILL.md, "
        "/workspace/skills/ml-signal-model/SKILL.md, and "
        "/workspace/skills/market-analyzer/SKILL.md as guidance.\n"
        f"Write the market-open summary in {output_hint}.\n"
        "Format:\n"
        "- 1 line headline\n"
        "- Hot tickers ranked by urgency\n"
        "- Why this may be an overreaction or just normal repricing\n"
        "- X/social narrative evidence and uncertainty\n"
        "- What to monitor next\n"
        "Do not give direct investment instructions.\n\n"
        f"Input JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def fallback_summary(sector_query: str, signals: list[PriceSignal], x_narrative: GrokNarrative) -> str:
    lines = [f"{sector_query}: 市場開始時の急落・異常値スキャン"]
    for signal in signals[:8]:
        lines.append(
            f"- {signal.symbol}: score={signal.anomaly_score:.1f}, "
            f"day={_fmt(signal.day_change_pct)}%, 5d={_fmt(signal.five_day_change_pct)}%, "
            f"drawdown60={_fmt(signal.drawdown_from_60d_high_pct)}%, vol20={_fmt(signal.volume_ratio_20d)}"
        )
    if x_narrative.summary:
        lines.append("")
        lines.append(f"Xナラティブ: {x_narrative.summary}")
    lines.append("")
    lines.append("Codex runnerが未認証または利用不可だったため、これは決定的ロジックによるフォールバック要約です。")
    return "\n".join(lines)


def render_report_markdown(report: ScanReport) -> str:
    lines = [
        f"# KabuBot Scan {report.created_at.isoformat()}",
        "",
        report.codex_summary,
        "",
        "## Top Signals",
    ]
    for signal in report.top_signals:
        lines.append(
            f"- {signal.symbol}: score {signal.anomaly_score:.1f}, "
            f"day {_fmt(signal.day_change_pct)}%, 5d {_fmt(signal.five_day_change_pct)}%, "
            f"60d drawdown {_fmt(signal.drawdown_from_60d_high_pct)}%"
        )
    if report.x_narrative.citations:
        lines.extend(["", "## X Citations"])
        lines.extend(f"- {url}" for url in report.x_narrative.citations[:10])
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
