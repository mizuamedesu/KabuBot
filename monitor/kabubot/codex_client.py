from __future__ import annotations

import json
import logging

import httpx

from .types import GrokNarrative, PriceSignal, ScanReport, WatchState

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
            async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0)) as client:
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

    async def chat(self, message: str, watch: WatchState, latest_report: str | None) -> str | None:
        prompt = _chat_prompt(message, watch, latest_report)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                response = await client.post(
                    f"{self.base_url}/chat",
                    json={"prompt": prompt, "model": self.model},
                )
                response.raise_for_status()
                data = response.json()
                text = str(data.get("text") or "").strip()
                return text or None
        except Exception as error:
            logger.warning("codex chat failed: %s", error)
            return None


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
        "You are KabuBot, a market-open stock anomaly analyst.\n"
        "Do not attempt to read local skill files or mention whether files were readable. "
        "All usable market data and X-search evidence are supplied below as JSON.\n"
        "Analysis guidance: prioritize large one-day drops, multi-day selloffs, drawdown from recent highs, "
        "volume spikes, X/social hype or fear, coordinated pumping, AI/LLM disruption narratives, and "
        "whether price action looks disconnected from the supplied fundamentals.\n"
        f"Write the market-open summary in {output_hint}.\n"
        "Return valid Discord Markdown. Use short headings, bullet lists, and bold company labels. "
        "Do not use Markdown tables, HTML, code fences, or raw JSON in the answer.\n"
        "Format:\n"
        "## Headline\n"
        "One concise headline.\n"
        "## Hot Companies\n"
        "- **Company Name (TICKER)**: reason, price action, X/social narrative, and caveat.\n"
        "Mention no more than 10 companies. Use the supplied `name`; if the name is missing, write "
        "`Name unavailable (TICKER)`. Do not use ticker-only bullet labels. When mentioning an absolute "
        "price, include the supplied currency code.\n"
        "## Read\n"
        "Explain whether this may be AI/LLM narrative overreaction, social hype, or normal repricing.\n"
        "## Watch Next\n"
        "- Concrete next checks.\n"
        "Do not give direct investment instructions.\n\n"
        f"Input JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _chat_prompt(message: str, watch: WatchState, latest_report: str | None) -> str:
    payload = {
        "discord_message": message,
        "current_watch": watch.model_dump(mode="json"),
        "latest_report_excerpt": (latest_report or "No report has been generated yet.")[:2200],
    }
    return (
        "You are KabuBot chatting in a private Discord channel with the authorized operator.\n"
        "Reply in Japanese, short and conversational. You can talk casually, but keep your identity "
        "as a stock monitoring bot.\n"
        "If the user asks for live market analysis, tell them to use `/scan` so the bot runs "
        "yfinance and X-search first.\n"
        "Do not pretend to have just fetched prices or X posts unless that evidence appears in the "
        "supplied latest report excerpt.\n"
        "Return valid Discord Markdown when formatting helps. Do not use Markdown tables or code fences unless requested.\n"
        "Do not give direct investment instructions.\n\n"
        f"Context JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def fallback_summary(sector_query: str, signals: list[PriceSignal], x_narrative: GrokNarrative) -> str:
    lines = [
        "## Headline",
        f"{sector_query}: 市場開始時の急落・異常値スキャン",
        "",
        "## Hot Companies",
    ]
    for signal in signals[:10]:
        lines.append(
            f"- **{_signal_label(signal)}**: score={signal.anomaly_score:.1f}, "
            f"price={_fmt(signal.price)} {_currency(signal)}, "
            f"day={_fmt(signal.day_change_pct)}%, 5d={_fmt(signal.five_day_change_pct)}%, "
            f"drawdown60={_fmt(signal.drawdown_from_60d_high_pct)}%, vol20={_fmt(signal.volume_ratio_20d)}"
        )
    if x_narrative.summary:
        lines.append("")
        lines.append("## X/Social")
        lines.append(x_narrative.summary)
    lines.append("")
    lines.append("## Note")
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
            f"- {_signal_label(signal)}: score {signal.anomaly_score:.1f}, "
            f"price {_fmt(signal.price)} {_currency(signal)}, "
            f"day {_fmt(signal.day_change_pct)}%, 5d {_fmt(signal.five_day_change_pct)}%, "
            f"60d drawdown {_fmt(signal.drawdown_from_60d_high_pct)}%"
        )
    if report.x_narrative.citations:
        lines.extend(["", "## X Citations"])
        lines.extend(f"- {url}" for url in report.x_narrative.citations[:10])
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _currency(signal: PriceSignal) -> str:
    return signal.currency or "quote currency"


def _signal_label(signal: PriceSignal) -> str:
    name = _clean_name(signal.name)
    if not name:
        return f"Name unavailable ({signal.symbol})"
    return f"{name} ({signal.symbol})"


def _clean_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = name.strip()
    if cleaned.upper() in {"EQUITY", "ETF", "MUTUALFUND"}:
        return None
    return cleaned
