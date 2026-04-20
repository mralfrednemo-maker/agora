from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from agora.commands.handlers import CommandContext, CommandHandler
from agora.drivers.fake import FakeDriver
from agora.engine.room import RoomEngine
from agora.persistence.store import RoomStore
from agora.web.api import ApiService


async def _noop_emit(_: dict[str, object]) -> None:
    return


def _workspace_tmp(name: str) -> Path:
    root = Path("C:/Users/chris/PROJECTS/agora/data/test-temp")
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{name}-{uuid4()}"
    target.mkdir(parents=True, exist_ok=True)
    return target


async def test_start_room_api_creates_participants_and_runs() -> None:
    temp_dir = _workspace_tmp("api-start")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=["p", "c", "d", "v\nAGREE"], cycle=False),
        "fake-b": FakeDriver("fake-b", "B", replies=["p", "c", "d", "v\nAGREE"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    commands = CommandHandler(engine=engine, context=CommandContext())
    api = ApiService(engine=engine, commands=commands, driver_health={})
    payload = await api.start_room(
        topic="api test",
        participants=["fake-a", "fake-b"],
        max_total_rounds=4,
        convergence="agree-marker",
        style="ein-mdp",
        auto_verdict=False,
    )
    room_id = str(payload["room_id"])
    assert room_id in engine.rooms
    assert len(engine.rooms[room_id].participants) == 2
    await engine.stop(room_id)
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_archive_and_delete_room_api() -> None:
    temp_dir = _workspace_tmp("api-archive-delete")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=["p", "c", "d", "v\nAGREE"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    commands = CommandHandler(engine=engine, context=CommandContext())
    api = ApiService(engine=engine, commands=commands, driver_health={})
    room = await engine.create_room("archive me")
    await engine.set_participants(room.id, ["fake-a"])
    await api.archive_room(room.id)
    assert engine.rooms[room.id].archived is True
    await api.delete_room(room.id)
    assert room.id not in engine.rooms
    shutil.rmtree(temp_dir, ignore_errors=True)
