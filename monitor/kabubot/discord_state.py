from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class DiscordAuthState(BaseModel):
    owner_user_id: str | None = None
    owner_username: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    authenticated_at: datetime | None = None
    pending_user_id: str | None = None
    pending_channel_id: str | None = None
    pending_started_at: datetime | None = None
    verification_uri: str | None = None
    user_code: str | None = None


class DiscordAuthStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "discord_auth.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get(self) -> DiscordAuthState:
        if not self.path.exists():
            return DiscordAuthState()
        try:
            return DiscordAuthState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except Exception:
            return DiscordAuthState()

    def is_authorized(self, user_id: int | str) -> bool:
        state = self.get()
        return state.owner_user_id is not None and state.owner_user_id == str(user_id)

    def has_owner(self) -> bool:
        return self.get().owner_user_id is not None

    def save_pending(
        self,
        *,
        user_id: int | str,
        channel_id: int | str,
        verification_uri: str | None,
        user_code: str | None,
    ) -> DiscordAuthState:
        state = self.get().model_copy(update={
            "pending_user_id": str(user_id),
            "pending_channel_id": str(channel_id),
            "pending_started_at": datetime.now(UTC),
            "verification_uri": verification_uri,
            "user_code": user_code,
        })
        self._save(state)
        return state

    def activate(
        self,
        *,
        user_id: int | str,
        username: str,
        channel_id: int | str,
        channel_name: str | None,
        guild_id: int | str | None,
    ) -> DiscordAuthState:
        state = self.get().model_copy(update={
            "owner_user_id": str(user_id),
            "owner_username": username,
            "guild_id": str(guild_id) if guild_id is not None else None,
            "channel_id": str(channel_id),
            "channel_name": channel_name,
            "authenticated_at": datetime.now(UTC),
            "pending_user_id": None,
            "pending_channel_id": None,
            "pending_started_at": None,
            "verification_uri": None,
            "user_code": None,
        })
        self._save(state)
        return state

    def bind_channel(
        self,
        *,
        channel_id: int | str,
        channel_name: str | None,
        guild_id: int | str | None,
    ) -> DiscordAuthState:
        state = self.get().model_copy(update={
            "guild_id": str(guild_id) if guild_id is not None else None,
            "channel_id": str(channel_id),
            "channel_name": channel_name,
        })
        self._save(state)
        return state

    def _save(self, state: DiscordAuthState) -> None:
        self.path.write_text(state.model_dump_json(indent=2), encoding="utf-8")

