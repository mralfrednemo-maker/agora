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


async def _wait_done(engine: RoomEngine, room_id: str, timeout: float = 5.0) -> None:
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < timeout:
        room = engine.rooms[room_id]
        if room.status == "done" and room.verdict_text is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"room did not reach done with verdict. status: {engine.rooms[room_id].status}, verdict: {engine.rooms[room_id].verdict_text}")


async def test_critic_terminate_e2e_converges_and_creates_verdict() -> None:
    temp_dir = _workspace_tmp("m3-e2e-critic")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-1": FakeDriver("fake-1", "Fake 1", replies=["pos1", "crit1", "deb1\nTERMINATE", "synth1", "This is a much longer verdict text that is definitely over fifty characters long to pass the test."], cycle=False),
        "fake-2": FakeDriver("fake-2", "Fake 2", replies=["pos2", "crit2", "deb2\nTERMINATE", "synth2", "This is another much longer verdict text that is also over fifty characters long."], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)

    room = await engine.create_room(
        "M3 End-to-end Critic+TERMINATE",
        style="critic-terminate",
        convergence_name="terminate-majority",
        max_total_rounds=8,
        auto_verdict=True,
    )
    await engine.set_participants(room.id, ["fake-1", "fake-2"])
    await engine.start(room.id)
    await _wait_done(engine, room.id)

    snapshot = engine.room_snapshot(room.id)
    verdict_path = temp_dir / "rooms" / room.id / "verdict.md"

    assert snapshot["status"] == "done"
    # positions (p) + critic (s) + debate (s) = 1*2 + 2 + 2 = 6
    # The spec says phases 2 and 3 alternate. With max_rounds=8, we have:
    # 1. positions (p, 2 turns)
    # 2. critic (s, 2 turns)
    # 3. debate (s, 2 turns) -> convergence here
    # 4. synthesis (p, 2 turns)
    # total = 2 + 2 + 2 + 2 = 8 turns
    assert len(snapshot["transcript"]) == 8
    assert snapshot["converged_round"] == 3 # 3rd *phase*
    assert snapshot["verdict_text"] is not None
    assert len(snapshot["verdict_text"]) > 50
    assert verdict_path.exists()
    assert verdict_path.read_text(encoding="utf-8") == snapshot["verdict_text"]

    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_verdict_regeneration() -> None:
    temp_dir = _workspace_tmp("m3-e2e-verdict-regen")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-1": FakeDriver("fake-1", "Fake 1", replies=["p1", "c1", "d1\nTERMINATE", "s1", "v1-original", "v1-regeneration-is-different"], cycle=False),
        "fake-2": FakeDriver("fake-2", "Fake 2", replies=["p2", "c2", "d2\nTERMINATE", "s2", "v2-original", "v2-regeneration-is-different"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("Verdict Regen Test", style="critic-terminate", auto_verdict=True, convergence_name="terminate-majority")
    await engine.set_participants(room.id, ["fake-1", "fake-2"])
    await engine.start(room.id)
    await _wait_done(engine, room.id)

    snapshot1 = engine.room_snapshot(room.id)
    assert snapshot1["status"] == "done"
    original_author = snapshot1["verdict_author"]
    assert original_author is not None
    assert "original" in snapshot1["verdict_text"]

    # Determine the other participant to regenerate with
    regenerate_with_id = "fake-2" if original_author == "fake-1" else "fake-1"

    # First regeneration, with the other participant
    new_verdict_text, new_author = await engine.regenerate_verdict(room.id, participant_id=regenerate_with_id)
    snapshot2 = engine.room_snapshot(room.id)
    assert new_author == regenerate_with_id
    assert "original" in snapshot2["verdict_text"] # Should be the *other* participant's original verdict
    assert snapshot2["verdict_text"] != snapshot1["verdict_text"]

    # Second regeneration, with the original participant again
    new_verdict_text_2, new_author_2 = await engine.regenerate_verdict(room.id, participant_id=original_author)
    snapshot3 = engine.room_snapshot(room.id)
    assert new_author_2 == original_author
    assert "regeneration-is-different" in snapshot3["verdict_text"]
    assert snapshot3["verdict_text"] != snapshot2["verdict_text"]
    
    verdict_path = temp_dir / "rooms" / room.id / "verdict.md"
    assert verdict_path.read_text(encoding="utf-8") == new_verdict_text_2

    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_follow_up() -> None:
    temp_dir = _workspace_tmp("m3-e2e-follow-up")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-1": FakeDriver("fake-1", "Fake 1", replies=["p1", "c1", "d1\nTERMINATE", "s1", "v1", "follow-up-reply"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room("Follow-up Test", style="critic-terminate", auto_verdict=True, convergence_name="terminate-majority")
    await engine.set_participants(room.id, ["fake-1"])
    await engine.start(room.id)
    await _wait_done(engine, room.id)

    transcript_len_before = len(engine.rooms[room.id].transcript.entries)

    follow_up_entry = await engine.follow_up(room.id, "fake-1", "This is a follow-up question.")

    transcript_len_after = len(engine.rooms[room.id].transcript.entries)
    snapshot = engine.room_snapshot(room.id)
    last_entry = snapshot["transcript"][-1]

    assert transcript_len_after == transcript_len_before + 1
    assert last_entry["participant_id"] == "fake-1"
    assert last_entry["role"] == "follow_up"
    assert last_entry["content"] == "follow-up-reply"
    assert follow_up_entry.content == "follow-up-reply"

    shutil.rmtree(temp_dir, ignore_errors=True)


async def _wait_for_warning(engine: RoomEngine, room_id: str, timeout: float = 10.0) -> None:
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < timeout:
        room = engine.rooms[room_id]
        if room.warning_detail:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("room did not set warning_detail")


async def test_stuck_debate_warning() -> None:
    temp_dir = _workspace_tmp("m3-e2e-stuck-warning")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-1": FakeDriver("fake-1", "Fake 1", replies=["reply"], cycle=True),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    room = await engine.create_room(
        "Stuck Debate Test",
        style="critic-terminate",
        max_total_rounds=15,
        convergence_name="terminate-majority",
    )
    await engine.set_participants(room.id, ["fake-1"])
    await engine.start(room.id)
    await _wait_for_warning(engine, room.id)
    await engine.stop(room.id) # Stop the room so the test doesn't hang

    snapshot = engine.room_snapshot(room.id)
    assert snapshot["warning_detail"] == "round 8/15 with no TERMINATE votes"

    shutil.rmtree(temp_dir, ignore_errors=True)

