from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    port: int
    data_dir: Path
    sector_query: str
    max_candidates: int
    market_timezone: str
    market_open_cron: str
    run_on_start: bool
    yfinance_threads: bool
    xai_api_key: str | None
    grok_model: str
    grok_query_days: int
    codex_runner_url: str
    codex_model: str | None
    discord_bot_token: str | None
    discord_owner_user_id: str | None
    discord_report_channel_id: str | None
    discord_webhook_url: str | None
    slack_webhook_url: str | None
    report_language: str


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    return Settings(
        port=int(os.getenv("PORT", "8790")),
        data_dir=data_dir,
        sector_query=os.getenv("SECTOR_QUERY", "ソフトウェア"),
        max_candidates=int(os.getenv("MAX_CANDIDATES", "45")),
        market_timezone=os.getenv("MARKET_TIMEZONE", "America/New_York"),
        market_open_cron=os.getenv("MARKET_OPEN_SCAN_CRON", "32 9 * * 1-5"),
        run_on_start=_bool("RUN_ON_START", "false"),
        yfinance_threads=_bool("YFINANCE_THREADS", "true"),
        xai_api_key=os.getenv("XAI_API_KEY"),
        grok_model=os.getenv("GROK_MODEL", "grok-3-mini"),
        grok_query_days=int(os.getenv("GROK_QUERY_DAYS", "2")),
        codex_runner_url=os.getenv("CODEX_RUNNER_URL", "http://codex-runner:8789"),
        codex_model=os.getenv("CODEX_MODEL") or None,
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN") or None,
        discord_owner_user_id=os.getenv("DISCORD_OWNER_USER_ID") or None,
        discord_report_channel_id=os.getenv("DISCORD_REPORT_CHANNEL_ID") or None,
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
        report_language=os.getenv("REPORT_LANGUAGE", "ja"),
    )

def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}
