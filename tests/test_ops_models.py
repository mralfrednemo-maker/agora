from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agora.drivers.base import Driver, DriverReply
from agora.drivers.claude_code_new import ClaudeCodeNewDriver
from agora.ops.admin import OPS_ROOM_ID, OpsManager
from agora.ops.tools import ToolRegistry


@dataclass
class DummyDriver(Driver):
    id: str = "admin-1"
    kind: str = "dummy"
    token_ceiling: int = 1_000
    model: str | None = "MiniMax-M2.7-highspeed"
    closed_rooms: list[str] = field(default_factory=list)

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        return "dummy-session"

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        return DriverReply(content="ok")

    async def close_session(self, room_id: str) -> None:
        self.closed_rooms.append(room_id)

    async def has_session(self, room_id: str) -> bool:
        return False


def test_claude_code_new_driver_uses_explicit_backend_model() -> None:
    driver = ClaudeCodeNewDriver(id="admin-1", display_name="Ops Admin", model="MiniMax-M2.7")
    command = driver._router_command(session_id="session-123")
    assert "--model MiniMax-M2.7" in command
    assert "--resume session-123" in command
    assert "-Provider minimax" in command


def test_claude_code_new_driver_falls_back_to_wrapper_default_when_model_unset() -> None:
    driver = ClaudeCodeNewDriver(id="claude-code-new-1", display_name="Claude Code New", model=None)
    command = driver._router_command()
    assert "--model MiniMax-M2.7" not in command
    assert "--model MiniMax-M2.7-highspeed" not in command
    assert "-Provider minimax" in command


def test_ops_system_frame_injects_backend_identity() -> None:
    driver = DummyDriver(model="MiniMax-M2.7")
    mgr = OpsManager(driver=driver, registry=ToolRegistry(), emit=_noop_emit)
    frame = mgr._system_frame()
    assert "Claude Minimax wrapper" in frame
    assert "`MiniMax-M2.7`" in frame
    assert "answer with that backend model name exactly" in frame


@pytest.mark.asyncio
async def test_ops_reset_session_closes_driver_session() -> None:
    driver = DummyDriver()
    mgr = OpsManager(driver=driver, registry=ToolRegistry(), emit=_noop_emit)
    mgr._session_started = True
    await mgr.reset_session()
    assert mgr._session_started is False
    assert driver.closed_rooms == [OPS_ROOM_ID]


async def _noop_emit(_payload: dict[str, object]) -> None:
    return None
