from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
from collections.abc import Awaitable, Callable
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
    name: str | None = None
    kind: Literal["earnings", "ex_dividend", "dividend_payment"]
    day: date
    end_day: date | None = None
    status: str = "scheduled (subject to change)"
    source: str = "Yahoo Finance / yfinance"

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.kind}|{self.day}|{self.end_day or ''}"


LABELS = {"earnings": "決算予定", "ex_dividend": "権利落ち日", "dividend_payment": "配当支払日"}
COLORS = {"earnings": "#2563eb", "ex_dividend": "#b45309", "dividend_payment": "#047857"}
CALENDAR_DAYS = 7
EVENT_ANALYSIS_BATCH_SIZE = 10


def company_label(event: MarketEvent) -> str:
    return f"{event.name or '社名未取得'} ({event.symbol})"


def event_label(event: MarketEvent) -> str:
    period = f"{event.day}〜{event.end_day}（予定期間）" if event.end_day else f"{event.day}（予定）"
    return f"{company_label(event)}: {LABELS[event.kind]} {period}"


def upcoming_events(snapshot: dict, today: date) -> list[MarketEvent]:
    last = today + timedelta(days=CALENDAR_DAYS - 1)
    return [event for item in snapshot["events"]
            if (event := MarketEvent.model_validate(item)).day <= last
            and (event.end_day or event.day) >= today]


def analysis_batches(events: list[MarketEvent]) -> list[list[MarketEvent]]:
    symbols = list(dict.fromkeys(event.symbol for event in events))
    return [[event for event in events if event.symbol in symbols[start:start + EVENT_ANALYSIS_BATCH_SIZE]]
            for start in range(0, len(symbols), EVENT_ANALYSIS_BATCH_SIZE)]


def calendar_caption(snapshot: dict, today: date) -> str:
    last = today + timedelta(days=CALENDAR_DAYS - 1)
    text = f"KabuBot 7日間のイベント予定 {today}〜{last}\n"
    text += f"対象 {len(snapshot['symbols'])} 銘柄。決算予定・権利落ち日・配当支払日を表示。"
    text += "\n日付は提供元の市場日付で、変更される場合があります。"
    if snapshot["warnings"]:
        text += f"\n未取得・部分取得: {len(snapshot['warnings'])} 件。予定なしとは限りません。"
    return text


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


def fetch_company_name(symbol: str) -> str | None:
    try:
        info = yf.Ticker(symbol).get_info() or {}
        return info.get("longName") or info.get("shortName") or None
    except Exception:
        return None


def event_symbols(watch, cap: int, regions: tuple[str, ...] = ("us", "jp"),
                  names: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
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
            if names is not None:
                for quote in quotes:
                    if quote.get("symbol") and quote.get("longName"):
                        names[str(quote["symbol"])] = str(quote["longName"])
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
    def __init__(self, settings, watch, notifier,
                 analyze: Callable[[dict, list[MarketEvent]], Awaitable[tuple[str, list[Path]]]] | None = None) -> None:
        self.settings, self.watch, self.notifier = settings, watch, notifier
        self.analyze = analyze
        self.root = settings.data_dir / "events"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.snapshot: dict | None = None

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.settings.market_timezone)).date()

    def _collect(self, today: date) -> dict:
        watch = self.watch.get()
        scope = json.dumps(watch.model_dump(mode="json"), sort_keys=True)
        names = _read_json(self.root / "names.json", {})
        symbols, warnings = event_symbols(watch, self.settings.event_max_symbols, self.settings.event_regions, names)
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
        last = today + timedelta(days=CALENDAR_DAYS - 1)
        visible = [e for e in ordered if e.day <= last and (e.end_day or e.day) >= today]
        missing = list(dict.fromkeys(e.symbol for e in visible if e.symbol not in names and not e.name))
        with ThreadPoolExecutor(max_workers=4) as pool:
            for symbol, name in zip(missing, pool.map(fetch_company_name, missing)):
                if name:
                    names[symbol] = name
                else:
                    warnings.append(f"{symbol}: 正式社名未取得")
        for event in ordered:
            event.name = names.get(event.symbol) or event.name
        _write_json(self.root / "names.json", names)
        snapshot = {"scope": scope, "sector_query": watch.sector_query, "symbols": symbols,
                    "updated_at": datetime.now(ZoneInfo(self.settings.market_timezone)).isoformat(),
                    "timezone": self.settings.market_timezone,
                    "range_start": today.isoformat(), "range_end": last.isoformat(),
                    "events": [e.model_dump(mode="json") for e in ordered], "warnings": warnings}
        paths = render_calendar(visible, today, self.root / "calendar.png", len(symbols), len(warnings))
        snapshot["calendar_files"] = [path.name for path in paths or [self.root / "calendar.png"]]
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
        events = upcoming_events(snapshot, today)
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
                due.append(f"{event_label(event)}・{remaining}日前")
                keys.append(key)
        fingerprint = _fingerprint([snapshot["scope"], [e.model_dump(mode="json") for e in events],
                                    snapshot["warnings"]])
        calendar_key = f"calendar-7d|{today}|{fingerprint}"

        def mark_sent(delivered_keys):
            for key in delivered_keys:
                sent[key] = today.isoformat()
            cutoff = (today - timedelta(days=90)).isoformat()
            _write_json(self.root / "sent.json", {k: v for k, v in sent.items() if v >= cutoff})

        if due or calendar_key not in sent:
            text = calendar_caption(snapshot, today)
            if due:
                text += "\n\n" + "\n".join(due)
            if not await self.notifier.send_message(text, self.calendar_paths(snapshot)):
                return
            mark_sent(keys + [calendar_key])
        # Record each successful batch separately: a later analysis failure must
        # not suppress retrying that batch, or resend completed batches.
        if self.analyze:
            for batch in analysis_batches(events):
                key = f"event-analysis|{today}|" + _fingerprint(
                    [snapshot["scope"], [e.model_dump(mode="json") for e in batch]])
                if key in sent:
                    continue
                text, paths = await self.analyze(snapshot, batch)
                if not await self.notifier.send_message(text, paths):
                    return
                mark_sent([key])

    def calendar_paths(self, snapshot: dict) -> list[Path]:
        return [self.root / name for name in snapshot.get("calendar_files", ["calendar.png"])]


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _wrap_display(text: str, width: int = 100) -> str:
    """Wrap full labels without truncation, accounting for wide Japanese glyphs."""
    lines, line, used = [], "", 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if line and used + size > width:
            boundary = line.rfind(" ") + 1
            if boundary < len(line) // 2:
                boundary = len(line)
            lines.append(line[:boundary])
            line = line[boundary:]
            used = sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in line)
        line += char
        used += size
    if line:
        lines.append(line)
    return "\n".join(lines)


