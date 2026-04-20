from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

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


async def _wait_done(engine: RoomEngine, room_id: str, timeout: float = 6.0) -> None:
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < timeout:
        if engine.rooms[room_id].status == "done":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("room did not finish")


def _never_terminate_replies(prefix: str, rounds: int) -> list[str]:
    replies = [f"{prefix} positions", f"{prefix} critic"]
    for idx in range(1, rounds + 1):
        replies.append(f"{prefix} debate round {idx}\ncontinue")
    replies.append(f"{prefix} synthesis\nTERMINATE")
    replies.append(f"{prefix} verdict writer")
    return replies


def _m2_replies(prefix: str) -> list[str]:
    return [
        f"{prefix} positions",
        f"{prefix} contrarian",
        f"{prefix} debate",
        f"{prefix} verdict\nAGREE",
    ]


def _critic_terminate_replies(prefix: str, final_terminate: bool) -> list[str]:
    marker = "TERMINATE" if final_terminate else "continue"
    return [
        f"{prefix} positions",
        f"{prefix} critic",
        f"{prefix} debate round 1",
        f"{prefix} debate round 2",
        f"{prefix} debate round 3\n{marker}",
        f"{prefix} synthesis\nTERMINATE",
        f"# Verdict\n{prefix} final verdict",
        f"{prefix} follow-up reply",
    ]


async def test_integration_m2_four_phase_run_three_fake_drivers() -> None:
    temp_dir = _workspace_tmp("integration-m2")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=_m2_replies("a"), cycle=False),
        "fake-b": FakeDriver("fake-b", "B", replies=_m2_replies("b"), cycle=False),
        "fake-c": FakeDriver("fake-c", "C", replies=_m2_replies("c"), cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("Debate topic", max_total_rounds=4)
    await engine.set_participants(room.id, ["fake-a", "fake-b", "fake-c"])
    await engine.start(room.id)
    await _wait_done(engine, room.id)
    snap = engine.room_snapshot(room.id)
    assert snap["current_phase"] == "verdict"
    assert snap["status"] == "done"
    assert len(snap["transcript"]) == 12
    verdict_entries = [entry for entry in snap["transcript"] if entry["phase"] == "verdict"]
    assert len(verdict_entries) == 3
    assert all(str(entry["content"]).rstrip().endswith("AGREE") for entry in verdict_entries)
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_restore_running_room_as_paused_after_restart() -> None:
    temp_dir = _workspace_tmp("restore-paused")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=_m2_replies("a"), cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("Crash topic")
    await engine.set_participants(room.id, ["fake-a"])
    engine.rooms[room.id].status = "running"
    engine.rooms[room.id].updated_at = "2026-04-20T00:00:00Z"
    engine._persist_room(engine.rooms[room.id])

    restored = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    await restored.restore_rooms()
    assert restored.rooms[room.id].status == "paused"
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.parametrize("convergence", ["agree-marker", "consensus-prefix", "none"])
async def test_room_create_accepts_supported_convergence(convergence: str) -> None:
    temp_dir = _workspace_tmp("convergence-create")
    store = RoomStore(temp_dir / "rooms")
    drivers = {"fake-a": FakeDriver("fake-a", "A", replies=_m2_replies("a"), cycle=False)}
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("Topic", convergence_name=convergence)
    assert room.convergence.name == convergence
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_integration_critic_terminate_stops_in_round_three_and_writes_verdict() -> None:
    temp_dir = _workspace_tmp("integration-critic-terminate")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=_critic_terminate_replies("a", final_terminate=True), cycle=False),
        "fake-b": FakeDriver("fake-b", "B", replies=_critic_terminate_replies("b", final_terminate=True), cycle=False),
        "fake-c": FakeDriver("fake-c", "C", replies=_critic_terminate_replies("c", final_terminate=False), cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room(
        "Critic terminate topic",
        max_total_rounds=15,
        convergence_name="terminate-majority",
        style="critic-terminate",
    )
    await engine.set_participants(room.id, ["fake-a", "fake-b", "fake-c"])
    await engine.start(room.id)
    await _wait_done(engine, room.id)
    snap = engine.room_snapshot(room.id)
    assert snap["status"] == "done"
    assert snap["current_phase"] == "synthesis"
    assert snap["converged_round"] >= 5
    assert snap["verdict_text"] is not None
    assert (store.room_dir(room.id) / "verdict.md").exists()
    phases = [entry["phase"] for entry in snap["transcript"] if entry["role"] == "participant"]
    assert phases.count("debate") == 9
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_follow_up_appends_follow_up_role_without_retriggering_convergence() -> None:
    temp_dir = _workspace_tmp("integration-follow-up")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=["p", "c", "d", "v\nAGREE", "follow-up answer"], cycle=False),
        "fake-b": FakeDriver("fake-b", "B", replies=["p", "c", "d", "v\nAGREE"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("Follow-up topic", max_total_rounds=4, auto_verdict=False)
    await engine.set_participants(room.id, ["fake-a", "fake-b"])
    await engine.start(room.id)
    await _wait_done(engine, room.id)
    before_count = len(engine.room_snapshot(room.id)["transcript"])
    entry = await engine.follow_up(room.id, "fake-a", "What changed your mind?")
    snap = engine.room_snapshot(room.id)
    assert entry.role == "follow_up"
    assert snap["status"] == "done"
    assert len(snap["transcript"]) == before_count + 1
    assert snap["transcript"][-1]["role"] == "follow_up"
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_critic_terminate_emits_stuck_warning_on_round_eight_no_votes() -> None:
    temp_dir = _workspace_tmp("integration-stuck-warning")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=_never_terminate_replies("a", rounds=9), cycle=False),
        "fake-b": FakeDriver("fake-b", "B", replies=_never_terminate_replies("b", rounds=9), cycle=False),
    }
    events: list[dict[str, object]] = []

    async def capture_emit(event: dict[str, object]) -> None:
        events.append(event)

    engine = RoomEngine(store=store, drivers=drivers, emit=capture_emit)
    room = await engine.create_room(
        "stuck",
        max_total_rounds=12,
        convergence_name="terminate-majority",
        style="critic-terminate",
        auto_verdict=False,
    )
    await engine.set_participants(room.id, ["fake-a", "fake-b"])
    await engine.start(room.id)
    await _wait_done(engine, room.id, timeout=8.0)
    warning_events = [event for event in events if event.get("type") == "debate.warning"]
    assert warning_events
    assert warning_events[0]["detail"] == "round 8/15 with no TERMINATE votes"
    snap = engine.room_snapshot(room.id)
    assert snap["warning_detail"] == "round 8/15 with no TERMINATE votes"
    shutil.rmtree(temp_dir, ignore_errors=True)
