from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from agora.config.phases import DEFAULT_STYLE, Phase, phase_by_name, phases_for_style
from agora.drivers.base import Driver, DriverError, DriverReply
from agora.engine.budget import BudgetManager, TokenBudget, stub_summarizer
from agora.engine.convergence import ConvergenceCheck, build_convergence
from agora.engine.primary_pair import PrimaryPairConfig, PrimaryPairRunner, RoleSpec
from agora.engine.templates import DeltaInput, ParticipantPromptView, RoomFrameInput, default_renderer
from agora.engine.transcript import Transcript, TranscriptEntry, make_entry, utc_now_iso
from agora.persistence.store import RoomStore

RoomStatus = Literal["idle", "running", "paused", "done"]
EventEmitter = Callable[[dict[str, object]], Awaitable[None]]
VERDICT_PROMPT = (
    "The debate has concluded. Produce a final verdict document in markdown. Include:\n"
    "- The brief (restated verbatim).\n"
    "- Shared conclusions (bulleted).\n"
    '- Residual disagreements (bulleted, or "None.").\n'
    "- Condensed reasoning trail (numbered list of turns that moved the debate).\n"
    "Do not introduce new arguments. Synthesise what was said.\n"
    "Max 600 words."
)
FOLLOW_UP_PHASE = Phase(
    name="follow_up",
    mode="serial",
    instruction_template="Respond to this follow-up based on the completed debate context.",
    max_rounds=1,
    include_opponents=False,
)


@dataclass(slots=True)
class Participant:
    id: str
    kind: str
    display_name: str
    driver: Driver
    persona: str | None = None


