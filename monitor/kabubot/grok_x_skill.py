from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .types import GrokNarrative, PriceSignal

logger = logging.getLogger(__name__)


class GrokXSkill:
    def __init__(self, api_key: str | None, model: str, query_days: int) -> None:
        self.api_key = api_key
        self.model = model
        self.query_days = query_days

    async def analyze(self, sector_query: str, signals: list[PriceSignal]) -> GrokNarrative:
        if not self.api_key:
            return GrokNarrative(
                enabled=False,
                model=self.model,
                summary="XAI_API_KEY is not configured; X live search was skipped.",
                warnings=["XAI_API_KEY is missing"],
            )

        now = datetime.now(UTC)
        from_date = (now - timedelta(days=self.query_days)).date().isoformat()
        to_date = now.date().isoformat()
        top_symbols = [signal.symbol for signal in signals[:12]]
        prompt = _build_prompt(sector_query, signals[:12])
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "Use X search as the primary source. Extract current ticker narratives, hype, fear, "
                        "coordinated pumping, and AI/LLM-related selloff talk. Keep interpretation minimal. "
                        "Return compact JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "tools": [
                {
                    "type": "x_search",
                    "from_date": from_date,
                    "to_date": to_date,
                    "enable_image_understanding": False,
                    "enable_video_understanding": False,
                }
            ],
            "store": False,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
                response = await client.post(
                    "https://api.x.ai/v1/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                logger.info("grok x search response received sector=%s", sector_query)
        except Exception as error:
            logger.warning("grok x search failed: %s", error)
            return GrokNarrative(
                enabled=True,
                model=self.model,
                query_window=f"{from_date}..{to_date}",
                summary=f"Grok X search failed: {error}",
                warnings=[str(error)],
            )

        text = _extract_output_text(data)
        logger.info("grok output text extracted sector=%s chars=%d", sector_query, len(text))
        parsed = _parse_json_object(text)
        logger.info("grok output parsed sector=%s parsed=%s", sector_query, bool(parsed))
        citations = _extract_citations(data)
        logger.info("grok citations extracted sector=%s citations=%d", sector_query, len(citations))
        tickers = parsed.get("tickers_discussed") if isinstance(parsed, dict) else None
        hype = parsed.get("hype_score_by_symbol") if isinstance(parsed, dict) else None
        summary = parsed.get("summary") if isinstance(parsed, dict) else None

        return GrokNarrative(
            enabled=True,
            model=self.model,
            query_window=f"{from_date}..{to_date}",
            summary=str(summary or text[:1200]),
            raw_text=text[:5000],
            citations=citations,
            tickers_discussed=[str(item).upper().removeprefix("$") for item in tickers] if isinstance(tickers, list) else top_symbols,
            hype_score_by_symbol=_normalize_hype(hype),
            warnings=[],
        )


def _build_prompt(sector_query: str, signals: list[PriceSignal]) -> str:
    compact = [
        signal.model_dump(
            include={
                "symbol",
                "name",
                "day_change_pct",
                "five_day_change_pct",
                "twenty_day_change_pct",
                "drawdown_from_60d_high_pct",
                "volume_ratio_20d",
                "anomaly_score",
                "notes",
            }
        )
        for signal in signals
    ]
    return (
        f"Sector/theme: {sector_query}\n"
        f"Candidate tickers and price anomalies:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
        "Search X for the current conversation around these tickers and the sector. "
        "Pay special attention to: AI/LLM disruption fear, forced selling, influencer pumping, "
        "short-seller campaigns, rumor-driven moves, and whether selloff narratives look disconnected from fundamentals.\n"
        "Return JSON with exactly these keys:\n"
        "{"
        "\"summary\": string in Japanese, "
        "\"tickers_discussed\": string[], "
        "\"hype_score_by_symbol\": object mapping ticker to 0-100 number, "
        "\"bearish_narratives\": string[], "
        "\"pump_or_coordination_signals\": string[], "
        "\"uncertainties\": string[]"
        "}"
    )


def _extract_output_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict):
                text = content.get("text") or content.get("output_text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_citations(data: Any) -> list[str]:
    found: list[str] = []
    visited = 0
    max_nodes = 2000

    def visit(value: Any) -> None:
        nonlocal visited
        visited += 1
        if visited > max_nodes:
            return
        if isinstance(value, dict):
            url = value.get("url") or value.get("uri")
            if isinstance(url, str) and url.startswith("http"):
                found.append(url)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data.get("citations") if isinstance(data, dict) else data)
    visit(data.get("output") if isinstance(data, dict) else data)
    seen: set[str] = set()
    unique: list[str] = []
    for url in found:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique[:20]


def _parse_json_object(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_hype(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw_score in value.items():
        try:
            score = float(raw_score)
        except Exception:
            continue
        result[str(key).upper().removeprefix("$")] = max(0.0, min(100.0, round(score, 2)))
    return result
