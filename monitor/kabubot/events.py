from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal

from .charts import serialized_render

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd
import yfinance as yf
from pydantic import BaseModel

from .sectors import sector_spec


class MarketEvent(BaseModel):
    symbol: str
    kind: Literal["earnings", "ex_dividend", "dividend_payment"]
    day: date
    end_day: date | None = None
    status: str = "scheduled (subject to change)"
    source: str = "Yahoo Finance / yfinance"

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.kind}|{self.day}|{self.end_day or ''}"


LABELS = {"earnings": "決算予定", "ex_dividend": "権利落ち日", "dividend_payment": "配当支払日"}
SHORT_LABELS = {"earnings": "E", "ex_dividend": "X", "dividend_payment": "D"}
COLORS = {"earnings": "#2563eb", "ex_dividend": "#b45309", "dividend_payment": "#047857"}


def parse_calendar(symbol: str, raw: dict) -> list[MarketEvent]:
    events = []
    for field, kind in [("Earnings Date", "earnings"), ("Ex-Dividend Date", "ex_dividend"),
                        ("Dividend Date", "dividend_payment")]:
        values = raw.get(field)
        values = values if isinstance(values, (list, tuple)) else [values]
        days = []
        for value in values:
            # Calendar dates are date-only exchange dates. Do not timezone-shift them.
            if value is None or isinstance(value, (int, float, bool)):
                continue
            try:
                stamp = pd.Timestamp(value)
                if not pd.isna(stamp):
                    days.append(stamp.date())
            except (TypeError, ValueError):
                continue
        if days:
            first, last = min(days), max(days)
            events.append(MarketEvent(symbol=symbol, kind=kind, day=first,
                                      end_day=last if last != first else None,
                                      status="estimated window" if last != first else "scheduled (subject to change)"))
    return events


def fetch_calendar(symbol: str) -> tuple[list[MarketEvent], str | None]:
    try:
        raw = yf.Ticker(symbol).calendar
        if not isinstance(raw, dict) or not raw:
            return [], f"{symbol}: イベント日程未取得（予定なしとは限りません）"
        events = parse_calendar(symbol, raw)
        return events, None if events else f"{symbol}: 日付未公表または未取得"
    except Exception as error:
        return [], f"{symbol}: イベント取得失敗: {error}"


def event_symbols(watch, cap: int, regions: tuple[str, ...] = ("us", "jp")) -> tuple[list[str], list[str]]:
    if watch.symbol_mode == "only":
        return list(dict.fromkeys(watch.symbols)), []
    spec = sector_spec(watch.sector_query)
    symbols = list(dict.fromkeys(watch.symbols + spec.default_tickers))
    warnings = []
    if not spec.yahoo_sector:
        return symbols, ["テーマのYahooセクター未解決。個別watchと既知のテーマ銘柄のみ対象です。"]
    try:
        from yfinance import EquityQuery
        if "software" in spec.keywords:
            query = EquityQuery("is-in", ["industry", "Software—Application", "Software—Infrastructure"])
        elif "semiconductor" in spec.keywords:
            query = EquityQuery("is-in", ["industry", "Semiconductors", "Semiconductor Equipment & Materials"])
        elif "ai" in spec.keywords:
            # AI is a theme rather than a Yahoo sector; do not include every tech stock.
            return symbols, ["AIテーマは既知のテーマ銘柄と個別watchを対象にします。"]
        else:
            query = EquityQuery("eq", ["sector", spec.yahoo_sector])
        if regions:
            region_query = EquityQuery("is-in", ["region", *regions]) if len(regions) > 1 else EquityQuery("eq", ["region", regions[0]])
            query = EquityQuery("and", [query, region_query])
        seen = set(symbols)
        for offset in range(0, cap, 250):
            size = min(250, cap - offset)
            response = yf.screen(query, offset=offset, size=size, sortField="ticker", sortAsc=True)
            if not isinstance(response, dict) or "quotes" not in response:
                raise ValueError("Yahoo screener returned no quotes field")
            quotes = response["quotes"]
            page = [str(q["symbol"]) for q in quotes if q.get("symbol")]
            new = [s for s in page if s not in seen]
            symbols.extend(new)
            seen.update(page)
            if len(quotes) < size or offset + size >= int(response.get("total", 10**9)):
                break
            if offset and not new:
                warnings.append("セクター取得ページが重複したため中断しました。対象は部分的です。")
                break
        else:
            warnings.append(f"セクター取得上限 {cap} 件に到達。全銘柄を網羅していません。")
    except Exception as error:
        warnings.append(f"セクター一覧取得失敗。取得済み・既知銘柄のみ対象: {error}")
    return list(dict.fromkeys(symbols)), warnings


