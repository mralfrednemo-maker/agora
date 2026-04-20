from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class DriverError(Exception):
    pass


class DriverTimeoutError(DriverError):
    pass


@dataclass(slots=True)
class DriverReply:
    content: str
    raw_output: str | None = None
    tokens_out: int = 0
    resume_id: str | None = None


class Driver(ABC):
    id: str
    kind: str
    token_ceiling: int

    async def send(self, prompt: str) -> DriverReply:
        raise NotImplementedError("Deprecated in M2; use start_session/send_in_session")

    @abstractmethod
    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        """Create a session for (driver, room). Returns the session id.

        If prime_reply is True (default), the frame's response is cached and
        returned on the next send_in_session call — this is how the debate
        engine treats the frame as the Phase 1 prompt. If False (e.g. for the
        Ops admin agent), the reply is discarded so the first real user turn
        produces its own response.
        """
        raise NotImplementedError

    @abstractmethod
    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        raise NotImplementedError

    @abstractmethod
    async def close_session(self, room_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def has_session(self, room_id: str) -> bool:
        raise NotImplementedError

    async def health_check(self) -> tuple[bool, str]:
        return True, "ok"


@dataclass(slots=True)
class DriverConfig:
    id: str
    kind: str
    display_name: str
    token_ceiling: int
    options: dict[str, Any] = field(default_factory=dict)
