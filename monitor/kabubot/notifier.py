from __future__ import annotations

import logging

import httpx

from .types import ScanReport

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

    async def send(self, report: ScanReport) -> None:
        text = _notification_text(report)
        async with httpx.AsyncClient(timeout=20.0) as client:
            if self.discord_bot_token and self.discord_report_channel_id:
                await self._post_discord_bot(client, text)
            if self.discord_webhook_url:
                await self._post_discord(client, text)
            if self.slack_webhook_url:
                await self._post_slack(client, text)
        if not any([self.discord_bot_token and self.discord_report_channel_id, self.discord_webhook_url, self.slack_webhook_url]):
            logger.info("no webhook configured; report stored only: %s", report.id)

    async def _post_discord_bot(self, client: httpx.AsyncClient, text: str) -> None:
        url = f"https://discord.com/api/v10/channels/{self.discord_report_channel_id}/messages"
        headers = {"Authorization": f"Bot {self.discord_bot_token}"}
        for chunk in _chunks(text, 1900):
            response = await client.post(url, headers=headers, json={"content": chunk})
            response.raise_for_status()

    async def _post_discord(self, client: httpx.AsyncClient, text: str) -> None:
        for chunk in _chunks(text, 1900):
            response = await client.post(self.discord_webhook_url, json={"content": chunk})
            response.raise_for_status()

    async def _post_slack(self, client: httpx.AsyncClient, text: str) -> None:
        response = await client.post(self.slack_webhook_url, json={"text": text[:3500]})
        response.raise_for_status()


def _notification_text(report: ScanReport) -> str:
    tickers = ", ".join(signal.symbol for signal in report.top_signals[:6])
    lines = [
        f"KabuBot {report.sector_query} scan: {tickers}",
        "",
        report.codex_summary[:3200],
    ]
    if report.warnings:
        lines.append("")
        lines.append("Warnings: " + "; ".join(report.warnings[:4]))
    return "\n".join(lines)


def _chunks(text: str, size: int) -> list[str]:
    return [text[index:index + size] for index in range(0, len(text), size)]
