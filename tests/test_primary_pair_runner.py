from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agora.drivers.base import Driver, DriverReply
from agora.engine.primary_pair import PrimaryPairConfig, PrimaryPairRunner, RoleSpec
from agora.engine.room import RoomEngine
from agora.persistence.store import RoomStore


@dataclass(slots=True)
class ScriptedDriver(Driver):
    id: str
    kind: str
    model: str
    effort: str | None = None
    token_ceiling: int = 100_000
    sessions: dict[str, str] = field(default_factory=dict)
    replies: list[str] = field(default_factory=list)
    bootstraps: dict[str, DriverReply] = field(default_factory=dict)

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        self.sessions[room_id] = f"{self.id}-session"
        reply = DriverReply(content=self.replies.pop(0), resume_id=self.sessions[room_id])
        if prime_reply:
            self.bootstraps[room_id] = reply
        return self.sessions[room_id]

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        if room_id in self.bootstraps:
            return self.bootstraps.pop(room_id)
        return DriverReply(content=self.replies.pop(0), resume_id=self.sessions[room_id])

    async def close_session(self, room_id: str) -> None:
        self.sessions.pop(room_id, None)

    async def has_session(self, room_id: str) -> bool:
        return room_id in self.sessions


@dataclass(slots=True)
class ConcurrencyProbe:
    active: int = 0
    peak: int = 0

    async def enter(self, delay: float) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(delay)
        finally:
            self.active -= 1


@dataclass(slots=True)
class DelayedScriptedDriver(ScriptedDriver):
    probe: ConcurrencyProbe | None = None
    delay_seconds: float = 0.0

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        if self.probe is not None and self.delay_seconds > 0:
            await self.probe.enter(self.delay_seconds)
        return await ScriptedDriver.start_session(self, room_id, system_frame, prime_reply)

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        if self.probe is not None and self.delay_seconds > 0:
            await self.probe.enter(self.delay_seconds)
        return await ScriptedDriver.send_in_session(self, room_id, user_message)


def full_doc(marker: str = "NOT_CONVERGED: test") -> str:
    remaining = (
        "- No substantive disagreement remains."
        if marker == "CONVERGED"
        else "- Boundary choice can still differ."
    )
    return "\n".join(
        [
            "VERDICT",
            "Use the best available official estimate.",
            "AGREEMENTS",
            "- Use an official statistical source where possible.",
            "REMAINING DISAGREEMENTS",
            remaining,
            "REASONING AND EVIDENCE",
            "- Define the target population first.",
            "FINAL DOCUMENT",
            "Define geography, choose source, state date, and report caveats.",
            marker,
        ]
    )


def seed_doc(label: str) -> str:
    return "\n".join(
        [
            "WORKING INTERPRETATION",
            "The question asks for a method, not a single number.",
            "DIRECT ANSWER",
            "Define the geography and use official statistics.",
            "SUPPORTING REASONS",
            "Official sources and explicit boundaries prevent mixing municipality, urban, and metro figures.",
            "MATERIAL UNCERTAINTIES",
            "The answer changes if the target is daytime or de facto population.",
        ]
    )


@pytest.mark.asyncio
async def test_primary_pair_runner_writes_auditable_ledger(tmp_path: Path) -> None:
    codex = ScriptedDriver(
        id="codex",
        kind="codex",
        model="gpt-5.4-mini",
        effort="low",
        replies=[seed_doc("A"), full_doc(), full_doc("CONVERGED")],
    )
    gemini = ScriptedDriver(
        id="gemini",
        kind="gemini-cli",
        model="gemini-2.5-flash-lite",
        replies=[seed_doc("B"), full_doc(), full_doc("CONVERGED")],
    )
    claude = ScriptedDriver(
        id="claude",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        replies=[seed_doc("S")],
    )
    config = PrimaryPairConfig(
        brief="What is the best way to calculate the population of Athens?",
        run_id="test-run",
        root_dir=tmp_path,
        max_revision_turns=2,
        primary_a=RoleSpec("primary_a", "LLM1 / Codex", codex, "llm1", "gpt-5.4-mini", "low"),
        primary_b=RoleSpec("primary_b", "LLM2 / Gemini CLI", gemini, "llm2", "gemini-2.5-flash-lite"),
        secondary=RoleSpec("secondary", "LLM3 / Claude MiniMax", claude, "llm3", "MiniMax-M2.7-highspeed"),
    )
    result = await PrimaryPairRunner(config).run()

    assert result.validation["ok"], result.validation
    assert result.status == "converged"
    assert result.stop_reason == "reciprocal_convergence"
    assert result.turns == 7
    assert Path(result.ledger_path).exists()
    assert result.final_artifact is not None
    assert result.final_artifact.marker == "CONVERGED"


