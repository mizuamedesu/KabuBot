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
        intents.guilds = True
        intents.dm_messages = False
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.settings = settings
        self.scanner = scanner
        self.allowed_user_ids = set(settings.discord_allowed_user_ids)

    async def on_ready(self) -> None:
        logger.info("discord bot logged in as %s", self.user)
        await self._leave_unallowed_guilds()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if not self._guild_allowed(guild):
            logger.warning("leaving unallowed guild %s (%s)", guild.name, guild.id)
            await guild.leave()

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
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
        if message.guild is None or isinstance(message.channel, discord.DMChannel):
            return False
        if not self._guild_allowed(message.guild):
            return False
        if str(message.channel.id) != self.settings.discord_allowed_channel_id:
            return False
        if str(message.author.id) not in self.allowed_user_ids:
            return False
        return True

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

        if _is_auth_finish(lowered):
            return await self._finish_auth()
        if _is_auth_start(lowered):
            return await self._start_auth()

        if any(term in lowered for term in ["auth status", "認証状態", "ログイン状態"]):
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

    async def _start_auth(self) -> str:
        status = await self.scanner.codex.auth_status()
        if status.get("status") == "authenticated":
            return (
                "Codexはすでに認証済みです。\n"
                "このbotは `.env` の `DISCORD_ALLOWED_*` で指定したサーバー/チャンネル/ユーザーだけに反応します。"
            )

        started = await self.scanner.codex.auth_start()
        verification_uri = str(started.get("verificationUri") or started.get("verification_uri") or "")
        user_code = str(started.get("userCode") or started.get("user_code") or "")
        lines = [
            "Codex認証を開始しました。ブラウザでログインして、完了したらこのチャンネルで `認証完了` と送ってください。",
        ]
        if verification_uri:
            lines.append(f"URL: {verification_uri}")
        if user_code:
            lines.append(f"Code: `{user_code}`")
        if not verification_uri and not user_code:
            lines.append("認証コードを取得できませんでした。少し待ってから `認証状態` を確認してください。")
        return "\n".join(lines)

    async def _finish_auth(self) -> str:
        status = await self.scanner.codex.auth_status()
        if status.get("status") != "authenticated":
            detail = status.get("stderr") or status.get("stdout") or "まだCodexが未認証です。"
            return f"まだ完了していません。\n{detail}"

        return (
            "認証完了。以後、このbotは `.env` で指定済みのユーザーだけの指示に従います。\n"
            f"cron送信先: <#{self.settings.discord_allowed_channel_id}>"
        )

    async def _leave_unallowed_guilds(self) -> None:
        for guild in list(self.guilds):
            if self._guild_allowed(guild):
                continue
            logger.warning("leaving unallowed guild %s (%s)", guild.name, guild.id)
            try:
                await guild.leave()
            except discord.HTTPException:
                logger.exception("failed to leave unallowed guild %s (%s)", guild.name, guild.id)

    def _guild_allowed(self, guild: discord.Guild) -> bool:
        return str(guild.id) == self.settings.discord_allowed_guild_id


async def run_discord_bot(settings: Settings, scanner: Scanner) -> KabuDiscordBot | None:
    if not settings.discord_bot_token:
        logger.info("DISCORD_BOT_TOKEN is not configured; Discord bot is disabled")
        return None
    missing = [
        name
        for name, value in [
            ("DISCORD_ALLOWED_GUILD_ID", settings.discord_allowed_guild_id),
            ("DISCORD_ALLOWED_CHANNEL_ID", settings.discord_allowed_channel_id),
            ("DISCORD_ALLOWED_USER_IDS", ",".join(settings.discord_allowed_user_ids)),
        ]
        if not value
    ]
    if missing:
        logger.warning("Discord bot private settings missing: %s; bot is disabled", ", ".join(missing))
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


def _is_auth_start(lowered: str) -> bool:
    return lowered in {"auth", "login", "認証", "ログイン"} or any(term in lowered for term in [
        "codex認証して",
        "codex login",
        "認証開始",
        "ログイン開始",
    ])


def _is_auth_finish(lowered: str) -> bool:
    return any(term in lowered for term in [
        "認証完了",
        "ログイン完了",
        "auth done",
        "login done",
        "authenticated",
    ])


async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
    clean = discord.utils.escape_mentions(text.strip() or "(empty)")
    for index in range(0, len(clean), 1900):
        await channel.send(clean[index:index + 1900])


def _help_text() -> str:
    return "\n".join([
        "KabuBot commands:",
        "- `認証`",
        "- `認証完了`",
        "- `ソフトウェアだけ。固定銘柄はいらない`",
        "- `AIインフラに変えて。NVDAとAMDも候補に入れて`",
        "- `MSFTとCRMだけ見て`",
        "- `今のテーマで急落を探して`",
        "- `最新レポート`",
    ])
