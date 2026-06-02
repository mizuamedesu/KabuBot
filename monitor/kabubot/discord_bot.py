from __future__ import annotations

import asyncio
import logging
import re

import discord

from .config import Settings
from .scanner import Scanner

logger = logging.getLogger(__name__)


class KabuDiscordBot(discord.Client):
    def __init__(self, settings: Settings, scanner: Scanner) -> None:
        intents = discord.Intents.default()
        intents.dm_messages = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.settings = settings
        self.scanner = scanner

    async def on_ready(self) -> None:
        logger.info("discord bot logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self.settings.discord_owner_user_id and str(message.author.id) != self.settings.discord_owner_user_id:
            await _send_chunks(message.channel, "このbotは許可されたユーザーからの指示だけ受け付けます。")
            return
        if not self._should_handle(message):
            return

        text = self._clean_content(message)
        if not text:
            await _send_chunks(message.channel, _help_text())
            return

        try:
            async with message.channel.typing():
                reply = await self._handle_text(text)
            await _send_chunks(message.channel, reply)
        except Exception as error:
            logger.exception("discord message handling failed")
            await _send_chunks(message.channel, f"処理に失敗しました: {error}")

    def _should_handle(self, message: discord.Message) -> bool:
        if isinstance(message.channel, discord.DMChannel):
            return True
        content = message.content.strip().lower()
        mentioned = self.user is not None and self.user.mentioned_in(message)
        return mentioned or content.startswith(("!kabu", "kabubot", "kabu ", "株bot"))

    def _clean_content(self, message: discord.Message) -> str:
        content = message.content.strip()
        if self.user is not None:
            content = re.sub(rf"<@!?{self.user.id}>", "", content).strip()
        content = re.sub(r"^(?:!kabu|kabubot|kabu|株bot)\s*", "", content, flags=re.I).strip()
        return content

    async def _handle_text(self, text: str) -> str:
        lowered = text.lower()
        if lowered in {"help", "ヘルプ", "使い方"}:
            return _help_text()
        if any(term in lowered for term in ["auth", "認証", "ログイン状態"]):
            status = await self.scanner.codex.auth_status()
            return f"Codex: {status.get('status')}\n{status.get('stderr') or status.get('stdout') or ''}".strip()
        if any(term in lowered for term in ["latest", "最新", "前回", "レポート"]):
            latest = self.scanner.store.latest_markdown()
            if latest and not _wants_scan(lowered):
                return latest[:3500]

        update = self.scanner.watch.update_from_message(text, apply=True)
        if not _wants_scan(lowered):
            return update.reply

        report = await self.scanner.scan(notify=False)
        return "\n\n".join([update.reply, report.codex_summary[:3200]])


async def run_discord_bot(settings: Settings, scanner: Scanner) -> KabuDiscordBot | None:
    if not settings.discord_bot_token:
        logger.info("DISCORD_BOT_TOKEN is not configured; Discord bot is disabled")
        return None
    bot = KabuDiscordBot(settings, scanner)
    asyncio.create_task(bot.start(settings.discord_bot_token))
    return bot


def _wants_scan(lowered: str) -> bool:
    return any(term in lowered for term in [
        "scan",
        "スキャン",
        "探して",
        "見て",
        "分析",
        "急落",
        "暴落",
        "今",
        "今日",
        "market",
    ])


async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
    clean = discord.utils.escape_mentions(text.strip() or "(empty)")
    for index in range(0, len(clean), 1900):
        await channel.send(clean[index:index + 1900])


def _help_text() -> str:
    return "\n".join([
        "KabuBot commands:",
        "- `ソフトウェアだけ。固定銘柄はいらない`",
        "- `AIインフラに変えて。NVDAとAMDも候補に入れて`",
        "- `MSFTとCRMだけ見て`",
        "- `今のテーマで急落を探して`",
        "- `最新レポート`",
    ])