@serialized_render
def render_calendar(events: list[MarketEvent], today: date, path: Path,
                    symbol_count: int, warning_count: int) -> list[Path]:
    """Render exactly seven dates; paginate busy weeks instead of hiding names."""
    pages, rows, used = [], [], 0.0
    for offset in range(CALENDAR_DAYS):
        day = today + timedelta(days=offset)
        daily = sorted([e for e in events if e.day <= day <= (e.end_day or e.day)],
                       key=lambda e: (e.kind, e.name or e.symbol))
        weekday = "月火水木金土日"[day.weekday()]
        heading = f"{day:%m/%d}（{weekday}）" + ("  今日" if day == today else "")
        if used + 1.2 > 17:
            pages.append(rows)
            rows, used = [], 0.0
        rows.append(("day", heading, None, .48))
        used += .48
        for event in daily:
            label = _wrap_display(company_label(event))
            height = .26 * (label.count("\n") + 1) + .32
            if used + height > 17:
                pages.append(rows)
                rows = [("day", heading + "（続き）", None, .48)]
                used = .48
            rows.append(("event", label, event, height))
            used += height
        if not daily:
            rows.append(("empty", "取得済みの予定なし", None, .38))
            used += .38
        rows.append(("space", "", None, .12))
        used += .12
    if rows:
        pages.append(rows)

    paths = []
    last = today + timedelta(days=CALENDAR_DAYS - 1)
    for page_index, page in enumerate(pages):
        body_height = sum(row[3] for row in page)
        total_height = body_height + 2.0
        fig = plt.figure(figsize=(13, total_height), dpi=140, facecolor="white")
        ax = fig.add_axes((.04, 1.0 / total_height, .92, body_height / total_height))
        ax.set_xlim(0, 12)
        ax.set_ylim(0, body_height)
        ax.axis("off")
        fig.text(.04, 1 - .35 / total_height, "決算・配当カレンダー｜7日間",
                 fontsize=20, weight="bold", va="top", color="#0f172a")
        fig.text(.04, 1 - .75 / total_height,
                 f"{today} 〜 {last}    |    {page_index + 1}/{len(pages)} ページ",
                 fontsize=11, va="top", color="#475569")
        y = body_height
        for kind, label, event, height in page:
            if kind == "day":
                ax.add_patch(Rectangle((0, y - height + .04), 12, height - .06,
                                      facecolor="#e2e8f0", edgecolor="none"))
                ax.text(.14, y - .09, label, fontsize=12, weight="bold", va="top", color="#0f172a")
            elif kind == "event":
                ax.add_patch(Rectangle((.1, y - height + .1), .045, height - .18,
                                      facecolor=COLORS[event.kind], edgecolor="none"))
                ax.text(.28, y - .045, label, fontsize=12, va="top", color="#0f172a", linespacing=1.25)
                detail = LABELS[event.kind]
                if event.end_day:
                    detail += f"  |  予定期間 {event.day}〜{event.end_day}"
                ax.text(.28, y - height + .22, detail, fontsize=10, va="top", color=COLORS[event.kind])
            elif kind == "empty":
                ax.text(.28, y - .05, label, fontsize=10, va="top", color="#64748b")
            y -= height
        fig.text(.04, .72 / total_height,
                 f"Yahoo Finance  |  対象 {symbol_count} 銘柄  |  未取得・部分取得 {warning_count} 件",
                 fontsize=9, color="#475569")
        fig.text(.04, .43 / total_height,
                 "提供元の市場日付。日程は変更される場合があります。未取得の日程は「予定なし」とは限りません。",
                 fontsize=9, color="#475569")
        output = path if page_index == 0 else path.with_name(f"{path.stem}-{page_index + 1}{path.suffix}")
        try:
            temporary = output.with_suffix(".tmp.png")
            fig.savefig(temporary)
            temporary.replace(output)
            paths.append(output)
        finally:
            plt.close(fig)
    return paths
