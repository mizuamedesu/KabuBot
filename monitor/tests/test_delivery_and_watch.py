from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kabubot.discord_bot import _recent_removal_symbols, _send_chart_files
from kabubot.notifier import _post_discord_files_payload
from kabubot.scanner import ANALYSIS_SIGNAL_LIMIT, _compose_report_signals
from kabubot.types import PriceSignal
from kabubot.types import WatchState
from kabubot.watch import apply_watch_message


class _Response:
    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    async def post(self, url, **kwargs):
        self.posts.append(
            {
                "url": url,
                "file_count": len(kwargs["files"]),
                "payload": json.loads(kwargs["data"]["payload_json"]),
            }
        )
        return _Response()


class _Channel:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.messages.append(
            {
                "content": kwargs["content"],
                "file_count": len(kwargs["files"]),
            }
        )


def test_discord_payload_splits_more_than_ten_files(tmp_path: Path) -> None:
    paths = []
    for index in range(23):
        path = tmp_path / f"{index}.png"
        path.write_bytes(b"png")
        paths.append(path)
    client = _Client()

    asyncio.run(_post_discord_files_payload(client, "https://example.invalid", None, paths))

    assert [post["file_count"] for post in client.posts] == [10, 10, 3]
    assert client.posts[0]["payload"]["content"].endswith("1-10/23")
    assert client.posts[-1]["payload"]["content"].endswith("21-23/23")


def test_discord_bot_splits_more_than_ten_files(tmp_path: Path) -> None:
    paths = []
    for index in range(21):
        path = tmp_path / f"{index}.png"
        path.write_bytes(b"png")
        paths.append(path)
    channel = _Channel()

    asyncio.run(_send_chart_files(channel, paths))

    assert [message["file_count"] for message in channel.messages] == [10, 10, 1]
    assert channel.messages[-1]["content"].endswith("21-21/21")


def test_report_composition_keeps_more_than_ten_registered_symbols() -> None:
    watch_symbols = [f"T{index}" for index in range(13)]
    signals = [PriceSignal(symbol=symbol, anomaly_score=float(index)) for index, symbol in enumerate(watch_symbols)]
    delivery_limit = max(ANALYSIS_SIGNAL_LIMIT, len(watch_symbols))

    result = _compose_report_signals(signals, watch_symbols, delivery_limit)

    assert [signal.symbol for signal in result] == watch_symbols


def test_augmented_watch_symbol_can_be_removed() -> None:
    current = WatchState(
        sector_query="ソフトウェア",
        symbols=["MSFT", "CRM", "7203.T"],
        recently_added_symbols=["CRM", "7203.T"],
        symbol_mode="augment",
    )

    state, actions = apply_watch_message(current, "CRMを削除")

    assert state.symbols == ["MSFT", "7203.T"]
    assert state.recently_added_symbols == ["7203.T"]
    assert actions == ["銘柄を削除: CRM"]


def test_missing_watch_symbol_is_reported_without_readding_it() -> None:
    current = WatchState(
        sector_query="ソフトウェア",
        symbols=["MSFT"],
        symbol_mode="augment",
    )

    state, actions = apply_watch_message(current, "CRMを削除")

    assert state.symbols == ["MSFT"]
    assert actions == ["削除対象は個別watchに未登録: CRM"]


def test_recently_added_symbols_support_ambiguous_removal() -> None:
    watch = WatchState(
        sector_query="ソフトウェア",
        symbols=["MSFT", "CRM", "7203.T"],
        recently_added_symbols=["CRM", "7203.T"],
        symbol_mode="augment",
    )

    symbols = _recent_removal_symbols("さっき追加したやつを消して", watch)
    state, actions = apply_watch_message(
        watch,
        "さっき追加したやつを消して",
        resolved_symbols=symbols,
        extract_text_symbols=False,
    )

    assert symbols == ["CRM", "7203.T"]
    assert state.symbols == ["MSFT"]
    assert state.recently_added_symbols == []
    assert actions == ["銘柄を削除: 7203.T, CRM"]


def test_ambiguous_software_sector_text_is_registered() -> None:
    current = WatchState(
        sector_query="半導体",
        symbols=[],
        symbol_mode="augment",
    )

    state, actions = apply_watch_message(current, "ソフトウェア系を監視して")

    assert state.sector_query == "ソフトウェア"
    assert actions == ["テーマを更新: ソフトウェア"]
