from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from .types import PriceSignal, ScanReport

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(
        self,
        discord_webhook_url: str | None,
        slack_webhook_url: str | None,
        discord_bot_token: str | None = None,
        discord_report_channel_id: str | None = None,
    ) -> None:
        self.discord_webhook_url = discord_webhook_url
        self.slack_webhook_url = slack_webhook_url
        self.discord_bot_token = discord_bot_token
        self.discord_report_channel_id = discord_report_channel_id

    async def send(self, report: ScanReport, chart_paths: list[Path] | None = None) -> None:
        text = _notification_text(report)
        chart_paths = chart_paths or []
        async with httpx.AsyncClient(timeout=20.0) as client:
            if self.discord_bot_token and self.discord_report_channel_id:
                await self._post_discord_bot(client, self.discord_report_channel_id, text)
                await self._post_discord_bot_files(client, self.discord_report_channel_id, chart_paths)
            if self.discord_webhook_url:
                await self._post_discord(client, text)
                await self._post_discord_files(client, chart_paths)
            if self.slack_webhook_url:
                await self._post_slack(client, text)
        if not any([self.discord_bot_token and self.discord_report_channel_id, self.discord_webhook_url, self.slack_webhook_url]):
            logger.info("no webhook configured; report stored only: %s", report.id)

    async def _post_discord_bot(self, client: httpx.AsyncClient, channel_id: str, text: str) -> None:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {self.discord_bot_token}"}
        for chunk in _chunks(text, 1900):
            response = await client.post(url, headers=headers, json={"content": chunk})
            response.raise_for_status()

    async def _post_discord_bot_files(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
        chart_paths: list[Path],
    ) -> None:
        if not chart_paths:
            return
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {self.discord_bot_token}"}
        await _post_discord_files_payload(client, url, headers, chart_paths)

    async def _post_discord(self, client: httpx.AsyncClient, text: str) -> None:
        for chunk in _chunks(text, 1900):
            response = await client.post(self.discord_webhook_url, json={"content": chunk})
            response.raise_for_status()

    async def _post_discord_files(self, client: httpx.AsyncClient, chart_paths: list[Path]) -> None:
        if not self.discord_webhook_url or not chart_paths:
            return
        await _post_discord_files_payload(client, self.discord_webhook_url, None, chart_paths)

    async def _post_slack(self, client: httpx.AsyncClient, text: str) -> None:
        response = await client.post(self.slack_webhook_url, json={"text": text[:3500]})
        response.raise_for_status()


def _notification_text(report: ScanReport) -> str:
    companies = ", ".join(_signal_label(signal) for signal in report.top_signals[:6])
    lines = [
        f"KabuBot {report.sector_query} scan: {companies}",
        "",
        report.codex_summary[:3200],
    ]
    if report.warnings:
        lines.append("")
        lines.append("Warnings: " + "; ".join(report.warnings[:4]))
    return "\n".join(lines)


def _chunks(text: str, size: int) -> list[str]:
    return [text[index:index + size] for index in range(0, len(text), size)]


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


async def _post_discord_files_payload(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str] | None,
    chart_paths: list[Path],
) -> None:
    total = len(chart_paths)
    for batch_start in range(0, total, 10):
        batch = chart_paths[batch_start:batch_start + 10]
        handles = []
        files = []
        try:
            for index, path in enumerate(batch):
                handle = path.open("rb")
                handles.append(handle)
                files.append((f"files[{index}]", (path.name, handle, "image/png")))
            if not files:
                continue
            payload = {"content": _chart_batch_label(batch_start, len(batch), total)}
            response = await client.post(
                url,
                headers=headers,
                data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                files=files,
            )
            response.raise_for_status()
        finally:
            for handle in handles:
                handle.close()


def _chart_batch_label(batch_start: int, batch_size: int, total: int) -> str:
    first = batch_start + 1
    last = batch_start + batch_size
    return f"価格チャート (3ヶ月・未調整終値・権利落ち表示) {first}-{last}/{total}"
