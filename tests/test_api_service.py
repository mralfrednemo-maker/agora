from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

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


async def test_start_room_api_rejects_unknown_style() -> None:
    temp_dir = _workspace_tmp("api-unknown-style")
    store = RoomStore(temp_dir / "rooms")
    drivers = {"fake-a": FakeDriver("fake-a", "A", replies=["p"], cycle=False)}
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    commands = CommandHandler(engine=engine, context=CommandContext())
    api = ApiService(engine=engine, commands=commands, driver_health={})
    with pytest.raises(ValueError) as exc:
        await api.start_room(
            topic="api style test",
            participants=["fake-a"],
            max_total_rounds=4,
            convergence="agree-marker",
            style="exhaustion-looop",
            auto_verdict=False,
        )
    assert "Unknown style" in str(exc.value)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.parametrize(
    ("max_total_rounds", "expected_error"),
    [
        (0, "max_total_rounds must be >= 4"),
        (51, "max_total_rounds must be <= 50 for style 'exhaustion-loop'"),
    ],
)
async def test_start_room_api_validates_exhaustion_round_bounds(
    max_total_rounds: int,
    expected_error: str,
) -> None:
    temp_dir = _workspace_tmp("api-exhaustion-round-bounds")
    store = RoomStore(temp_dir / "rooms")
    target_dir = temp_dir / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    dod_file = temp_dir / "dod.md"
    dod_file.write_text("dod", encoding="utf-8")
    drivers = {
        "claude": FakeDriver("claude", "Claude", replies=["p"], cycle=False),
        "gemini": FakeDriver("gemini", "Gemini", replies=["p"], cycle=False),
        "codex": FakeDriver("codex", "Codex", replies=["p"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    engine.drivers["claude"].kind = "claude-code-new"
    engine.drivers["gemini"].kind = "gemini-cli"
    engine.drivers["codex"].kind = "codex"
    commands = CommandHandler(engine=engine, context=CommandContext())
    api = ApiService(engine=engine, commands=commands, driver_health={})
    with pytest.raises(ValueError) as exc:
        await api.start_room(
            topic="api exhaustion rounds",
            participants=["claude", "gemini", "codex"],
            max_total_rounds=max_total_rounds,
            convergence="adversarial-exhaustion",
            style="exhaustion-loop",
            auto_verdict=False,
            target_file=str(target_dir),
            dod_file=str(dod_file),
        )
    assert expected_error in str(exc.value)
    shutil.rmtree(temp_dir, ignore_errors=True)


async def test_start_room_api_rejects_invalid_exhaustion_participants() -> None:
    temp_dir = _workspace_tmp("api-exhaustion-kinds")
    store = RoomStore(temp_dir / "rooms")
    target_dir = temp_dir / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    dod_file = temp_dir / "dod.md"
    dod_file.write_text("dod", encoding="utf-8")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=["p"], cycle=False),
        "fake-b": FakeDriver("fake-b", "B", replies=["p"], cycle=False),
        "fake-c": FakeDriver("fake-c", "C", replies=["p"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    commands = CommandHandler(engine=engine, context=CommandContext())
    api = ApiService(engine=engine, commands=commands, driver_health={})
    with pytest.raises(ValueError) as exc:
        await api.start_room(
            topic="api exhaustion participants",
            participants=["fake-a", "fake-b", "fake-c"],
            max_total_rounds=4,
            convergence="adversarial-exhaustion",
            style="exhaustion-loop",
            auto_verdict=False,
            target_file=str(target_dir),
            dod_file=str(dod_file),
        )
    assert "Exhaustion Loop requires one each" in str(exc.value)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.parametrize(
    ("target_file", "dod_file", "expected_error"),
    [
        ("pyproject.toml", "pyproject.toml", "Target path must be a directory"),
        ("src", "src", "DoD path must be a file"),
    ],
)
async def test_start_room_api_validates_exhaustion_path_types(
    target_file: str,
    dod_file: str,
    expected_error: str,
) -> None:
    temp_dir = _workspace_tmp("api-exhaustion-path-types")
    store = RoomStore(temp_dir / "rooms")
    drivers = {
        "fake-a": FakeDriver("fake-a", "A", replies=["p"], cycle=False),
        "fake-b": FakeDriver("fake-b", "B", replies=["p"], cycle=False),
        "fake-c": FakeDriver("fake-c", "C", replies=["p"], cycle=False),
    }
    engine = RoomEngine(store=store, drivers=drivers, emit=_noop_emit)
    commands = CommandHandler(engine=engine, context=CommandContext())
    api = ApiService(engine=engine, commands=commands, driver_health={})
    engine.drivers["fake-a"].kind = "claude-code-new"
    engine.drivers["fake-b"].kind = "gemini-cli"
    engine.drivers["fake-c"].kind = "codex"
    with pytest.raises(ValueError) as exc:
        await api.start_room(
            topic="api exhaustion path types",
            participants=["fake-a", "fake-b", "fake-c"],
            max_total_rounds=4,
            convergence="adversarial-exhaustion",
            style="exhaustion-loop",
            auto_verdict=False,
            target_file=target_file,
            dod_file=dod_file,
        )
    assert expected_error in str(exc.value)
    shutil.rmtree(temp_dir, ignore_errors=True)
