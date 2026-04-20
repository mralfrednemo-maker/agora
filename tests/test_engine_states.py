from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest

from agora.drivers.base import DriverReply
from agora.drivers.fake import FakeDriver
from agora.engine.room import RoomEngine
from agora.persistence.store import RoomStore


async def _noop_emit(_: dict[str, object]) -> None:
    return


def _workspace_tmp(name: str) -> Path:
    root = Path("C:/Users/chris/PROJECTS/agora/data/test-temp")
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{name}-{uuid4()}"
    target.mkdir(parents=True, exist_ok=True)
    return target


async def _wait_done(engine: RoomEngine, room_id: str, timeout: float = 4.0) -> None:
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < timeout:
        if engine.rooms[room_id].status == "done":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("room did not reach done")


class DelayedFakeDriver(FakeDriver):
    def __init__(self, *args, delay_s: float, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_s = delay_s

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:  # type: ignore[override]
        await asyncio.sleep(self.delay_s)
        return await super().send_in_session(room_id, user_message)


async def test_engine_state_transitions_and_persistence() -> None:
    temp_dir = _workspace_tmp("engine-states")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-1": FakeDriver("fake-1", "Fake 1", replies=["p", "c", "d", "v\nAGREE"], cycle=False),
        "fake-2": FakeDriver("fake-2", "Fake 2", replies=["p", "c", "d", "v\nAGREE"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)

    room = await engine.create_room("topic")
    await engine.set_participants(room.id, ["fake-1", "fake-2"])
    await engine.inject(room.id, "Use strict logic")
    await engine.start(room.id)
    await _wait_done(engine, room.id)

    snapshot = engine.room_snapshot(room.id)
    assert snapshot["status"] == "done"
    assert len(snapshot["transcript"]) == 10
    assert (store.room_dir(room.id) / "room.json").exists()
    assert (store.room_dir(room.id) / "transcript.jsonl").exists()
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_max_total_rounds_four_produces_exactly_four_turns_single_participant() -> None:
    temp_dir = _workspace_tmp("max-rounds-4")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-1": FakeDriver("fake-1", "Fake 1", replies=["positions", "contrarian", "debate", "verdict\nAGREE"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("topic", max_total_rounds=4)
    await engine.set_participants(room.id, ["fake-1"])
    await engine.start(room.id)
    await _wait_done(engine, room.id)
    snapshot = engine.room_snapshot(room.id)
    assert len(snapshot["transcript"]) == 4
    phases = [item["phase"] for item in snapshot["transcript"]]
    assert phases == ["positions", "contrarian", "debate", "verdict"]
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_parallel_phase_elapsed_close_to_max_not_sum() -> None:
    temp_dir = _workspace_tmp("parallel-timing")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "a": DelayedFakeDriver("a", "A", replies=["p", "c", "d", "v\nAGREE"], cycle=False, delay_s=0.25),
        "b": DelayedFakeDriver("b", "B", replies=["p", "c", "d", "v\nAGREE"], cycle=False, delay_s=0.35),
        "c": DelayedFakeDriver("c", "C", replies=["p", "c", "d", "v\nAGREE"], cycle=False, delay_s=0.45),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("parallel timing", max_total_rounds=4)
    await engine.set_participants(room.id, ["a", "b", "c"])
    started = perf_counter()
    await engine.start(room.id)
    await _wait_done(engine, room.id, timeout=6.0)
    elapsed = perf_counter() - started
    # Two parallel phases + one serial + one parallel => lower than pure serial sum.
    assert elapsed < 4.0
    assert elapsed > 1.1
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_fake_session_persistence_and_rehydration() -> None:
    temp_dir = _workspace_tmp("session-rehydrate")
    state_path = Path("C:/Users/chris/PROJECTS/agora/data/driver-state/fake-session-test")
    if state_path.exists():
        shutil.rmtree(state_path, ignore_errors=True)
    driver = FakeDriver("fake-session-test", "Fake", replies=["boot", "next"], cycle=False)
    await driver.start_session("room-a", "frame")
    assert await driver.has_session("room-a")

    rehydrated = FakeDriver("fake-session-test", "Fake", replies=["boot", "next"], cycle=False)
    assert await rehydrated.has_session("room-a")
    await rehydrated.close_session("room-a")
    assert await rehydrated.has_session("room-a") is False

    if state_path.exists():
        shutil.rmtree(state_path, ignore_errors=True)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.parametrize("value", [1, 2, 3])
async def test_set_rounds_enforces_minimum_four(value: int) -> None:
    temp_dir = _workspace_tmp("min-rounds")
    store = RoomStore(temp_dir / "rooms")
    drivers = {"fake-1": FakeDriver("fake-1", "Fake", replies=["a"])}
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("topic")
    await engine.set_rounds(room.id, value=value, extend=False)
    assert engine.rooms[room.id].max_total_rounds == 4
    shutil.rmtree(temp_dir, ignore_errors=True)
