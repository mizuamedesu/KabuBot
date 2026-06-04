from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal

import discord
from discord import app_commands

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
        self.tree = app_commands.CommandTree(self)
        self.scan_lock = asyncio.Lock()
        self._register_slash_commands()

    async def setup_hook(self) -> None:
        guild = discord.Object(id=int(self.settings.discord_allowed_guild_id or "0"))
        synced = await self.tree.sync(guild=guild)
        logger.info("synced %d slash commands for guild %s", len(synced), self.settings.discord_allowed_guild_id)

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
            command, rest = _split_command(text)
            if command in {"scan", "スキャン"}:
                await self._send_latest_report_charts(message.channel)
            if command in {"quote", "quotes", "銘柄", "価格"}:
                await self._send_symbol_charts(message.channel, _symbols_from_text(rest))
        except Exception as error:
            logger.exception("discord message handling failed")
            await _send_chunks(message.channel, f"処理に失敗しました: {error}")

    def _register_slash_commands(self) -> None:
        guild = discord.Object(id=int(self.settings.discord_allowed_guild_id or "0"))

        @app_commands.command(name="help", description="KabuBotのコマンド一覧を表示")
        async def help_command(interaction: discord.Interaction) -> None:
            await self._handle_interaction(interaction, "help")

        @app_commands.command(name="auth", description="Codex認証の開始・完了・状態確認")
        @app_commands.describe(action="start / finish / status")
        async def auth_command(
            interaction: discord.Interaction,
            action: Literal["status", "start", "finish"] = "status",
        ) -> None:
            await self._handle_interaction(interaction, f"auth {action}")

        @app_commands.command(name="watch", description="監視テーマ・watch銘柄を表示または更新")
        @app_commands.describe(action="show / set", text="set時の自然文。例: ソフトウェアだけ。固定銘柄はいらない")
        async def watch_command(
            interaction: discord.Interaction,
            action: Literal["show", "set"] = "show",
            text: str = "",
        ) -> None:
            command_text = "watch show" if action == "show" else f"watch set {text}"
            await self._handle_interaction(interaction, command_text)

        @app_commands.command(name="scan", description="今のwatchまたは指定テーマで急落・異常値をスキャン")
        @app_commands.describe(sector="省略すると現在のwatchテーマを使う。例: ソフトウェア")
        async def scan_command(interaction: discord.Interaction, sector: str = "") -> None:
            await self._handle_interaction(interaction, f"scan {sector}".strip())

        @app_commands.command(name="quote", description="指定銘柄の価格シグナルを取得")
        @app_commands.describe(symbols="スペース区切り。例: MSFT CRM NOW")
        async def quote_command(interaction: discord.Interaction, symbols: str) -> None:
            await self._handle_interaction(interaction, f"quote {symbols}")

        @app_commands.command(name="report", description="最新レポートを表示")
        async def report_command(interaction: discord.Interaction) -> None:
            await self._handle_interaction(interaction, "report latest")

        @app_commands.command(name="chat", description="Codexに短く相談する")
        @app_commands.describe(message="Codexへ渡すメッセージ")
        async def chat_command(interaction: discord.Interaction, message: str) -> None:
            await self._handle_interaction(interaction, f"chat {message}")

        for command in [help_command, auth_command, watch_command, scan_command, quote_command, report_command, chat_command]:
            self.tree.add_command(command, guild=guild)

    async def _handle_interaction(self, interaction: discord.Interaction, text: str) -> None:
        if not self._interaction_allowed(interaction):
            await interaction.response.send_message("このサーバー/チャンネル/ユーザーでは使えません。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        command = ""
        success = True
        try:
            command, _ = _split_command(text)
            if command in {"scan", "スキャン"}:
                await interaction.followup.send("スキャン開始。yfinance -> Grok X Search -> Codex要約の順に走らせます。")
            reply = await self._handle_text(text)
        except Exception as error:
            logger.exception("slash command handling failed")
            success = False
            reply = f"処理に失敗しました: {error}"
        if command in {"scan", "スキャン"} and interaction.channel:
            await _send_chunks(interaction.channel, reply)
            if success:
                await self._send_latest_report_charts(interaction.channel)
            return
        if command in {"quote", "quotes", "銘柄", "価格"} and interaction.channel:
            await _send_interaction_chunks(interaction, reply)
            _, rest = _split_command(text)
            await self._send_symbol_charts(interaction.channel, _symbols_from_text(rest))
            return
        await _send_interaction_chunks(interaction, reply)

    def _interaction_allowed(self, interaction: discord.Interaction) -> bool:
        if str(interaction.guild_id or "") != self.settings.discord_allowed_guild_id:
            return False
        if str(interaction.channel_id or "") != self.settings.discord_allowed_channel_id:
            return False
        return str(interaction.user.id) in self.allowed_user_ids

    def _should_handle(self, message: discord.Message) -> bool:
        if message.guild is None or isinstance(message.channel, discord.DMChannel):
            return False
        if not self._guild_allowed(message.guild):
            return False
        if str(message.channel.id) != self.settings.discord_allowed_channel_id:
            return False
        if str(message.author.id) not in self.allowed_user_ids:
            return False
        content = message.content.strip()
        mentioned = self.user is not None and self.user.mentioned_in(message)
        prefixed = content.lower().startswith(("!kabu", "kabubot", "kabu ", "株bot"))
        cleaned = self._clean_content(message)
        return mentioned or prefixed or _looks_like_command(cleaned)

    def _clean_content(self, message: discord.Message) -> str:
        content = message.content.strip()
        if self.user is not None:
            content = re.sub(rf"<@!?{self.user.id}>", "", content).strip()
        content = re.sub(r"^(?:!kabu|kabubot|kabu|株bot)\s*", "", content, flags=re.I).strip()
        return content

    async def _handle_text(self, text: str) -> str:
        lowered = text.lower()
        command, rest = _split_command(text)

        if command in {"help", "ヘルプ", "使い方"}:
            return _help_text()

        if command in {"auth", "認証"}:
            if rest in {"", "start", "開始", "login", "ログイン"}:
                return await self._start_auth()
            if rest in {"finish", "done", "完了", "認証完了", "login done"}:
                return await self._finish_auth()
            if rest in {"status", "状態"}:
                status = await self.scanner.codex.auth_status()
                return f"Codex: {status.get('status')}\n{status.get('stderr') or status.get('stdout') or ''}".strip()

        if _is_auth_finish(lowered):
            return await self._finish_auth()
        if _is_auth_start(lowered):
            return await self._start_auth()

        if lowered in {"auth status", "認証状態", "ログイン状態"}:
            status = await self.scanner.codex.auth_status()
            return f"Codex: {status.get('status')}\n{status.get('stderr') or status.get('stdout') or ''}".strip()

        if command in {"report", "レポート"} and rest in {"", "latest", "最新"}:
            latest = self.scanner.store.latest_markdown()
            return latest[:3500] if latest else "まだレポートはありません。`scan` で初回スキャンできます。"
        if lowered in {"latest", "最新", "最新レポート"}:
            latest = self.scanner.store.latest_markdown()
            return latest[:3500] if latest else "まだレポートはありません。`scan` で初回スキャンできます。"

        if command in {"watch", "監視"}:
            if rest in {"", "show", "status", "状態", "表示"}:
                return _watch_state_text(self.scanner.watch.get())
            update_text = rest
            if rest.startswith(("set ", "設定 ", "update ", "変更 ")):
                update_text = rest.split(maxsplit=1)[1] if len(rest.split(maxsplit=1)) > 1 else ""
            if not update_text:
                return "watchに何を設定するか書いてください。例: `watch set ソフトウェアだけ。固定銘柄はいらない`"
            update = self.scanner.watch.update_from_message(update_text, apply=True)
            return update.reply

        if command in {"scan", "スキャン"}:
            if self.scan_lock.locked():
                return "すでにスキャン中です。終わるまで少し待ってください。"
            watch = self.scanner.watch.get()
            sector_query = None if rest in {"", "now", "current", "今", "今のテーマ"} else rest
            async with self.scan_lock:
                report = await self.scanner.scan(sector_query=sector_query or None, notify=False)
            intro = (
                "今のwatchでスキャンします。\n"
                f"現在のテーマ: {sector_query or watch.sector_query}\n"
                f"個別watch: {_watch_symbols_text(watch.symbols)}"
            )
            return "\n\n".join([intro, report.codex_summary[:3200]])

        if command in {"quote", "quotes", "銘柄", "価格"}:
            symbols = _symbols_from_text(rest)
            if not symbols:
                return "銘柄を指定してください。例: `quote MSFT CRM NOW`"
            signals = await self.scanner.quote(symbols)
            return _quote_text(signals)

        if command in {"chat", "雑談"}:
            if not rest:
                return "chatに続けて話しかけてください。例: `chat いま何を見てる？`"
            reply = await self.scanner.codex.chat(
                rest,
                self.scanner.watch.get(),
                self.scanner.store.latest_markdown(),
            )
            if reply:
                return reply[:3500]
            return "受け取りました。ただ、いまCodexチャット応答が使えません。"

        return "未知のコマンドです。`help` で使えるコマンドを見られます。"

    async def _send_latest_report_charts(self, channel: discord.abc.Messageable) -> None:
        try:
            report = self.scanner.store.latest_report()
            if not report:
                return
            paths = await asyncio.to_thread(self.scanner.charts.render_report_charts, report, 10)
            await _send_chart_files(channel, paths)
        except Exception:
            logger.exception("failed to send report charts")

    async def _send_symbol_charts(self, channel: discord.abc.Messageable, symbols: list[str]) -> None:
        if not symbols:
            return
        try:
            signals = await self.scanner.quote(symbols)
            paths = await asyncio.to_thread(
                self.scanner.charts.render_signal_charts,
                signals,
                self.scanner.charts.root / "quotes",
                10,
            )
            await _send_chart_files(channel, paths)
        except Exception:
            logger.exception("failed to send symbol charts")

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


def _split_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "", ""
    if stripped in {"認証完了", "認証状態", "ログイン状態", "最新レポート"}:
        return stripped, ""
    parts = stripped.split(maxsplit=1)
    command = parts[0].lower() if re.fullmatch(r"[A-Za-z]+", parts[0]) else parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    return command, rest


def _looks_like_command(text: str) -> bool:
    command, rest = _split_command(text)
    if text in {"認証完了", "認証状態", "ログイン状態", "最新レポート"}:
        return True
    if command in {
        "help",
        "ヘルプ",
        "使い方",
        "auth",
        "認証",
        "watch",
        "監視",
        "scan",
        "スキャン",
        "report",
        "レポート",
        "latest",
        "最新",
        "quote",
        "quotes",
        "銘柄",
        "価格",
        "chat",
        "雑談",
    }:
        return True
    return bool(rest) and command in {"kabu", "kabubot", "株bot"}


def _symbols_from_text(text: str) -> list[str]:
    symbols: list[str] = []
    for item in re.split(r"[\s,、]+", text.strip()):
        cleaned = item.strip().upper().removeprefix("$")
        if re.fullmatch(r"[A-Z0-9]{1,8}(?:\.[A-Z]{1,3})?|\d{4}\.T", cleaned):
            symbols.append(cleaned)
    return list(dict.fromkeys(symbols))


def _watch_symbols_text(symbols: list[str]) -> str:
    return "なし" if not symbols else ", ".join(symbols)


def _watch_state_text(watch) -> str:
    mode = "限定" if watch.symbol_mode == "only" else "セクター候補に追加"
    return (
        f"現在のテーマ: {watch.sector_query}\n"
        f"個別watch: {_watch_symbols_text(watch.symbols)} ({mode})"
    )


def _quote_text(signals) -> str:
    if not signals:
        return "価格を取得できませんでした。"
    lines = ["価格シグナル:"]
    for signal in signals[:12]:
        lines.append(
            f"- {_signal_label(signal)}: score={signal.anomaly_score:.1f}, "
            f"price={_fmt(signal.price)} {_currency(signal)}, "
            f"day={_fmt(signal.day_change_pct)}%, "
            f"5d={_fmt(signal.five_day_change_pct)}%, "
            f"drawdown60={_fmt(signal.drawdown_from_60d_high_pct)}%"
        )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _currency(signal) -> str:
    return getattr(signal, "currency", None) or "quote currency"


def _signal_label(signal) -> str:
    name = _clean_name(getattr(signal, "name", None))
    symbol = getattr(signal, "symbol", "UNKNOWN")
    if not name:
        return f"Name unavailable ({symbol})"
    return f"{name} ({symbol})"


def _clean_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = name.strip()
    if cleaned.upper() in {"EQUITY", "ETF", "MUTUALFUND"}:
        return None
    return cleaned


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


async def _send_interaction_chunks(interaction: discord.Interaction, text: str) -> None:
    clean = discord.utils.escape_mentions(text.strip() or "(empty)")
    chunks = [clean[index:index + 1900] for index in range(0, len(clean), 1900)]
    if not chunks:
        chunks = ["(empty)"]
    await interaction.followup.send(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


async def _send_chart_files(channel: discord.abc.Messageable, paths) -> None:
    files: list[discord.File] = []
    for path in paths[:10]:
        try:
            files.append(discord.File(str(path), filename=path.name))
        except Exception:
            logger.exception("failed to attach chart %s", path)
    if files:
        await channel.send(content="価格チャート (3ヶ月・調整後終値)", files=files)


def _help_text() -> str:
    return "\n".join([
        "KabuBot commands:",
        "- `/auth action:status`",
        "- `/watch action:show`",
        "- `/watch action:set text:ソフトウェアだけ。固定銘柄はいらない`",
        "- `/scan`",
        "- `/scan sector:ソフトウェア`",
        "- `/quote symbols:MSFT CRM NOW`",
        "- `/report`",
        "- `/chat message:いま何を見てる？`",
    ])
