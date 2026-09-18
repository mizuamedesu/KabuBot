from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from kabubot.events import EventMonitor, MarketEvent, event_symbols, parse_calendar, render_calendar
from kabubot.ml_signal import enrich_anomaly_scores
from kabubot.types import PriceSignal, WatchState
from kabubot.yfinance_skill import _return_zscore
from kabubot.charts import ChartRenderer


def test_calendar_parses_date_window_without_inventing_dates():
    events = parse_calendar("TEST", {"Earnings Date": [date(2026, 10, 1), date(2026, 10, 3)],
                                    "Ex-Dividend Date": "2026-09-30", "Dividend Date": None})
    assert len(events) == 2
    assert events[0].day == date(2026, 10, 1)
    assert events[0].end_day == date(2026, 10, 3)
    assert events[0].status == "estimated window"
    assert events[1].kind == "ex_dividend"
    assert parse_calendar("TEST", {"Earnings Date": [None, "bad", float("nan")]}) == []


def test_sector_unwatched_symbols_are_paged_and_only_mode_is_respected(monkeypatch):
    calls = []
    def screen(query, **kwargs):
        calls.append(kwargs)
        assert "industry" in str(query)
        offset = kwargs["offset"]
        return {"quotes": [{"symbol": f"S{i}"} for i in range(offset, min(offset + 250, 252))], "total": 252}
    monkeypatch.setattr("kabubot.events.yf.screen", screen)
    watch = WatchState(sector_query="ソフトウェア", symbols=["CUSTOM"])
    symbols, warnings = event_symbols(watch, 2000)
    assert "S251" in symbols and "CUSTOM" in symbols and "MSFT" in symbols
    assert len(calls) == 2 and not warnings
    watch.symbol_mode = "only"
    assert event_symbols(watch, 2000) == (["CUSTOM"], [])
    assert len(calls) == 2