@pytest.mark.asyncio
async def test_primary_pair_executes_parallel_groups_concurrently(tmp_path: Path) -> None:
    probe = ConcurrencyProbe()
    codex = DelayedScriptedDriver(
        id="codex",
        kind="codex",
        model="gpt-5.4-mini",
        effort="low",
        replies=[seed_doc("A"), full_doc(), full_doc("CONVERGED")],
        probe=probe,
        delay_seconds=0.02,
    )
    gemini = DelayedScriptedDriver(
        id="gemini",
        kind="gemini-cli",
        model="gemini-2.5-flash-lite",
        replies=[seed_doc("B"), full_doc(), full_doc("CONVERGED")],
        probe=probe,
        delay_seconds=0.02,
    )
    claude = DelayedScriptedDriver(
        id="claude",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        replies=[seed_doc("S")],
        probe=probe,
        delay_seconds=0.02,
    )
    config = PrimaryPairConfig(
        brief="What is the best way to calculate the population of Athens?",
        run_id="parallel-test",
        root_dir=tmp_path,
        max_revision_turns=2,
        primary_a=RoleSpec("primary_a", "LLM1 / Codex", codex, "llm1", "gpt-5.4-mini", "low"),
        primary_b=RoleSpec("primary_b", "LLM2 / Gemini CLI", gemini, "llm2", "gemini-2.5-flash-lite"),
        secondary=RoleSpec("secondary", "LLM3 / Claude MiniMax", claude, "llm3", "MiniMax-M2.7-highspeed"),
    )

    result = await PrimaryPairRunner(config).run()

    assert result.validation["ok"], result.validation
    assert probe.peak >= 2


@pytest.mark.asyncio
async def test_primary_pair_requires_reciprocal_confirmation(tmp_path: Path) -> None:
    codex = ScriptedDriver(
        id="codex",
        kind="codex",
        model="gpt-5.4-mini",
        effort="low",
        replies=[seed_doc("A"), full_doc("CONVERGED")],
    )
    gemini = ScriptedDriver(
        id="gemini",
        kind="gemini-cli",
        model="gemini-2.5-flash-lite",
        replies=[seed_doc("B"), full_doc("CONVERGED"), full_doc("CONVERGED")],
    )
    claude = ScriptedDriver(
        id="claude",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        replies=[seed_doc("S")],
    )
    config = PrimaryPairConfig(
        brief="Should a small team use a monolith?",
        run_id="one-sided-test",
        root_dir=tmp_path,
        max_revision_turns=1,
        primary_a=RoleSpec("primary_a", "LLM1 / Codex", codex, "llm1", "gpt-5.4-mini", "low"),
        primary_b=RoleSpec("primary_b", "LLM2 / Gemini CLI", gemini, "llm2", "gemini-2.5-flash-lite"),
        secondary=RoleSpec("secondary", "LLM3 / Claude MiniMax", claude, "llm3", "MiniMax-M2.7-highspeed"),
    )
    result = await PrimaryPairRunner(config).run()

    assert result.validation["ok"], result.validation
    assert result.status == "stopped_at_cap"
    assert result.stop_reason == "revision_cap_reached"
    assert result.final_artifact is not None
    assert result.final_artifact.role_key == "primary_b"
    assert result.final_artifact.marker == "CONVERGED"


@pytest.mark.asyncio
async def test_primary_pair_reports_cap_when_not_converged(tmp_path: Path) -> None:
    codex = ScriptedDriver(
        id="codex",
        kind="codex",
        model="gpt-5.4-mini",
        effort="low",
        replies=[seed_doc("A"), full_doc(), full_doc()],
    )
    gemini = ScriptedDriver(
        id="gemini",
        kind="gemini-cli",
        model="gemini-2.5-flash-lite",
        replies=[seed_doc("B"), full_doc(), full_doc()],
    )
    claude = ScriptedDriver(
        id="claude",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        replies=[seed_doc("S")],
    )
    config = PrimaryPairConfig(
        brief="What is the best way to calculate a boundary-dependent population?",
        run_id="cap-test",
        root_dir=tmp_path,
        max_revision_turns=2,
        primary_a=RoleSpec("primary_a", "LLM1 / Codex", codex, "llm1", "gpt-5.4-mini", "low"),
        primary_b=RoleSpec("primary_b", "LLM2 / Gemini CLI", gemini, "llm2", "gemini-2.5-flash-lite"),
        secondary=RoleSpec("secondary", "LLM3 / Claude MiniMax", claude, "llm3", "MiniMax-M2.7-highspeed"),
    )
    result = await PrimaryPairRunner(config).run()

    assert result.validation["ok"], result.validation
    assert result.status == "stopped_at_cap"
    assert result.stop_reason == "revision_cap_reached"
    assert result.final_artifact is not None
    assert result.final_artifact.marker == "NOT_CONVERGED"


