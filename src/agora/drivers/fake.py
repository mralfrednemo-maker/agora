from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.drivers.base import Driver, DriverError, DriverReply


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


@dataclass(slots=True)
class FakeDriver(Driver):
    id: str
    display_name: str
    replies: list[str] = field(default_factory=list)
    replies_by_room: dict[str, list[str]] = field(default_factory=dict)
    cycle: bool = True
    token_ceiling: int = 20_000

    def __post_init__(self) -> None:
        self.kind = "fake"
        self._state_root = Path("C:/Users/chris/PROJECTS/agora/data/driver-state") / self.id
        self._sessions_dir = self._state_root / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._indices: dict[str, int] = {}
        self._active_sessions: set[str] = set()
        self._bootstrap_reply: dict[str, str] = {}
        self._session_ids: dict[str, str] = {}
        self._rehydrate_sessions()

    def _session_file(self, room_id: str) -> Path:
        return self._sessions_dir / f"{room_id}.json"

    def _persist_session(self, room_id: str, session_id: str) -> None:
        _atomic_write_json(
            self._session_file(room_id),
            {"session_id": session_id, "created_at": _utc_now_iso(), "driver_kind": self.kind},
        )

    def _rehydrate_sessions(self) -> None:
        for file_path in self._sessions_dir.glob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            session_id = payload.get("session_id")
            if isinstance(session_id, str) and session_id:
                room_id = file_path.stem
                self._session_ids[room_id] = session_id
                self._active_sessions.add(room_id)

    def _room_replies(self, room_id: str) -> list[str]:
        return self.replies_by_room.get(room_id, self.replies)

    def _consume(self, room_id: str) -> str:
        values = self._room_replies(room_id)
        index = self._indices.get(room_id, 0)
        if not values:
            raise DriverError("FakeDriver has no replies configured")
        if index >= len(values):
            if not self.cycle:
                raise DriverError("FakeDriver replies exhausted")
            index = 0
        value = values[index]
        self._indices[room_id] = index + 1
        return value

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        _ = system_frame
        session_id = f"fake-session-{room_id}"
        self._active_sessions.add(room_id)
        self._session_ids[room_id] = session_id
        self._persist_session(room_id, session_id)
        if prime_reply:
            self._bootstrap_reply[room_id] = self._consume(room_id)
        return session_id

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        _ = user_message
        if room_id not in self._active_sessions:
            raise DriverError(f"No fake session for room '{room_id}'")
        if room_id in self._bootstrap_reply:
            value = self._bootstrap_reply.pop(room_id)
            return DriverReply(content=value, raw_output=value)
        value = self._consume(room_id)
        return DriverReply(content=value, raw_output=value)

    async def close_session(self, room_id: str) -> None:
        self._active_sessions.discard(room_id)
        self._session_ids.pop(room_id, None)
        self._bootstrap_reply.pop(room_id, None)
        try:
            self._session_file(room_id).unlink(missing_ok=True)
        except OSError:
            return

    async def has_session(self, room_id: str) -> bool:
        return room_id in self._active_sessions

    async def send(self, prompt: str) -> DriverReply:
        return await self.send_in_session("__legacy__", prompt)
