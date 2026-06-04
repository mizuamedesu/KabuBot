from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from .config import load_settings
from .discord_bot import run_discord_bot
from .scanner import Scanner
from .types import ScanRequest, WatchUpdateRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = load_settings()
scanner = Scanner(settings)
scheduler = AsyncIOScheduler(timezone=settings.market_timezone)
discord_bot = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global discord_bot
    scheduler.add_job(
        scheduled_scan,
        CronTrigger.from_crontab(settings.market_open_cron, timezone=settings.market_timezone),
        id="market-open-scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    discord_bot = await run_discord_bot(settings, scanner)
    if settings.run_on_start:
        asyncio.create_task(scheduled_scan())
    yield
    if discord_bot:
        await discord_bot.close()
    scheduler.shutdown(wait=False)


app = FastAPI(title="KabuBot", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "scheduler_running": scheduler.running}


@app.get("/config")
async def config() -> dict:
    watch = scanner.watch.get()
    discord_private_ready = bool(
        settings.discord_bot_token
        and settings.discord_allowed_guild_id
        and settings.discord_allowed_channel_id
        and settings.discord_allowed_user_ids
    )
    return {
        "sector_query": watch.sector_query,
        "watch": watch.model_dump(mode="json"),
        "max_candidates": settings.max_candidates,
        "market_timezone": settings.market_timezone,
        "market_open_cron": settings.market_open_cron,
        "x_search_enabled": bool(settings.xai_api_key),
        "grok_model": settings.grok_model,
        "codex_runner_url": settings.codex_runner_url,
        "notifications": {
            "discord_bot": discord_private_ready,
            "discord_token_configured": bool(settings.discord_bot_token),
            "discord_private_guild": bool(settings.discord_allowed_guild_id),
            "discord_private_channel": bool(settings.discord_allowed_channel_id),
            "discord_allowed_users": len(settings.discord_allowed_user_ids),
            "discord": bool(settings.discord_webhook_url),
            "slack": bool(settings.slack_webhook_url),
        },
    }


@app.get("/auth/status")
async def auth_status() -> dict:
    return await scanner.codex.auth_status()


@app.post("/auth/start")
async def auth_start() -> dict:
    return await scanner.codex.auth_start()


@app.get("/watch")
async def watch() -> dict:
    return scanner.watch.get().model_dump(mode="json")


@app.post("/watch")
async def update_watch(request: WatchUpdateRequest) -> dict:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    result = scanner.watch.update_from_message(request.message, apply=request.apply)
    return result.model_dump(mode="json")


@app.post("/scan")
async def scan(request: ScanRequest) -> dict:
    report = await scanner.scan(
        sector_query=request.sector_query,
        symbols=request.symbols,
        max_candidates=request.max_candidates,
        notify=request.notify,
    )
    return report.model_dump(mode="json")


@app.get("/quotes/{symbols}")
async def quotes(symbols: str) -> dict:
    requested = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="symbols are required")
    signals = await scanner.quote(requested)
    return {"symbols": [signal.model_dump(mode="json") for signal in signals]}


@app.get("/reports/latest.md", response_class=PlainTextResponse)
async def latest_markdown() -> str:
    text = scanner.store.latest_markdown()
    if text is None:
        raise HTTPException(status_code=404, detail="no report yet")
    return text


@app.get("/reports/latest")
async def latest_json() -> dict:
    text = scanner.store.latest_json()
    if text is None:
        raise HTTPException(status_code=404, detail="no report yet")
    return json.loads(text)


async def scheduled_scan() -> None:
    try:
        await scanner.scan(notify=True)
    except Exception:
        logging.exception("scheduled scan failed")


def main() -> None:
    uvicorn.run("kabubot.main:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