@dataclass(slots=True)
class Room:
    id: str
    topic: str
    participants: list[Participant]
    phase_sequence: list[Phase]
    current_phase_index: int
    current_round: int
    max_total_rounds: int
    style: str
    status: RoomStatus
    convergence: ConvergenceCheck
    injected_instruction: str | None
    addressed_note: tuple[str, str] | None
    transcript: Transcript
    created_at: str
    updated_at: str
    archived: bool = False
    summary_cache: str | None = None
    auto_verdict: bool = True
    verdict_author: str | None = None
    verdict_text: str | None = None
    converged_round: int | None = None
    warning_detail: str | None = None
    target_file: str | None = None
    dod_file: str | None = None
    exhaustion_cycle: int = 0
    room_config: dict[str, object] = field(default_factory=dict)

    def current_phase(self) -> Phase:
        return self.phase_sequence[self.current_phase_index]

    def total_rounds(self) -> int:
        if self.style == "exhaustion-loop":
            return sum(1 for entry in self.transcript.entries if entry.role == "participant")
        keys = {(entry.phase, entry.round) for entry in self.transcript.entries if entry.role == "participant"}
        return len(keys)

    def exhaustion_attempts(self) -> int:
        return sum(
            1
            for entry in self.transcript.entries
            if entry.role == "participant" and entry.phase == "audit_gemini"
        )

    def _next_up(self) -> str | None:
        if self.status != "running" or not self.participants:
            return None
        if self.style == "exhaustion-loop":
            kind_by_phase = {
                "fix": "claude-code-new",
                "audit_gemini": "gemini-cli",
                "audit_codex": "codex",
            }
            target_kind = kind_by_phase.get(self.current_phase().name)
            if target_kind:
                for participant in self.participants:
                    if participant.kind == target_kind:
                        return participant.id
                return None
        if self.current_phase().mode == "parallel":
            return "all"
        return self.participants[0].id

    def to_state(self) -> dict[str, object]:
        agree_count, terminate_count = self._final_marker_counts()
        return {
            "id": self.id,
            "topic": self.topic,
            "participants": [
                {"id": p.id, "kind": p.kind, "display_name": p.display_name, "persona": p.persona}
                for p in self.participants
            ],
            "phase_sequence": [
                {
                    "name": phase.name,
                    "mode": phase.mode,
                    "instruction_template": phase.instruction_template,
                    "max_rounds": phase.max_rounds,
                    "include_opponents": phase.include_opponents,
                }
                for phase in self.phase_sequence
            ],
            "current_phase_index": self.current_phase_index,
            "current_phase": self.current_phase().name,
            "current_mode": self.current_phase().mode,
            "current_round": self.current_round,
            "next_up": self._next_up(),
            "max_total_rounds": self.max_total_rounds,
            "style": self.style,
            "status": self.status,
            "archived": self.archived,
            "convergence": self.convergence.name,
            "injected_instruction": self.injected_instruction,
            "addressed_note": self.addressed_note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary_cache": self.summary_cache,
            "auto_verdict": self.auto_verdict,
            "verdict_author": self.verdict_author,
            "verdict_text": self.verdict_text,
            "converged_round": self.converged_round,
            "warning_detail": self.warning_detail,
            "target_file": self.target_file,
            "dod_file": self.dod_file,
            "exhaustion_cycle": self.exhaustion_cycle,
            "room_config": self.room_config,
            "agree_count": agree_count,
            "terminate_count": terminate_count,
        }

    def _final_marker_counts(self) -> tuple[int, int]:
        agree = 0
        terminate = 0
        for participant in self.participants:
            latest = self.transcript.latest_by_participant(participant.id)
            if not latest:
                continue
            marker = self._last_non_empty_line(latest.content)
            if marker == "AGREE":
                agree += 1
            if marker == "TERMINATE":
                terminate += 1
        return agree, terminate

    @staticmethod
    def _last_non_empty_line(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ""


@dataclass(slots=True)
class RoomEngine:
    store: RoomStore
    drivers: dict[str, Driver]
    emit: EventEmitter
    renderer = default_renderer()
    budget_manager: BudgetManager = field(default_factory=lambda: BudgetManager(summarizer=stub_summarizer))
    rooms: dict[str, Room] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False)

    async def restore_rooms(self) -> None:
        for room_id in self.store.discover_rooms():
            room_data, transcript_data, summary_data = self.store.load_room(room_id)
            participants: list[Participant] = []
            for part in room_data.get("participants", []):
                driver_id = str(part["id"])
                if driver_id not in self.drivers:
                    continue
                driver = self.drivers[driver_id]
                participants.append(
                    Participant(
                        id=driver_id,
                        kind=str(part["kind"]),
                        display_name=str(part.get("display_name", driver_id)),
                        driver=driver,
                        persona=part.get("persona"),
                    )
                )
            style = str(room_data.get("style", DEFAULT_STYLE))
            phase_sequence = [
                Phase(
                    name=item["name"],
                    mode=item.get("mode", "serial"),
                    instruction_template=item["instruction_template"],
                    max_rounds=int(item["max_rounds"]),
                    include_opponents=bool(item.get("include_opponents", True)),
                )
                for item in room_data.get("phase_sequence", [])
            ] or phases_for_style(style=style, max_total_rounds=int(room_data.get("max_total_rounds", 6)))
            transcript = Transcript.from_jsonable(transcript_data)
            status_raw = str(room_data.get("status", "idle"))
            verdict_text = room_data.get("verdict_text")
            if not verdict_text:
                verdict_text = self.store.load_verdict(str(room_data["id"]))
            room = Room(
                id=str(room_data["id"]),
                topic=str(room_data["topic"]),
                participants=participants,
                phase_sequence=phase_sequence,
                current_phase_index=int(room_data.get("current_phase_index", 0)),
                current_round=int(room_data.get("current_round", 1)),
                max_total_rounds=max(4, int(room_data.get("max_total_rounds", 6))),
                style=style,
                status="paused" if status_raw in {"running", "paused"} else status_raw,
                convergence=build_convergence(str(room_data.get("convergence", "agree-marker"))),
                injected_instruction=room_data.get("injected_instruction"),
                addressed_note=tuple(room_data["addressed_note"]) if room_data.get("addressed_note") else None,
                transcript=transcript,
                created_at=str(room_data.get("created_at", utc_now_iso())),
                updated_at=utc_now_iso(),
                archived=bool(room_data.get("archived", False)),
                summary_cache=(summary_data or {}).get("summary") if summary_data else room_data.get("summary_cache"),
                auto_verdict=bool(room_data.get("auto_verdict", True)),
                verdict_author=room_data.get("verdict_author"),
                verdict_text=verdict_text,
                converged_round=room_data.get("converged_round"),
                warning_detail=room_data.get("warning_detail"),
                target_file=room_data.get("target_file"),
                dod_file=room_data.get("dod_file"),
                exhaustion_cycle=int(room_data.get("exhaustion_cycle", 0)),
                room_config=dict(room_data.get("room_config") or {}),
            )
            self.rooms[room.id] = room
            self._locks[room.id] = asyncio.Lock()
            self._persist_room(room)
            await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    def list_rooms(self) -> list[dict[str, object]]:
        sorted_ids = sorted(self.rooms, key=lambda rid: str(self.rooms[rid].updated_at), reverse=True)
        return [self.room_snapshot(room_id) for room_id in sorted_ids]

    def room_snapshot(self, room_id: str) -> dict[str, object]:
        room = self.rooms[room_id]
        state = room.to_state()
        state["transcript"] = room.transcript.to_jsonable()
        state["room_dir"] = str(self.store.room_dir(room.id))
        return state

    async def create_room(
        self,
        topic: str,
        convergence_name: str = "agree-marker",
        max_total_rounds: int = 6,
        style: str = DEFAULT_STYLE,
        auto_verdict: bool = True,
        target_file: str | None = None,
        dod_file: str | None = None,
        exhaustion_cycle: int = 0,
        room_config: dict[str, object] | None = None,
    ) -> Room:
        total = max_total_rounds if style == "exhaustion-loop" else max(4, max_total_rounds)
        selected_style = style.strip().lower() if style else DEFAULT_STYLE
        room = Room(
            id=str(uuid4()),
            topic=topic,
            participants=[],
            phase_sequence=phases_for_style(selected_style, total),
            current_phase_index=0,
            current_round=1,
            max_total_rounds=total,
            style=selected_style,
            status="idle",
            convergence=build_convergence(convergence_name),
            injected_instruction=None,
            addressed_note=None,
            transcript=Transcript(),
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            archived=False,
            auto_verdict=auto_verdict,
            target_file=target_file,
            dod_file=dod_file,
            exhaustion_cycle=exhaustion_cycle,
            room_config=dict(room_config or {}),
        )
        self.rooms[room.id] = room
        self._locks[room.id] = asyncio.Lock()
        self._persist_room(room)
        self._event(room, "info", "room_created", {"topic": topic})
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        return room

    async def archive_room(self, room_id: str) -> None:
        room = self.rooms[room_id]
        room.archived = True
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    async def delete_room(self, room_id: str) -> None:
        room = self.rooms.pop(room_id)
        self._locks.pop(room_id, None)
        task = self._tasks.pop(room_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for participant in room.participants:
            await participant.driver.close_session(room_id)
        self.store.delete_room(room_id)
        await self.emit({"type": "room.deleted", "room_id": room_id})

    async def set_participants(
        self,
        room_id: str,
        participant_ids: list[str],
        participant_specs: list[dict[str, object]] | None = None,
    ) -> None:
        room = self.rooms[room_id]
        spec_by_id = {
            str(item.get("driver_id")): item
            for item in (participant_specs or [])
            if item.get("driver_id")
        }
        selected: list[Participant] = []
        for driver_id in participant_ids:
            driver = self.drivers[driver_id]
            spec = spec_by_id.get(driver_id, {})
            display_name = str(spec.get("label") or spec.get("role_key") or driver_id)
            persona = None
            if spec:
                requested_model = str(spec.get("requested_model") or "").strip()
                requested_effort = str(spec.get("effort") or "").strip()
                role_key = str(spec.get("role_key") or "").strip()
                bits = [bit for bit in [f"role={role_key}" if role_key else "", f"model={requested_model}" if requested_model else "", f"effort={requested_effort}" if requested_effort else ""] if bit]
                if bits:
                    persona = " / ".join(bits)
            selected.append(Participant(id=driver_id, kind=driver.kind, display_name=display_name, driver=driver, persona=persona))
        room.participants = selected
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    async def _ensure_task(self, room_id: str) -> None:
        """Atomically cancel any existing task and spawn a fresh _run task."""
        old_task = self._tasks.pop(room_id, None)
        if old_task and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except asyncio.CancelledError:
                pass
        new_task = asyncio.create_task(self._run(room_id), name=f"agora-room-{room_id}")
        self._tasks[room_id] = new_task

    async def start(self, room_id: str) -> None:
        room = self.rooms[room_id]
        if room.status == "running":
            return
        room.status = "running"
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        self._event(room, "info", "room_started", {})
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        await self._ensure_task(room_id)

    async def pause(self, room_id: str) -> None:
        room = self.rooms[room_id]
        if room.status == "running":
            room.status = "paused"
            room.updated_at = utc_now_iso()
            self._persist_room(room)
            self._event(room, "info", "paused", {})
            await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    async def resume(self, room_id: str) -> None:
        room = self.rooms[room_id]
        if room.status != "paused":
            return
        room.status = "running"
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        self._event(room, "info", "resumed", {})
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        await self._ensure_task(room_id)

    async def stop(self, room_id: str) -> None:
        room = self.rooms[room_id]
        room.status = "done"
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        old_task = self._tasks.pop(room_id, None)
        if old_task and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except asyncio.CancelledError:
                pass
        for participant in room.participants:
            await participant.driver.close_session(room_id)
        self._event(room, "info", "stopped", {})
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    async def inject(self, room_id: str, text: str) -> None:
        room = self.rooms[room_id]
        room.injected_instruction = text
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    async def addressed_note(self, room_id: str, participant_id: str, text: str) -> None:
        room = self.rooms[room_id]
        room.addressed_note = (participant_id, text)
        room.updated_at = utc_now_iso()
        self._persist_room(room)

    async def set_phase(self, room_id: str, name: str) -> None:
        room = self.rooms[room_id]
        target = phase_by_name(name)
        if target is None:
            raise ValueError(f"Unknown phase: {name}")
        for idx, phase in enumerate(room.phase_sequence):
            if phase.name == target.name:
                room.current_phase_index = idx
                room.current_round = 1
                room.updated_at = utc_now_iso()
                self._persist_room(room)
                self._event(room, "info", "phase_changed", {"phase": phase.name})
                await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
                return

    async def set_rounds(self, room_id: str, value: int, extend: bool) -> None:
        room = self.rooms[room_id]
        total = room.max_total_rounds + value if extend else value
        room.max_total_rounds = max(4, total)
        room.phase_sequence = phases_for_style(room.style, room.max_total_rounds)
        room.updated_at = utc_now_iso()
        self._persist_room(room)

    async def synthesize(self, room_id: str, model: str | None = None) -> str:
        verdict, _ = await self.regenerate_verdict(room_id, participant_id=model)
        return verdict

    async def regenerate_verdict(self, room_id: str, participant_id: str | None = None) -> tuple[str, str]:
        room = self.rooms[room_id]
        if room.status != "done":
            raise ValueError("Verdict can be generated only when room status is done")
        participant = self._pick_verdict_participant(room, preferred_id=participant_id)
        reply, _, _ = await self._send_to_participant(room, FOLLOW_UP_PHASE, participant, None, user_message=VERDICT_PROMPT)
        room.verdict_author = participant.id
        room.verdict_text = reply.content
        room.updated_at = utc_now_iso()
        self.store.save_verdict(room.id, reply.content)
        self._persist_room(room)
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        return reply.content, participant.id

    async def follow_up(self, room_id: str, participant_id: str, text: str) -> TranscriptEntry:
        room = self.rooms[room_id]
        if room.status != "done":
            raise RuntimeError("follow-up is available only when room is done")
        participant = self._pick_verdict_participant(room, preferred_id=participant_id)
        reply, tokens_in, latency_ms = await self._send_to_participant(
            room, FOLLOW_UP_PHASE, participant, None, user_message=text
        )
        entry = make_entry(
            seq=room.transcript.next_seq(),
            phase=FOLLOW_UP_PHASE.name,
            round_number=room.current_round,
            participant_id=participant.id,
            participant_kind=participant.kind,
            role="follow_up",
            content=reply.content,
            tokens_in=tokens_in,
            tokens_out=reply.tokens_out,
            latency_ms=latency_ms,
        )
        await self._append_entry(room, participant, entry)
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        return entry

    async def _run(self, room_id: str) -> None:
        try:
            async with self._locks[room_id]:
                room = self.rooms[room_id]
                if self._is_primary_pair_room(room):
                    await self._run_primary_pair_room(room)
                    return
                while room.status == "running":
                    phase = room.current_phase()
                    participants = room.participants
                    if not participants:
                        room.status = "paused"
                        self._persist_room(room)
                        break

                    if room.style == "exhaustion-loop":
                        # Cap check: before entering a new fix phase, verify we
                        # haven't exhausted the relay budget. A new relay attempt is
                        # spent once Gemini audits the latest fixer output, even if
                        # Codex never gets a turn because Gemini found a blocking gap.
                        if phase.name == "fix" and room.exhaustion_cycle >= room.max_total_rounds:
                            room.status = "done"
                            room.converged_round = room.total_rounds()
                            room.warning_detail = "max cycles reached before consensus"
                            room.updated_at = utc_now_iso()
                            self._event(
                                room,
                                "info",
                                "exhaustion_cap_reached",
                                {"cycles": room.exhaustion_cycle, "attempts": room.exhaustion_attempts()},
                            )
                            self._persist_room(room)
                            break
                        selected = self._get_exhaustion_participant(room, phase)
                        instruction_note = self._build_exhaustion_instruction_note(room)
                        if not selected:
                            room.status = "paused"
                            room.warning_detail = f"missing required participant for exhaustion phase '{phase.name}'"
                            room.updated_at = utc_now_iso()
                            self._event(room, "warning", "exhaustion_missing_participant", {"phase": phase.name})
                            self._persist_room(room)
                            break
                    elif room.addressed_note:
                        target_id, note = room.addressed_note
                        selected = [p for p in participants if p.id == target_id]
                        instruction_note = f"Addressed note for {target_id}: {note}. Only this participant should respond now."
                    else:
                        selected = participants
                        instruction_note = None

                    if phase.mode == "parallel":
                        await self._run_parallel_turn(room, phase, selected, instruction_note)
                    else:
                        await self._run_serial_turn(room, phase, selected, instruction_note)

                    if room.status != "running":
                        break

                    room.current_round += 1
                    room.injected_instruction = None
                    room.addressed_note = None
                    room.updated_at = utc_now_iso()

                    if room.style == "exhaustion-loop":
                        transitioned = await self._handle_exhaustion_transition(room, phase)
                        if transitioned:
                            self._persist_room(room)
                            await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
                            continue
                        break

                    last_round = room.current_round - 1
                    if phase.mode == "serial":
                        last_entries = room.transcript.by_phase_round(phase.name, last_round)
                        if room.convergence.check(last_entries):
                            self._event(room, "info", "converged", {"phase": phase.name, "round": last_round})
                            room.converged_round = room.total_rounds()
                            if room.style == "critic-terminate":
                                if not self._advance_to_synthesis_or_finish(room):
                                    break
                                continue
                            if not self._advance_phase_or_finish(room):
                                break
                            continue
                        if room.style == "critic-terminate" and phase.name == "debate" and last_round == 8:
                            terminate_votes = sum(
                                1 for entry in last_entries if room._last_non_empty_line(entry.content) == "TERMINATE"
                            )
                            if terminate_votes == 0:
                                room.warning_detail = "round 8/15 with no TERMINATE votes"
                                await self.emit(
                                    {
                                        "type": "debate.warning",
                                        "room_id": room.id,
                                        "detail": room.warning_detail,
                                    }
                                )

                    if last_round >= phase.max_rounds:
                        if not self._advance_phase_or_finish(room):
                            break
                        continue

                    # Termination logic: cycles for exhaustion, turns for everything else
                    limit_met = False
                    if room.style == "exhaustion-loop":
                        if room.exhaustion_cycle >= room.max_total_rounds:
                            limit_met = True
                    elif room.total_rounds() >= room.max_total_rounds:
                        limit_met = True

                    if limit_met:
                        room.status = "done"
                        room.converged_round = room.total_rounds()
                        await self._maybe_generate_auto_verdict(room)
                        self._persist_room(room)
                        break

                    self._persist_room(room)
                    await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

                self._persist_room(room)
                if room.status == "done" and room.converged_round is not None:
                    await self._maybe_generate_auto_verdict(room)
                await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        finally:
            # Clean up our task reference only if we are still the tracked task.
            current = asyncio.current_task()
            tracked = self._tasks.get(room_id)
            if tracked is current:
                self._tasks.pop(room_id, None)

    def _is_primary_pair_room(self, room: Room) -> bool:
        return room.style == "primary-pair" or room.room_config.get("ui_mode") == "primary-pair"

    async def _run_primary_pair_room(self, room: Room) -> None:
        try:
            config = self._build_primary_pair_config(room)
            runner = PrimaryPairRunner(config)
            result = await runner.run()
            room.status = "done"
            room.converged_round = result.turns
            room.warning_detail = None if result.status == "converged" else result.stop_reason
            room.verdict_author = result.final_artifact.role_key if result.final_artifact else None
            if result.final_artifact:
                room.verdict_text = Path(result.final_artifact.path).read_text(encoding="utf-8", errors="replace")
                self.store.save_verdict(room.id, room.verdict_text)
            room.room_config = {
                **room.room_config,
                "primary_pair": {
                    **dict(room.room_config.get("primary_pair") or {}),
                    "run_id": result.run_id,
                    "run_dir": result.run_dir,
                    "ledger_path": result.ledger_path,
                    "status": result.status,
                    "stop_reason": result.stop_reason,
                    "converged": result.status == "converged",
                    "turns": result.turns,
                    "final_artifact": result.final_artifact.path if result.final_artifact else None,
                    "validation": result.validation,
                },
            }
            room.updated_at = utc_now_iso()
            self._event(room, "info", "primary_pair_complete", room.room_config["primary_pair"])
            self._persist_room(room)
            await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        except Exception as exc:
            room.status = "paused"
            room.warning_detail = f"Primary Pair failed: {type(exc).__name__}: {exc}"
            room.updated_at = utc_now_iso()
            self._event(room, "error", "primary_pair_failed", {"error_type": type(exc).__name__, "error": str(exc)})
            self._persist_room(room)
            await self.emit({"type": "error", "room_id": room.id, "detail": room.warning_detail})
            await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    def _build_primary_pair_config(self, room: Room) -> PrimaryPairConfig:
        by_role = {str(item.get("role_key")): item for item in self._role_assignments(room) if item.get("role_key")}

        def participant_for(role_key: str, fallback_index: int) -> Participant:
            spec = by_role.get(role_key)
            if spec:
                driver_id = str(spec.get("driver_id") or "")
                for participant in room.participants:
                    if participant.id == driver_id:
                        return participant
            if len(room.participants) <= fallback_index:
                raise ValueError(f"Primary Pair requires participant for {role_key}")
            return room.participants[fallback_index]

        primary_a = participant_for("primary_a", 0)
        primary_b = participant_for("primary_b", 1)
        secondary = participant_for("secondary", 2)

        def role_spec(role_key: str, participant: Participant, label: str, logical_role: str) -> RoleSpec:
            spec = by_role.get(role_key, {})
            requested_model = str(spec.get("requested_model") or "").strip() or getattr(participant.driver, "model", None)
            requested_effort = str(spec.get("effort") or "").strip() or getattr(participant.driver, "effort", None)
            if requested_model and requested_model.lower() != "default" and hasattr(participant.driver, "model"):
                setattr(participant.driver, "model", requested_model)
            if requested_effort and hasattr(participant.driver, "effort"):
                setattr(participant.driver, "effort", requested_effort)
            return RoleSpec(
                role_key=role_key,
                label=str(spec.get("label") or label),
                driver=participant.driver,
                logical_role=logical_role,
                model=requested_model,
                effort=requested_effort or None,
            )

        max_revision_turns = max(1, min(8, int(room.max_total_rounds) - 3))
        return PrimaryPairConfig(
            brief=room.topic,
            run_id=f"room-{room.id}",
            max_revision_turns=max_revision_turns,
            primary_a=role_spec("primary_a", primary_a, "LLM1", "llm1"),
            primary_b=role_spec("primary_b", primary_b, "LLM2", "llm2"),
            secondary=role_spec("secondary", secondary, "LLM3", "llm3"),
            event_callback=lambda payload: self._handle_primary_pair_event(room, payload),
        )

    async def _handle_primary_pair_event(self, room: Room, payload: dict[str, Any]) -> None:
        event = str(payload.get("event") or "")
        role = payload.get("role") if isinstance(payload.get("role"), dict) else {}
        role_key = str(role.get("role_key") or "")
        participant = self._participant_for_role_key(room, role_key)
        room.room_config = {
            **room.room_config,
            "primary_pair": {
                **dict(room.room_config.get("primary_pair") or {}),
                "run_id": payload.get("run_id"),
                "status": "running" if event != "run_complete" else payload.get("status"),
                "current_event": event,
                "current_turn_id": payload.get("turn_id"),
                "current_phase": payload.get("phase"),
                "current_artifact": payload.get("artifact_id"),
                "active_role": role_key,
                "active_participant_id": participant.id if participant else None,
                "prompt_preview": payload.get("prompt_preview"),
                "reply_preview": payload.get("reply_preview"),
                "last_marker": payload.get("reply_marker"),
                "turns": payload.get("turns", room.transcript.next_seq() - 1),
                "ledger_path": str(Path("C:/Users/chris/PROJECTS/agora/data/primary-pair-runs") / f"room-{room.id}" / "turn-ledger.jsonl"),
            },
        }
        room.updated_at = utc_now_iso()
        self._event(room, "debug", f"primary_pair_{event}", payload)
        self._persist_room(room)
        await self.emit({"type": "primary_pair.event", "room_id": room.id, "event": payload})
        if participant and event == "turn_start":
            await self.emit({"type": "participant.thinking", "room_id": room.id, "participant_id": participant.id, "in_flight": True})
        if participant and event in {"turn_complete", "turn_invalid_retry"}:
            await self.emit({"type": "participant.thinking", "room_id": room.id, "participant_id": participant.id, "in_flight": False})
        if participant and event == "turn_complete":
            reply_path = payload.get("reply_path")
            content = Path(str(reply_path)).read_text(encoding="utf-8", errors="replace") if reply_path else str(payload.get("reply_preview") or "")
            artifact_id = str(payload.get("artifact_id") or payload.get("phase") or "primary_pair")
            entry = make_entry(
                seq=room.transcript.next_seq(),
                phase=artifact_id,
                round_number=self._primary_pair_logical_round(artifact_id, int(payload.get("attempt") or 1)),
                participant_id=participant.id,
                participant_kind=participant.kind,
                role="participant",
                content=content,
                tokens_in=0,
                tokens_out=0,
                latency_ms=int(payload.get("latency_ms") or 0),
            )
            await self._append_entry(room, participant, entry)
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    def _participant_for_role_key(self, room: Room, role_key: str) -> Participant | None:
        for item in self._role_assignments(room):
            if item.get("role_key") == role_key:
                driver_id = item.get("driver_id")
                for participant in room.participants:
                    if participant.id == driver_id:
                        return participant
        return None

    def _role_assignments(self, room: Room) -> list[dict[str, object]]:
        assignments = room.room_config.get("role_assignments")
        return [item for item in assignments if isinstance(item, dict)] if isinstance(assignments, list) else []

    @staticmethod
    def _primary_pair_logical_round(artifact_id: str, fallback: int) -> int:
        match = re.match(r"^([ABS])(\d+)$", artifact_id or "")
        if not match:
            return max(1, fallback)
        family, ordinal_text = match.groups()
        ordinal = int(ordinal_text)
        if family == "S":
            return 1
        if ordinal == 0:
            return 1
        if ordinal == 1:
            return 2
        return ordinal + 1

    async def _run_serial_turn(self, room: Room, phase: Phase, selected: list[Participant], instruction_note: str | None) -> None:
        for participant in selected:
            if room.status != "running":
                break
            reply, tokens_in, latency_ms = await self._send_to_participant(room, phase, participant, instruction_note)
            await self._append_reply(room, phase, participant, reply, tokens_in, latency_ms)

    async def _run_parallel_turn(self, room: Room, phase: Phase, selected: list[Participant], instruction_note: str | None) -> None:
        async def worker(participant: Participant) -> tuple[Participant, DriverReply | None, int, int, str | None]:
            if room.status != "running":
                return participant, None, 0, 0, "room not running"
            try:
                reply, tokens_in, latency_ms = await self._send_to_participant(room, phase, participant, instruction_note)
                return participant, reply, tokens_in, latency_ms, None
            except DriverError as exc:
                return participant, None, 0, 0, str(exc)

        results = await asyncio.gather(*(worker(participant) for participant in selected))
        for participant, reply, tokens_in, latency_ms, error in sorted(results, key=lambda item: item[0].id):
            if error is not None:
                entry = make_entry(
                    seq=room.transcript.next_seq(),
                    phase=phase.name,
                    round_number=room.current_round,
                    participant_id=participant.id,
                    participant_kind=participant.kind,
                    role="participant",
                    content="",
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=0,
                    error=error,
                )
                self._event(room, "error", "driver_error", {"participant_id": participant.id, "detail": error})
                await self._append_entry(room, participant, entry)
                continue
            assert reply is not None
            await self._append_reply(room, phase, participant, reply, tokens_in, latency_ms)

    async def _send_to_participant(
        self,
        room: Room,
        phase: Phase,
        participant: Participant,
        instruction_note: str | None,
        user_message: str | None = None,
    ) -> tuple[DriverReply, int, int]:
        await self.emit(
            {"type": "participant.thinking", "room_id": room.id, "participant_id": participant.id, "in_flight": True}
        )
        start = perf_counter()
        ledger_id = f"{room.transcript.next_seq()}-{participant.id}-{phase.name}-{room.current_round}"
        try:
            requested_model = self._requested_model_for(room, participant)
            if requested_model:
                self._apply_requested_model(participant, requested_model)
            room_frame = self._build_room_frame(room, participant, phase, instruction_note)
            delta = user_message or self._build_delta(room, participant, phase, instruction_note)
            had_session = await participant.driver.has_session(room.id)
            self._turn_ledger(
                room,
                {
                    "event": "turn_start",
                    "ledger_id": ledger_id,
                    "phase": phase.name,
                    "round": room.current_round,
                    "participant_id": participant.id,
                    "participant_kind": participant.kind,
                    "participant_label": participant.display_name,
                    "role": self._role_spec_for(room, participant).get("role_key"),
                    "role_label": self._role_spec_for(room, participant).get("label"),
                    "requested_model": requested_model,
                    "driver_model": getattr(participant.driver, "model", None),
                    "had_session": had_session,
                    "session_id_before": self._driver_session_id(participant.driver, room.id),
                    "prompt_kind": "follow_up" if user_message else ("delta" if had_session else "room_frame"),
                    "prompt_sha256": self._sha256(delta if had_session else room_frame),
                    "prompt_preview": self._preview(delta if had_session else room_frame),
                    "include_opponents": phase.include_opponents,
                    "opponent_latest": self._opponent_latest_summary(room, participant, phase),
                },
            )
            if not await participant.driver.has_session(room.id):
                await participant.driver.start_session(room.id, room_frame)
            budget = TokenBudget(ceiling=participant.driver.token_ceiling)
            tokens_in = budget.count(delta)
            reply = await participant.driver.send_in_session(room.id, delta)
            latency_ms = int((perf_counter() - start) * 1000)
            self._turn_ledger(
                room,
                {
                    "event": "turn_complete",
                    "ledger_id": ledger_id,
                    "phase": phase.name,
                    "round": room.current_round,
                    "participant_id": participant.id,
                    "requested_model": requested_model,
                    "driver_model": getattr(participant.driver, "model", None),
                    "session_id_after": self._driver_session_id(participant.driver, room.id) or reply.resume_id,
                    "reply_resume_id": reply.resume_id,
                    "latency_ms": latency_ms,
                    "tokens_in": tokens_in,
                    "tokens_out": reply.tokens_out,
                    "reply_sha256": self._sha256(reply.content),
                    "reply_preview": self._preview(reply.content),
                },
            )

            # Extract and emit thoughts if present
            thoughts = self._extract_thoughts(reply.content)
            if thoughts:
                await self.emit({
                    "type": "participant.thought",
                    "room_id": room.id,
                    "participant_id": participant.id,
                    "content": thoughts
                })

            if participant.kind == "gemini-cli" and self._looks_like_tool_use(reply.content):
                self._event(
                    room,
                    "warning",
                    "driver_error",
                    {
                        "participant_id": participant.id,
                        "detail": "gemini output indicates tool-use drift; raw output included in transcript",
                    },
                )
                if reply.raw_output:
                    reply = DriverReply(
                        content=f"{reply.content}\n\n[RAW OUTPUT]\n{reply.raw_output}",
                        raw_output=reply.raw_output,
                        tokens_out=reply.tokens_out,
                        resume_id=reply.resume_id,
                    )
            return reply, tokens_in, latency_ms
        except Exception as exc:
            self._turn_ledger(
                room,
                {
                    "event": "turn_error",
                    "ledger_id": ledger_id,
                    "phase": phase.name,
                    "round": room.current_round,
                    "participant_id": participant.id,
                    "requested_model": self._requested_model_for(room, participant),
                    "driver_model": getattr(participant.driver, "model", None),
                    "session_id": self._driver_session_id(participant.driver, room.id),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:2000],
                },
            )
            raise
        finally:
            await self.emit(
                {"type": "participant.thinking", "room_id": room.id, "participant_id": participant.id, "in_flight": False}
            )

    async def _append_reply(
        self,
        room: Room,
        phase: Phase,
        participant: Participant,
        reply: DriverReply,
        tokens_in: int,
        latency_ms: int,
        role: str = "participant",
    ) -> None:
        entry = make_entry(
            seq=room.transcript.next_seq(),
            phase=phase.name,
            round_number=room.current_round,
            participant_id=participant.id,
            participant_kind=participant.kind,
            role=role,
            content=reply.content,
            tokens_in=tokens_in,
            tokens_out=reply.tokens_out,
            latency_ms=latency_ms,
        )
        await self._append_entry(room, participant, entry)

    async def _append_entry(self, room: Room, participant: Participant, entry: TranscriptEntry) -> None:
        room.transcript.append(entry)
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        self.store.append_transcript(room.id, entry.to_dict())
        await self.emit({"type": "message.append", "room_id": room.id, "message": entry.to_dict()})
        await self.emit(
            {
                "type": "participant.status",
                "room_id": room.id,
                "participant_id": participant.id,
                "status": "replied" if entry.error is None else "error",
            }
        )

    def _phase_instruction(self, room: Room, phase: Phase, instruction_note: str | None) -> str:
        instruction = phase.instruction_template
        if room.injected_instruction:
            instruction = f"{room.injected_instruction}\n\n{instruction}"
        if instruction_note:
            instruction = f"{instruction_note}\n\n{instruction}"
        if phase.name == "debate":
            instruction = f"{instruction}\n\nDebate phase max rounds for this room: {phase.max_rounds}."
        return instruction

    def _build_room_frame(self, room: Room, participant: Participant, phase: Phase, instruction_note: str | None) -> str:
        if participant.kind == "gemini-cli" and room.style == "exhaustion-loop":
            return (
                "Run this audit now and return the findings in this reply.\n"
                "Do not say you are ready. Do not ask what to do. Do the work now.\n\n"
                f"Brief: {room.topic}\n"
                f"Phase: {phase.name}\n"
                f"Instruction: {self._phase_instruction(room, phase, instruction_note)}\n\n"
                "Requirements:\n"
                "- Inspect the target directory and the DoD document.\n"
                "- Use shell commands as needed.\n"
                "- If tests exist, run them.\n"
                "- If the codebase is perfect, your final line must be exactly: ZERO FINDINGS\n"
                "- Otherwise list the concrete gaps and missing fixes.\n\n"
                f"{self._build_exhaustion_instruction_note(room)}\n"
            )
        payload = RoomFrameInput(
            topic=room.topic,
            phase_name=phase.name,
            phase_instruction=self._phase_instruction(room, phase, instruction_note),
            participants=[
                ParticipantPromptView(
                    participant_id=p.id,
                    display_name=p.display_name,
                    kind=p.kind,
                    last_content="",
                )
                for p in room.participants
                if p.id != participant.id
            ],
            self_view=ParticipantPromptView(
                participant_id=participant.id,
                display_name=participant.display_name,
                kind=participant.kind,
                last_content="",
            ),
        )
        if participant.kind == "gemini-cli" and room.style != "exhaustion-loop":
            payload.phase_instruction = (
                "You are a debate participant. Do not read files, do not run shell commands, do not inspect "
                "the workspace. Answer the debate brief as a commentator. All arguments must be in plain text.\n\n"
                + payload.phase_instruction
            )
        return self.renderer.render_room_frame(payload)

    def _build_delta(self, room: Room, participant: Participant, phase: Phase, instruction_note: str | None) -> str:
        if participant.kind == "gemini-cli" and room.style == "exhaustion-loop":
            self_last = room.transcript.latest_by_participant(participant.id)
            return (
                "Continue the audit now and return the findings in this reply.\n"
                "Do not say you are ready. Do not ask what to do next. Do the work now.\n\n"
                f"Brief: {room.topic}\n"
                f"Phase: {phase.name}\n"
                f"Round: {room.current_round}\n"
                f"Your last response:\n{self_last.content if self_last else '(none yet)'}\n\n"
                f"Instruction: {self._phase_instruction(room, phase, instruction_note)}\n\n"
                "Requirements:\n"
                "- Inspect the current target state and the DoD.\n"
                "- Use shell commands as needed.\n"
                "- If tests exist, run them.\n"
                "- If the codebase is perfect, your final line must be exactly: ZERO FINDINGS\n"
                "- Otherwise list the concrete gaps and missing fixes.\n\n"
                f"{self._build_exhaustion_instruction_note(room)}\n"
            )
        opponents: list[ParticipantPromptView] = []
        if phase.include_opponents:
            for peer in room.participants:
                if peer.id == participant.id:
                    continue
                last = room.transcript.latest_by_participant(peer.id)
                opponents.append(
                    ParticipantPromptView(
                        participant_id=peer.id,
                        display_name=peer.display_name,
                        kind=peer.kind,
                        last_content=(last.content if last else "(none yet)"),
                    )
                )
        self_last = room.transcript.latest_by_participant(participant.id)
        payload = DeltaInput(
            phase_name=phase.name,
            round_number=room.current_round,
            include_opponents=phase.include_opponents,
            opponents=opponents,
            phase_instruction=self._phase_instruction(room, phase, instruction_note),
            self_last=(self_last.content if self_last else ""),
        )
        return self.renderer.render_delta(payload)

    def _advance_phase_or_finish(self, room: Room) -> bool:
        if room.current_phase_index + 1 < len(room.phase_sequence):
            room.current_phase_index += 1
            room.current_round = 1
            room.updated_at = utc_now_iso()
            self._event(room, "info", "phase_changed", {"phase": room.current_phase().name})
            self._persist_room(room)
            return True
        room.status = "done"
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        return False

    def _advance_to_synthesis_or_finish(self, room: Room) -> bool:
        for idx, phase in enumerate(room.phase_sequence):
            if phase.name == "synthesis":
                room.current_phase_index = idx
                room.current_round = 1
                room.updated_at = utc_now_iso()
                self._event(room, "info", "phase_changed", {"phase": phase.name})
                self._persist_room(room)
                return True
        return self._advance_phase_or_finish(room)

    async def _maybe_generate_auto_verdict(self, room: Room) -> None:
        if not room.auto_verdict or room.verdict_text:
            return
        verdict, author = await self.regenerate_verdict(room.id, participant_id=None)
        room.verdict_text = verdict
        room.verdict_author = author

    def _pick_verdict_participant(self, room: Room, preferred_id: str | None = None) -> Participant:
        participants_by_id = {participant.id: participant for participant in room.participants}
        if preferred_id:
            participant = participants_by_id.get(preferred_id)
            if participant is None:
                raise ValueError(f"Unknown participant: {preferred_id}")
            return participant

        candidates: list[tuple[Participant, int, bool]] = []
        for participant in room.participants:
            latest = room.transcript.latest_by_participant(participant.id)
            content = latest.content if latest else ""
            compact = len("".join(content.split()))
            marker = room._last_non_empty_line(content)
            has_marker = marker in {"AGREE", "TERMINATE"}
            candidates.append((participant, compact, has_marker))

        marker_candidates = [candidate for candidate in candidates if candidate[2]]
        scoped = marker_candidates if marker_candidates else candidates
        if not scoped:
            raise ValueError("Room has no participants")
        scoped.sort(key=lambda item: (-item[1], item[0].id))
        return scoped[0][0]

    def _build_exhaustion_instruction_note(self, room: Room) -> str:
        parts: list[str] = []
        if room.target_file:
            parts.append(f"Target Directory: {room.target_file}")
        if room.dod_file:
            parts.append(f"DoD Document: {room.dod_file}")
        return "\n".join(parts)

    def _get_exhaustion_participant(self, room: Room, phase: Phase) -> list[Participant]:
        kind_map = {
            "fix": "claude-code-new",
            "audit_gemini": "gemini-cli",
            "audit_codex": "codex",
        }
        target_kind = kind_map.get(phase.name)
        if not target_kind:
            return []
        for participant in room.participants:
            if participant.kind == target_kind:
                return [participant]
        return []

    def _looks_like_tool_use(self, text: str) -> bool:
        lowered = text.lower()
        structural_markers = [
            "```tool_code",
            "```shell",
            "```bash",
            '"tool_calls":',
            '"tool_call":',
            "<tool_code",
            "[tool_call",
            "function_call:",
            "```python\n# i'll",
        ]
        return any(marker in lowered for marker in structural_markers)

    def _extract_thoughts(self, text: str) -> str | None:
        """Extracts content from <thought> tags if present."""
        import re
        match = re.search(r"<thought>(.*?)</thought>", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _role_spec_for(self, room: Room, participant: Participant) -> dict[str, object]:
        assignments = room.room_config.get("role_assignments")
        if not isinstance(assignments, list):
            return {}
        for item in assignments:
            if isinstance(item, dict) and item.get("driver_id") == participant.id:
                return item
        return {}

    def _requested_model_for(self, room: Room, participant: Participant) -> str | None:
        spec = self._role_spec_for(room, participant)
        requested = str(spec.get("requested_model") or "").strip()
        if requested and requested.lower() != "default":
            return requested
        return None

    def _apply_requested_model(self, participant: Participant, requested_model: str) -> None:
        if hasattr(participant.driver, "model"):
            setattr(participant.driver, "model", requested_model)

    def _driver_session_id(self, driver: Driver, room_id: str) -> str | None:
        sessions = getattr(driver, "sessions", None)
        if isinstance(sessions, dict):
            value = sessions.get(room_id)
            return str(value) if value else None
        return None

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _preview(value: str, limit: int = 1200) -> str:
        clean = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        return clean[:limit] + ("...[truncated]" if len(clean) > limit else "")

    def _opponent_latest_summary(self, room: Room, participant: Participant, phase: Phase) -> list[dict[str, object]]:
        if not phase.include_opponents:
            return []
        output: list[dict[str, object]] = []
        for peer in room.participants:
            if peer.id == participant.id:
                continue
            latest = room.transcript.latest_by_participant(peer.id)
            output.append(
                {
                    "participant_id": peer.id,
                    "display_name": peer.display_name,
                    "has_latest": latest is not None,
                    "latest_phase": latest.phase if latest else None,
                    "latest_round": latest.round if latest else None,
                    "latest_sha256": self._sha256(latest.content) if latest else None,
                    "latest_preview": self._preview(latest.content, limit=300) if latest else None,
                }
            )
        return output

    def _turn_ledger(self, room: Room, detail: dict[str, object]) -> None:
        line = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **detail,
        }
        self.store.append_turn_ledger(room.id, line)
        self._event(room, "debug", str(detail.get("event", "turn_ledger")), detail)

    async def _handle_exhaustion_transition(self, room: Room, phase: Phase) -> bool:
        last_entry = room.transcript.entries[-1] if room.transcript.entries else None
        if not last_entry or last_entry.error:
            room.status = "paused"
            room.updated_at = utc_now_iso()
            self._event(room, "warning", "exhaustion_paused", {"phase": phase.name})
            return False

        if phase.name == "fix":
            await self.set_phase(room.id, "audit_gemini")
            return True

        last_line = room._last_non_empty_line(last_entry.content).upper()
        has_zero_findings = last_line == "ZERO FINDINGS"

        if phase.name == "audit_gemini":
            if has_zero_findings:
                await self.set_phase(room.id, "audit_codex")
                return True
            room.exhaustion_cycle += 1
            await self.set_phase(room.id, "fix")
            return True

        if phase.name == "audit_codex":
            # A full exhaustion cycle completes only once Codex has audited the
            # current fixer output, regardless of pass or fail.
            room.exhaustion_cycle += 1
            if has_zero_findings:
                room.status = "done"
                room.converged_round = room.total_rounds()
                room.updated_at = utc_now_iso()
                self._event(
                    room,
                    "info",
                    "converged",
                    {"detail": "Exhaustion met: Both Gemini and Codex reported ZERO FINDINGS."},
                )
                return False
            await self.set_phase(room.id, "fix")
            return True

        return False

    def _event(self, room: Room, level: str, event: str, detail: dict[str, object]) -> None:
        line = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "level": level, "event": event, "detail": detail}
        self.store.append_event(room.id, line)

    def _persist_room(self, room: Room) -> None:
        self.store.save_room(room.id, room.to_state())
