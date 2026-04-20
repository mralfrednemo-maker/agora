from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Awaitable, Callable, Literal
from uuid import uuid4

from agora.config.phases import DEFAULT_STYLE, Phase, phase_by_name, phases_for_style
from agora.drivers.base import Driver, DriverError, DriverReply
from agora.engine.budget import BudgetManager, TokenBudget, stub_summarizer
from agora.engine.convergence import ConvergenceCheck, build_convergence
from agora.engine.templates import DeltaInput, ParticipantPromptView, RoomFrameInput, default_renderer
from agora.engine.transcript import Transcript, TranscriptEntry, make_entry, utc_now_iso
from agora.persistence.store import RoomStore

RoomStatus = Literal["idle", "running", "paused", "done"]
EventEmitter = Callable[[dict[str, object]], Awaitable[None]]
VERDICT_PROMPT = (
    "The debate has concluded. Produce a final verdict document in markdown. Include:\n"
    "- The brief (restated verbatim).\n"
    "- Shared conclusions (bulleted).\n"
    "- Residual disagreements (bulleted, or \"None.\").\n"
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

    def current_phase(self) -> Phase:
        return self.phase_sequence[self.current_phase_index]

    def total_rounds(self) -> int:
        keys = {(entry.phase, entry.round) for entry in self.transcript.entries if entry.role == "participant"}
        return len(keys)

    def _next_up(self) -> str | None:
        if self.status != "running" or not self.participants:
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
                status="paused" if status_raw in {"running", "paused"} else status_raw,  # crash-safe restore
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
    ) -> Room:
        total = max(4, max_total_rounds)
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
        for participant in room.participants:
            await participant.driver.close_session(room_id)
        self.store.delete_room(room_id)
        await self.emit({"type": "room.deleted", "room_id": room_id})

    async def set_participants(self, room_id: str, participant_ids: list[str]) -> None:
        room = self.rooms[room_id]
        selected: list[Participant] = []
        for driver_id in participant_ids:
            driver = self.drivers[driver_id]
            selected.append(Participant(id=driver_id, kind=driver.kind, display_name=driver_id, driver=driver))
        room.participants = selected
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})

    async def start(self, room_id: str) -> None:
        room = self.rooms[room_id]
        if room.status == "running":
            return
        room.status = "running"
        room.updated_at = utc_now_iso()
        self._persist_room(room)
        self._event(room, "info", "room_started", {})
        await self.emit({"type": "room.update", "room_id": room.id, "state": self.room_snapshot(room.id)})
        self._tasks[room_id] = asyncio.create_task(self._run(room_id), name=f"agora-room-{room_id}")

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
        self._tasks[room_id] = asyncio.create_task(self._run(room_id), name=f"agora-room-{room_id}")

    async def stop(self, room_id: str) -> None:
        room = self.rooms[room_id]
        room.status = "done"
        room.updated_at = utc_now_iso()
        self._persist_room(room)
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
        room = self.rooms[room_id]
        chosen = model or "haiku"
        text = f"[M2 STUB] Synthesis for room {room.id} using {chosen} is not implemented in M2."
        self._event(room, "info", "synthesized", {"model": chosen})
        return text

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
        lock = self._locks[room_id]
        async with lock:
            room = self.rooms[room_id]
            while room.status == "running":
                phase = room.current_phase()
                participants = room.participants
                if not participants:
                    room.status = "paused"
                    self._persist_room(room)
                    break

                if room.addressed_note:
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

                room.current_round += 1
                room.injected_instruction = None
                room.addressed_note = None
                room.updated_at = utc_now_iso()

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

                if room.total_rounds() >= room.max_total_rounds:
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
        try:
            room_frame = self._build_room_frame(room, participant, phase, instruction_note)
            delta = user_message or self._build_delta(room, participant, phase, instruction_note)
            if not await participant.driver.has_session(room.id):
                await participant.driver.start_session(room.id, room_frame)
            budget = TokenBudget(ceiling=participant.driver.token_ceiling)
            tokens_in = budget.count(delta)
            reply = await participant.driver.send_in_session(room.id, delta)
            latency_ms = int((perf_counter() - start) * 1000)
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
        if participant.kind == "gemini-cli":
            payload.phase_instruction = (
                "You are a debate participant. Do not read files, do not run shell commands, do not inspect "
                "the workspace. Answer the debate brief as a commentator. All arguments must be in plain text.\n\n"
                + payload.phase_instruction
            )
        return self.renderer.render_room_frame(payload)

    def _build_delta(self, room: Room, participant: Participant, phase: Phase, instruction_note: str | None) -> str:
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
        payload = DeltaInput(
            phase_name=phase.name,
            round_number=room.current_round,
            include_opponents=phase.include_opponents,
            opponents=opponents,
            phase_instruction=self._phase_instruction(room, phase, instruction_note),
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
        room.converged_round = room.total_rounds()
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
        try:
            verdict, author = await self.regenerate_verdict(room.id, participant_id=None)
            room.verdict_text = verdict
            room.verdict_author = author
        except Exception as exc:
            self._event(room, "error", "auto_verdict_failed", {"detail": str(exc)})

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

    def _looks_like_tool_use(self, text: str) -> bool:
        """Detect Gemini CLI tool-use drift via structural markers only.
        Prose-substring matching produces too many false positives on
        legitimate debate replies that happen to contain words like "read"
        or "file". Look for concrete tool-call scaffolding instead: fenced
        shell blocks, tool-call JSON shapes, or Gemini's canonical
        Linked-Devices / function-call headers.
        """
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

    def _event(self, room: Room, level: str, event: str, detail: dict[str, object]) -> None:
        line = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "level": level, "event": event, "detail": detail}
        self.store.append_event(room.id, line)

    def _persist_room(self, room: Room) -> None:
        self.store.save_room(room.id, room.to_state())