class EventMonitor:
    def __init__(self, settings, watch, notifier) -> None:
        self.settings, self.watch, self.notifier = settings, watch, notifier
        self.root = settings.data_dir / "events"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.snapshot: dict | None = None

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.settings.market_timezone)).date()

    def _collect(self, today: date) -> dict:
        watch = self.watch.get()
        scope = json.dumps(watch.model_dump(mode="json"), sort_keys=True)
        symbols, warnings = event_symbols(watch, self.settings.event_max_symbols, self.settings.event_regions)
        events = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for items, warning in pool.map(fetch_calendar, symbols):
                events.extend(items)
                if warning:
                    warnings.append(warning)
        first = today.replace(day=1)
        # Retain observed past dates of this month when Yahoo rolls to next quarter.
        previous = _read_json(self.root / "latest.json", {})
        if previous.get("scope") == scope:
            events.extend(MarketEvent.model_validate(e) for e in previous.get("events", [])
                          if first <= date.fromisoformat(e["day"]) < today and e["symbol"] in symbols)
        unique = {e.key: e for e in events if (e.end_day or e.day) >= first}
        ordered = sorted(unique.values(), key=lambda e: (e.day, e.symbol, e.kind))
        snapshot = {"scope": scope, "sector_query": watch.sector_query, "symbols": symbols,
                    "updated_at": datetime.now(ZoneInfo(self.settings.market_timezone)).isoformat(),
                    "timezone": self.settings.market_timezone,
                    "events": [e.model_dump(mode="json") for e in ordered], "warnings": warnings}
        render_calendar(ordered, today, self.root / "calendar.png", len(symbols), len(warnings))
        _write_json(self.root / "latest.json", snapshot)
        return snapshot

    async def refresh(self, notify: bool = False, force: bool = False) -> dict:
        async with self.lock:
            today = self.today()
            watch_scope = json.dumps(self.watch.get().model_dump(mode="json"), sort_keys=True)
            if (force or not self.snapshot or self.snapshot.get("scope") != watch_scope
                    or self.snapshot["updated_at"][:10] != today.isoformat()):
                self.snapshot = await asyncio.to_thread(self._collect, today)
            if notify:
                await self._notify(self.snapshot, today)
            return self.snapshot

    async def _notify(self, snapshot: dict, today: date) -> None:
        sent = _read_json(self.root / "sent.json", {})
        events = [MarketEvent.model_validate(e) for e in snapshot["events"]]
        due = []
        keys = []
        for event in events:
            remaining = (event.day - today).days
            # Catch up after downtime or late publication, but never announce a past event.
            thresholds = [d for d in self.settings.event_alert_days if 0 < remaining <= d]
            if not thresholds:
                continue
            threshold = min(thresholds)
            key = f"{event.key}|{threshold}"
            if key not in sent:
                due.append(f"{event.symbol}: {LABELS[event.kind]} {event.day}"
                           f"{('〜' + str(event.end_day)) if event.end_day else ''}（{remaining}日前・予定）")
                keys.append(key)
        fingerprint = hashlib.sha256(json.dumps([snapshot["scope"], snapshot["events"],
                                                 snapshot["warnings"]], sort_keys=True).encode()).hexdigest()[:16]
        calendar_key = f"calendar|{today:%Y-%m}|{fingerprint}"
        if not due and calendar_key in sent:
            return
        text = f"KabuBot イベント予定（対象 {len(snapshot['symbols'])} 銘柄）\n"
        text += "\n".join(due) if due else "今月のイベントカレンダーを更新しました。"
        text += "\nE=決算予定 / X=権利落ち日 / D=配当支払日。日付は提供元の市場日付、変更の可能性あり。"
        if snapshot["warnings"]:
            text += f"\n未取得・部分取得: {len(snapshot['warnings'])} 件。予定なしとは限りません。"
        delivered = await self.notifier.send_message(text, [self.root / "calendar.png"])
        if delivered:
            for key in keys + [calendar_key]:
                sent[key] = today.isoformat()
            cutoff = (today - timedelta(days=90)).isoformat()
            _write_json(self.root / "sent.json", {k: v for k, v in sent.items() if v >= cutoff})


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@serialized_render
def render_calendar(events: list[MarketEvent], today: date, path: Path, symbol_count: int, warning_count: int) -> None:
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(today.year, today.month)
    entries = {}
    for week in weeks:
        for day in week:
            entries[day] = [e for e in events if e.day <= day <= (e.end_day or e.day)]
    max_lines = max([len(v) for k, v in entries.items() if k.month == today.month] or [0])
    # Increase cell height instead of dropping busy-day events.
    height = max(1.5, 0.2 * (max_lines + 2))
    fig, ax = plt.subplots(figsize=(16, height * len(weeks) + 1.8), dpi=130)
    ax.set_xlim(0, 7)
    ax.set_ylim(0, len(weeks))
    ax.axis("off")
    for row, week in enumerate(weeks):
        for col, day in enumerate(week):
            y = len(weeks) - row - 1
            current = day.month == today.month
            color = "#dbeafe" if day == today else "white" if current else "#f1f5f9"
            ax.add_patch(Rectangle((col, y), 1, 1, facecolor=color, edgecolor="#cbd5e1"))
            ax.text(col + .04, y + .95, str(day.day), va="top", fontsize=10, weight="bold")
            if current:
                for index, event in enumerate(entries[day]):
                    ax.text(col + .04, y + .78 - index * (.7 / max(4, max_lines)),
                            f"{SHORT_LABELS[event.kind]} {event.symbol}{' ~' if event.end_day else ''}",
                            fontsize=8, va="top", color=COLORS[event.kind])
    for col, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
        ax.text(col + .5, len(weeks) + .08, day, ha="center", fontsize=11)
    fig.suptitle(f"{today:%B %Y} | Earnings & dividends", fontsize=19, weight="bold", y=.98)
    fig.text(.04, .025, f"E Earnings (scheduled)   X Ex-dividend   D Dividend payment   ~ Estimated window\n"
             f"Yahoo Finance | {symbol_count} symbols | {warning_count} data warnings | Updated {today} | Exchange calendar dates\n"
             "Dates may change. Blank cells mean no retrieved events; missing dates are not inferred.", fontsize=9)
    fig.subplots_adjust(top=.89, bottom=.13, left=.035, right=.985)
    try:
        temporary = path.with_suffix(".tmp.png")
        fig.savefig(temporary)
        temporary.replace(path)
    finally:
        plt.close(fig)