@pytest.mark.asyncio
async def test_primary_pair_retries_conflicting_convergence_marker(tmp_path: Path) -> None:
    codex = ScriptedDriver(
        id="codex",
        kind="codex",
        model="gpt-5.4-mini",
        effort="low",
        replies=[seed_doc("A"), full_doc(), full_doc()],
    )
    gemini = ScriptedDriver(
        id="gemini",
        kind="gemini-cli",
        model="gemini-2.5-flash-lite",
        replies=[
            seed_doc("B"),
            full_doc(),
            full_doc("CONVERGED").replace(
                "- No substantive disagreement remains.",
                "- Boundary choice still differs.",
            ),
            full_doc(),
        ],
    )
    claude = ScriptedDriver(
        id="claude",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        replies=[seed_doc("S")],
    )
    config = PrimaryPairConfig(
        brief="What is the best way to calculate a boundary-dependent population?",
        run_id="conflicting-marker-test",
        root_dir=tmp_path,
        max_revision_turns=2,
        primary_a=RoleSpec("primary_a", "LLM1 / Codex", codex, "llm1", "gpt-5.4-mini", "low"),
        primary_b=RoleSpec("primary_b", "LLM2 / Gemini CLI", gemini, "llm2", "gemini-2.5-flash-lite"),
        secondary=RoleSpec("secondary", "LLM3 / Claude MiniMax", claude, "llm3", "MiniMax-M2.7-highspeed"),
    )
    result = await PrimaryPairRunner(config).run()

    ledger = Path(result.ledger_path).read_text(encoding="utf-8")
    assert "CONVERGED marker conflicts with unresolved remaining disagreements" in ledger
    assert result.validation["ok"], result.validation
    assert result.status == "stopped_at_cap"


@pytest.mark.asyncio
async def test_room_engine_wires_primary_pair_runner_to_transcript(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []

    async def emit(payload: dict[str, object]) -> None:
        events.append(payload)

    codex = ScriptedDriver(
        id="codex",
        kind="codex",
        model="gpt-5.4-mini",
        effort="low",
        replies=[seed_doc("A"), full_doc(), full_doc("CONVERGED")],
    )
    gemini = ScriptedDriver(
        id="gemini",
        kind="gemini-cli",
        model="gemini-2.5-flash-lite",
        replies=[seed_doc("B"), full_doc(), full_doc("CONVERGED")],
    )
    claude = ScriptedDriver(
        id="claude",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        replies=[seed_doc("S")],
    )
    engine = RoomEngine(
        store=RoomStore(tmp_path / "rooms"),
        drivers={"codex": codex, "gemini": gemini, "claude": claude},
        emit=emit,
    )
    room = await engine.create_room(
        "What is the best way to calculate the population of Athens?",
        style="primary-pair",
        max_total_rounds=5,
        auto_verdict=False,
        room_config={
            "ui_mode": "primary-pair",
            "role_assignments": [
                {"role_key": "primary_a", "label": "LLM1", "driver_id": "codex", "requested_model": "gpt-5.4-mini", "effort": "low"},
                {"role_key": "primary_b", "label": "LLM2", "driver_id": "gemini", "requested_model": "gemini-2.5-flash-lite", "effort": ""},
                {"role_key": "secondary", "label": "LLM3", "driver_id": "claude", "requested_model": "MiniMax-M2.7-highspeed", "effort": ""},
            ],
        },
    )
    await engine.set_participants(room.id, ["codex", "gemini", "claude"], participant_specs=room.room_config["role_assignments"])  # type: ignore[index]
    await engine.start(room.id)

    task = engine._tasks.get(room.id)
    if task is not None:
        await task

    snapshot = engine.room_snapshot(room.id)
    assert snapshot["status"] == "done"
    assert snapshot["verdict_text"]
    assert len(snapshot["transcript"]) == 7
    assert snapshot["room_config"]["primary_pair"]["status"] == "converged"  # type: ignore[index]
    assert any(event.get("type") == "primary_pair.event" for event in events)
    assert any(event.get("type") == "participant.thinking" and event.get("in_flight") is True for event in events)
