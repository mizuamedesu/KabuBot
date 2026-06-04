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
        if not self._should_handle(message):
            return

        text = self._clean_content(message)
        if not text:
            await _send_chunks(message.channel, _help_text())
            return

        try:
            async with message.channel.typing():
                reply = await self._handle_text(text, message)
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

    async def _handle_text(self, text: str, message: discord.Message) -> str:
        lowered = text.lower()
        if lowered in {"help", "ヘルプ", "使い方"}:
            return _help_text()

        if _is_auth_finish(lowered):
            return await self._finish_auth(message)
        if _is_auth_start(lowered):
            return await self._start_auth(message)

        if not self.scanner.discord_auth.is_authorized(message.author.id):
            if self.scanner.discord_auth.has_owner():
                return "このbotはCodex認証を完了したDiscordユーザーからの指示だけ受け付けます。"
            return "まだDiscordユーザーがCodex認証に紐づいていません。まず `認証` と送ってください。"

        if _is_bind_channel(lowered):
            state = self.scanner.discord_auth.bind_channel(
                channel_id=message.channel.id,
                channel_name=_channel_name(message),
                guild_id=message.guild.id if message.guild else None,
            )
            return f"このチャンネルをcronレポート送信先にしました: {state.channel_name or state.channel_id}"

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

    async def _start_auth(self, message: discord.Message) -> str:
        state = self.scanner.discord_auth.get()
        if state.owner_user_id and state.owner_user_id != str(message.author.id):
            return "すでに別のDiscordユーザーがCodex認証済みです。"
        if state.pending_user_id and state.pending_user_id != str(message.author.id):
            return "別のユーザーがCodex認証中です。認証を開始した本人だけが完了できます。"

        status = await self.scanner.codex.auth_status()
        if status.get("status") == "authenticated":
            activated = self._activate_current_channel(message)
            return (
                "Codexはすでに認証済みです。このDiscordユーザーとチャンネルを紐づけました。\n"
                f"ユーザー: {activated.owner_username}\n"
                f"cron送信先: {activated.channel_name or activated.channel_id}"
            )

        started = await self.scanner.codex.auth_start()
        verification_uri = str(started.get("verificationUri") or started.get("verification_uri") or "")
        user_code = str(started.get("userCode") or started.get("user_code") or "")
        self.scanner.discord_auth.save_pending(
            user_id=message.author.id,
            channel_id=message.channel.id,
            verification_uri=verification_uri or None,
            user_code=user_code or None,
        )
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

    async def _finish_auth(self, message: discord.Message) -> str:
        state = self.scanner.discord_auth.get()
        if state.pending_user_id and state.pending_user_id != str(message.author.id):
            return "認証を開始したDiscordユーザーだけが `認証完了` できます。"

        status = await self.scanner.codex.auth_status()
        if status.get("status") != "authenticated":
            detail = status.get("stderr") or status.get("stdout") or "まだCodexが未認証です。"
            return f"まだ完了していません。\n{detail}"

        activated = self._activate_current_channel(message)
        return (
            "認証完了。このDiscordユーザーだけがKabuBotへ指示できます。\n"
            f"ユーザー: {activated.owner_username}\n"
            f"cron送信先: {activated.channel_name or activated.channel_id}"
        )

    def _activate_current_channel(self, message: discord.Message):
        return self.scanner.discord_auth.activate(
            user_id=message.author.id,
            username=str(message.author),
            channel_id=message.channel.id,
            channel_name=_channel_name(message),
            guild_id=message.guild.id if message.guild else None,
        )


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


def _is_bind_channel(lowered: str) -> bool:
    return any(term in lowered for term in [
        "このチャンネル",
        "通知先",
        "送信先",
        "レポート先",
        "cron先",
        "bind channel",
    ])


def _channel_name(message: discord.Message) -> str:
    return getattr(message.channel, "name", None) or "DM"


async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
    clean = discord.utils.escape_mentions(text.strip() or "(empty)")
    for index in range(0, len(clean), 1900):
        await channel.send(clean[index:index + 1900])


def _help_text() -> str:
    return "\n".join([
        "KabuBot commands:",
        "- `認証`",
        "- `認証完了`",
        "- `このチャンネルを通知先にして`",
        "- `ソフトウェアだけ。固定銘柄はいらない`",
        "- `AIインフラに変えて。NVDAとAMDも候補に入れて`",
        "- `MSFTとCRMだけ見て`",
        "- `今のテーマで急落を探して`",
        "- `最新レポート`",
    ])