def test_sector_failure_and_limit_are_visible(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr("kabubot.events.yf.screen", fail)
    symbols, warnings = event_symbols(WatchState(sector_query="ソフトウェア"), 1)
    assert "MSFT" in symbols and "offline" in warnings[0]
    monkeypatch.setattr("kabubot.events.yf.screen", lambda *a, **k: {"quotes": [{"symbol": "NEW"}], "total": 10})
    symbols, warnings = event_symbols(WatchState(sector_query="ソフトウェア"), 1)
    assert "NEW" in symbols and "上限" in warnings[0]


class Delivery:
    def __init__(self):
        self.messages = []
        self.result = True
        self.fail = False

    async def send_message(self, text, paths):
        if self.fail:
            raise RuntimeError("delivery failed")
        self.messages.append(text)
        return self.result


def make_monitor(tmp_path, delivery):
    settings = SimpleNamespace(data_dir=tmp_path, market_timezone="Asia/Tokyo",
                               event_max_symbols=2000, event_regions=("us", "jp"), event_alert_days=(7, 3, 1))
    watch = SimpleNamespace(get=lambda: WatchState(sector_query="ソフトウェア"))
    return EventMonitor(settings, watch, delivery)


def snapshot(events):
    return {"scope": "software", "events": [e.model_dump(mode="json") for e in events],
            "symbols": ["UNWATCHED"], "warnings": []}


def test_alerts_cross_month_catch_up_and_survive_restart(tmp_path):
    delivery = Delivery()
    monitor = make_monitor(tmp_path, delivery)
    data = snapshot([MarketEvent(symbol="UNWATCHED", kind="earnings", day=date(2026, 10, 1))])
    asyncio.run(monitor._notify(data, date(2026, 9, 25)))
    assert "6日前" in delivery.messages[0]
    assert "UNWATCHED" in delivery.messages[0]
    restarted = make_monitor(tmp_path, delivery)
    asyncio.run(restarted._notify(data, date(2026, 9, 26)))
    assert len(delivery.messages) == 1
    asyncio.run(restarted._notify(data, date(2026, 9, 28)))
    assert "3日前" in delivery.messages[-1]
    asyncio.run(restarted._notify(data, date(2026, 9, 30)))
    assert "1日前" in delivery.messages[-1]
    assert len(delivery.messages) == 3


def test_failed_and_unconfigured_delivery_is_not_marked_sent(tmp_path):
    delivery = Delivery()
    delivery.result = False
    monitor = make_monitor(tmp_path, delivery)
    data = snapshot([MarketEvent(symbol="UNWATCHED", kind="ex_dividend", day=date(2026, 9, 19))])
    asyncio.run(monitor._notify(data, date(2026, 9, 18)))
    assert not (monitor.root / "sent.json").exists()
    delivery.fail = True
    with pytest.raises(RuntimeError):
        asyncio.run(monitor._notify(data, date(2026, 9, 18)))
    assert not (monitor.root / "sent.json").exists()
    delivery.fail = False
    delivery.result = True
    asyncio.run(monitor._notify(data, date(2026, 9, 18)))
    assert (monitor.root / "sent.json").exists()


def test_rescheduled_events_alert_again_and_past_events_do_not(tmp_path):
    delivery = Delivery()
    monitor = make_monitor(tmp_path, delivery)
    today = date(2026, 9, 18)
    event = MarketEvent(symbol="UNWATCHED", kind="dividend_payment", day=today + timedelta(days=2))
    asyncio.run(monitor._notify(snapshot([event]), today))
    event.day += timedelta(days=1)
    asyncio.run(monitor._notify(snapshot([event]), today))
    assert len(delivery.messages) == 2
    event.day = today - timedelta(days=1)
    asyncio.run(monitor._notify(snapshot([event]), today))
    assert "日前" not in delivery.messages[-1]


def test_refresh_keeps_month_history_but_removes_replaced_future_dates(tmp_path, monkeypatch):
    delivery = Delivery()
    monitor = make_monitor(tmp_path, delivery)
    monkeypatch.setattr("kabubot.events.event_symbols", lambda *args: (["TEST"], []))
    monkeypatch.setattr("kabubot.events.render_calendar", lambda *args: None)
    items = [MarketEvent(symbol="TEST", kind="earnings", day=date(2026, 9, 10)),
             MarketEvent(symbol="TEST", kind="ex_dividend", day=date(2026, 9, 20))]
    monkeypatch.setattr("kabubot.events.fetch_calendar", lambda symbol: (items, None))
    monitor._collect(date(2026, 9, 9))
    items = [MarketEvent(symbol="TEST", kind="ex_dividend", day=date(2026, 9, 22))]
    result = monitor._collect(date(2026, 9, 18))
    assert [e["day"] for e in result["events"]] == ["2026-09-10", "2026-09-22"]


def test_total_return_z_uses_previous_observations_and_adjusts_dividends():
    returns = np.array([.01, -.01] * 10 + [-.10])
    close = pd.Series(100 * np.cumprod(np.r_[1, 1 + returns]))
    dividends = pd.Series(0., index=close.index)
    expected = (-.10 - returns[:-1].mean()) / returns[:-1].std(ddof=1)
    assert _return_zscore(close, dividends) == pytest.approx(expected, abs=.0001)
    dividends.iloc[-1] = close.iloc[-2] * .10
    assert _return_zscore(close, dividends) == pytest.approx(0, abs=.0001)
    assert _return_zscore(pd.Series([100.] * 25), pd.Series(dtype=float)) is None


def test_peer_comparison_excludes_self_other_dates_and_currencies():
    base = dict(as_of_date="2026-09-18", industry="Software", original_currency="USD")
    signals = [PriceSignal(symbol="A", day_change_pct=-5, **base),
               PriceSignal(symbol="B", day_change_pct=1, **base),
               PriceSignal(symbol="C", day_change_pct=3, **base),
               PriceSignal(symbol="STALE", day_change_pct=-90, **{**base, "as_of_date": "2026-09-17"}),
               PriceSignal(symbol="JP", day_change_pct=-90, **{**base, "original_currency": "JPY"})]
    result = {s.symbol: s for s in enrich_anomaly_scores(signals)}
    assert result["A"].peer_count == 2
    assert result["A"].peer_day_median_pct == 2
    assert result["A"].peer_day_difference_pp == -7
    assert result["JP"].peer_count == 0


def test_render_metrics_and_busy_calendar(tmp_path, monkeypatch):
    index = pd.bdate_range("2026-06-20", periods=65)
    frame = pd.DataFrame({"Close": 100 + np.sin(np.arange(65)) * 2 + np.arange(65) * .1,
                          "Dividends": 0., "Volume": 1000}, index=index)
    frame.loc[index[-1], "Close"] = 85
    monkeypatch.setattr("kabubot.charts.yf.download", lambda **kwargs: frame)
    renderer = ChartRenderer(tmp_path)
    signal = PriceSignal(symbol="TEST", name="Test Corporation", currency="USD", as_of_date="2026-09-18",
                         day_change_pct=-12.3, return_zscore_20d=-4.5, peer_count=8, peer_day_median_pct=-1,
                         peer_day_difference_pp=-11.3, statistical_score=91, volume_ratio_20d=3.5)
    assert renderer.render_signal_charts([signal], tmp_path) == [tmp_path / "TEST.png"]
    events = [MarketEvent(symbol=f"TEST{i}", kind="earnings", day=date(2026, 9, 18)) for i in range(12)]
    events += [MarketEvent(symbol="DIV", kind="ex_dividend", day=date(2026, 9, 21)),
               MarketEvent(symbol="PAY", kind="dividend_payment", day=date(2026, 9, 30)),
               MarketEvent(symbol="RANGE", kind="earnings", day=date(2026, 9, 23), end_day=date(2026, 9, 25))]
    render_calendar(events, date(2026, 9, 18), tmp_path / "calendar.png", 16, 2)
    assert (tmp_path / "calendar.png").stat().st_size > 10000
