from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .types import WatchState, WatchUpdateResult


CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])\$?([A-Z]{1,6}(?:\.[A-Z]{1,3})?|\d{4}\.T)(?![A-Za-z0-9])")
IGNORED_TICKERS = {
    "AI",
    "API",
    "CEO",
    "CFO",
    "GPU",
    "IPO",
    "LLM",
    "QUERY",
    "SECTOR",
    "SaaS".upper(),
    "USA",
    "USD",
    "VM",
    "X",
}


class WatchStore:
    def __init__(self, data_dir: Path, default_sector_query: str, default_symbols: list[str]) -> None:
        self.path = data_dir / "watch.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_state = WatchState(
            sector_query=default_sector_query,
            symbols=_unique_symbols(default_symbols),
            symbol_mode="augment",
            notes=[],
        )

    def get(self) -> WatchState:
        if not self.path.exists():
            return self.default_state
        try:
            return WatchState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self.default_state

    def update_from_message(self, message: str, apply: bool = True) -> WatchUpdateResult:
        current = self.get()
        next_state, actions = apply_watch_message(current, message)
        if apply:
            self.save(next_state)
        reply = _reply(next_state, actions, applied=apply)
        return WatchUpdateResult(reply=reply, actions=actions, state=next_state)

    def save(self, state: WatchState) -> None:
        state = state.model_copy(update={"updated_at": datetime.now(UTC)})
        self.path.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def apply_watch_message(current: WatchState, message: str) -> tuple[WatchState, list[str]]:
    text = message.strip()
    lowered = text.lower()
    symbols = _extract_symbols(text)
    next_state = current.model_copy(deep=True)
    actions: list[str] = []

    if _wants_reset(lowered):
        next_state = next_state.model_copy(update={"symbols": [], "symbol_mode": "augment"})
        actions.append("個別watchをクリア")

    if symbols and _wants_remove(lowered):
        remaining = [symbol for symbol in next_state.symbols if symbol not in symbols]
        removed = sorted(set(next_state.symbols) - set(remaining))
        next_state = next_state.model_copy(update={"symbols": remaining})
        if removed:
            actions.append("銘柄を削除: " + ", ".join(removed))

    elif symbols:
        mode = "only" if _wants_only(lowered) else next_state.symbol_mode
        merged = symbols if mode == "only" else _unique_symbols(next_state.symbols + symbols)
        next_state = next_state.model_copy(update={"symbols": merged, "symbol_mode": mode})
        label = "銘柄を限定" if mode == "only" else "銘柄を追加"
        actions.append(f"{label}: " + ", ".join(symbols))

    sector = _extract_sector_query(text, symbols)
    if sector:
        next_state = next_state.model_copy(update={"sector_query": sector})
        actions.append(f"テーマを更新: {sector}")

    if _wants_sector_only(lowered):
        next_state = next_state.model_copy(update={"symbols": [], "symbol_mode": "augment"})
        actions.append("セクター探索のみへ変更")

    if _wants_augment(lowered) and next_state.symbol_mode == "only":
        next_state = next_state.model_copy(update={"symbol_mode": "augment"})
        actions.append("銘柄限定を解除")

    if not actions and text:
        next_state = next_state.model_copy(update={"sector_query": text})
        actions.append(f"テーマを自然文として更新: {text}")

    return next_state, actions


def _extract_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for match in CASHTAG_RE.finditer(text):
        symbol = match.group(1).upper()
        if symbol in IGNORED_TICKERS:
            continue
        symbols.append(symbol)
    return _unique_symbols(symbols)


def _extract_sector_query(text: str, symbols: list[str]) -> str | None:
    stripped = text.strip()
    env_match = re.search(r"SECTOR_QUERY\s*=\s*(.+)", stripped, re.I)
    if env_match:
        return _clean_sector(env_match.group(1))

    marker_match = re.search(r"(?:テーマ|セクター|sector|theme)\s*(?:は|を|:|=)\s*(.+)", stripped, re.I)
    if marker_match:
        return _clean_sector(marker_match.group(1))

    known = [
        "ソフトウェア",
        "半導体",
        "AIインフラ",
        "AIソフトウェア",
        "クラウド",
        "SaaS",
        "ヘルスケア",
        "バイオ",
        "エネルギー",
    ]
    for item in known:
        if item.lower() in stripped.lower():
            return item

    if not symbols and _looks_like_theme(stripped):
        return _clean_sector(stripped)
    return None


def _clean_sector(value: str) -> str:
    cleaned = value.strip().strip("。.,、")
    cleaned = re.sub(r"(で|を)?(見て|監視して|追って|watchして|見る|探して)$", "", cleaned, flags=re.I).strip()
    return cleaned or value.strip()


def _looks_like_theme(text: str) -> bool:
    if not text or len(text) > 80:
        return False
    keywords = [
        "ソフト",
        "software",
        "saas",
        "クラウド",
        "ai",
        "llm",
        "半導体",
        "ヘルス",
        "バイオ",
        "エネルギー",
        "急落",
        "暴落",
        "煽り",
    ]
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def _wants_reset(lowered: str) -> bool:
    return any(term in lowered for term in ["watchlist clear", "watch clear", "クリア", "リセット", "全部外", "固定銘柄はいらない"])


def _wants_remove(lowered: str) -> bool:
    return any(term in lowered for term in ["外して", "消して", "削除", "除外", "remove", "unwatch"])


def _wants_only(lowered: str) -> bool:
    return any(term in lowered for term in ["だけ", "限定", "only", "固定", "この銘柄"])


def _wants_augment(lowered: str) -> bool:
    return any(term in lowered for term in ["限定解除", "広げて", "セクターも", "候補も", "augment"])


def _wants_sector_only(lowered: str) -> bool:
    return any(term in lowered for term in ["セクターだけ", "テーマだけ", "個別なし", "銘柄固定なし", "watchは自然言語"])


def _unique_symbols(symbols: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        cleaned = symbol.strip().upper().removeprefix("$")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _reply(state: WatchState, actions: list[str], applied: bool) -> str:
    prefix = "反映しました。" if applied else "プレビューです。"
    symbol_text = "なし" if not state.symbols else ", ".join(state.symbols)
    mode = "限定" if state.symbol_mode == "only" else "セクター候補に追加"
    action_text = " / ".join(actions) if actions else "変更なし"
    return (
        f"{prefix} {action_text}\n"
        f"現在のテーマ: {state.sector_query}\n"
        f"個別watch: {symbol_text} ({mode})"
    )
