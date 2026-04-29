from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agora.commands.handlers import CommandHandler
from agora.config.phases import MIN_TOTAL_ROUNDS, STYLE_ROUND_CAPS
from agora.engine.live_handover import LiveHandoverService

# Styles defined in the M3 spec
SPEC_STYLES = ("ein-mdp", "critic-terminate", "primary-pair")
from agora.engine.room import RoomEngine


@dataclass(slots=True)
class ApiService:
    engine: RoomEngine
    commands: CommandHandler
    driver_health: dict[str, dict[str, object]]
    live_handover: LiveHandoverService | None = None

    async def list_rooms(self) -> list[dict[str, object]]:
        return self.engine.list_rooms()

    async def get_room(self, room_id: str) -> dict[str, object]:
        return self.engine.room_snapshot(room_id)

    async def create_room(self, topic: str) -> dict[str, object]:
        room = await self.engine.create_room(topic=topic)
        self.commands.context.active_room_id = room.id
        return {"room_id": room.id}

    def _validate_style(self, style: str) -> str:
        selected_style = style.strip().lower() if style else "ein-mdp"
        if selected_style not in SPEC_STYLES and selected_style not in ("exhaustion-loop",):
            known = ", ".join(sorted(SPEC_STYLES))
            raise ValueError(f"Unknown style '{style}'. Known styles: {known}")
        return selected_style

    def _validate_max_total_rounds(self, style: str, max_total_rounds: int) -> None:
        if max_total_rounds < MIN_TOTAL_ROUNDS:
            raise ValueError(f"max_total_rounds must be >= {MIN_TOTAL_ROUNDS}")
        cap = STYLE_ROUND_CAPS.get(style, STYLE_ROUND_CAPS["ein-mdp"])
        if max_total_rounds > cap:
            raise ValueError(f"max_total_rounds must be <= {cap} for style '{style}'")

    def _validate_participants(self, participants: list[str], style: str) -> None:
        # Always validate that every participant ID is a known driver
        for participant_id in participants:
            if participant_id not in self.engine.drivers:
                raise ValueError(f"Unknown participant: {participant_id}")

        if style != "exhaustion-loop":
            return

        # Exhaustion-loop requires exactly 3 participants: claude-code-new, gemini-cli, codex
        if len(participants) != 3:
            raise ValueError(
                f"Exhaustion Loop requires exactly 3 participants (claude-code-new, gemini-cli, codex). "
                f"Got {len(participants)}."
            )
        participant_kinds = {self.engine.drivers[pid].kind for pid in participants}
        required = {"claude-code-new", "gemini-cli", "codex"}
        missing = sorted(required - participant_kinds)
        if missing:
            raise ValueError(
                "Exhaustion Loop requires one each of claude-code-new, gemini-cli, and codex participants. "
                f"Missing kinds: {missing}"
            )

    def _resolve_exhaustion_path(self, raw_path: str, *, expect_directory: bool, label: str) -> str:
        agora_root = Path(__file__).parent.parent.parent.parent.resolve()
        path = (agora_root / raw_path) if not os.path.isabs(raw_path) else Path(raw_path)
        if not path.exists():
            raise ValueError(f"{label} path does not exist: {raw_path}")
        if expect_directory and not path.is_dir():
            raise ValueError(f"{label} path must be a directory: {raw_path}")
        if not expect_directory and not path.is_file():
            raise ValueError(f"{label} path must be a file: {raw_path}")
        return str(path.resolve())

    async def start_room(
        self,
        topic: str,
        participants: list[str],
        max_total_rounds: int,
        convergence: str,
        style: str,
        auto_verdict: bool,
        target_file: str | None = None,
        dod_file: str | None = None,
        ui_mode: str | None = None,
        role_assignments: list[dict[str, object]] | None = None,
        workflow_notes: str | None = None,
    ) -> dict[str, object]:
        selected_style = self._validate_style(style)
        self._validate_max_total_rounds(selected_style, max_total_rounds)
        self._validate_participants(participants, selected_style)

        if selected_style == "exhaustion-loop":
            auto_verdict = False  # exhaustion workflow produces no meaningful verdict
            if not target_file or not dod_file:
                raise ValueError("Exhaustion Loop requires both Target Directory and DoD Document paths.")
            target_file = self._resolve_exhaustion_path(
                target_file,
                expect_directory=True,
                label="Target",
            )
            dod_file = self._resolve_exhaustion_path(
                dod_file,
                expect_directory=False,
                label="DoD",
            )

        room = await self.engine.create_room(
            topic=topic,
            convergence_name=convergence,
            max_total_rounds=max_total_rounds,
            style=selected_style,
            auto_verdict=auto_verdict,
            target_file=target_file,
            dod_file=dod_file,
            room_config={
                "ui_mode": ui_mode or selected_style,
                "role_assignments": list(role_assignments or []),
                "workflow_notes": workflow_notes or "",
            },
        )
        await self.engine.set_participants(room.id, participants, participant_specs=role_assignments)
        await self.engine.start(room.id)
        self.commands.context.active_room_id = room.id
        return {"room_id": room.id}

    async def archive_room(self, room_id: str) -> dict[str, object]:
        await self.engine.archive_room(room_id)
        return {"ok": True}

    async def delete_room(self, room_id: str) -> dict[str, object]:
        await self.engine.delete_room(room_id)
        return {"ok": True}

    async def command(self, room_id: str, text: str) -> dict[str, object]:
        return await self.commands.handle(text=text, room_id=room_id)

    async def regenerate_verdict(self, room_id: str, participant_id: str | None) -> dict[str, object]:
        verdict, author = await self.engine.regenerate_verdict(room_id=room_id, participant_id=participant_id)
        return {"verdict": verdict, "author": author}

    async def follow_up(self, room_id: str, participant_id: str, text: str) -> dict[str, object]:
        message = await self.engine.follow_up(room_id=room_id, participant_id=participant_id, text=text)
        return {"message": message.to_dict()}

    async def list_drivers(self) -> dict[str, object]:
        rows = []
        for driver in self.engine.drivers.values():
            health = self.driver_health.get(driver.id, {"ok": True, "detail": "ok"})
            rows.append(
                {
                    "id": driver.id,
                    "display_name": getattr(driver, "display_name", driver.id),
                    "kind": driver.kind,
                    "token_ceiling": driver.token_ceiling,
                    "current_model": getattr(driver, "model", None),
                    "health": health,
                }
            )
        return {"drivers": rows}

    async def list_filesystem(self, raw_path: str = ".") -> dict[str, object]:
        projects_root = Path("C:/Users/chris/PROJECTS").resolve()
        
        if not raw_path or raw_path == ".":
            target = projects_root
        else:
            target = Path(raw_path)
            if not target.is_absolute():
                target = (projects_root / raw_path).resolve()
            else:
                target = target.resolve()
        
        if projects_root not in target.parents and target != projects_root:
            target = projects_root

        items = []
        try:
            for p in target.iterdir():
                items.append({
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "rel_path": str(p.relative_to(projects_root)).replace("\\", "/")
                })
        except Exception as e:
            raise ValueError(f"Failed to list {target}: {e}")

        return {
            "current_path": str(target.relative_to(projects_root)).replace("\\", "/"),
            "full_path": str(target).replace("\\", "/"),
            "items": sorted(items, key=lambda x: (not x["is_dir"], x["name"].lower()))
        }

    async def attach_live_link(self, label: str, driver_id: str, external_session_ref: str) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        result = await self.live_handover.attach_live_link(
            label=label,
            driver_id=driver_id,
            external_session_ref=external_session_ref,
        )
        return {
            "live_link_id": result.live_link_id,
            "driver_id": result.driver_id,
            "driver_kind": result.driver_kind,
            "external_session_ref": result.external_session_ref,
            "canonical_external_session_ref": result.canonical_external_session_ref,
            "binding_room_id": result.binding_room_id,
            "model_identity": result.model_identity,
        }

    async def list_live_links(self) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        return {"live_links": self.live_handover.list_live_links()}

    async def run_live_handover(
        self,
        *,
        goal: str,
        interviewer_link_id: str,
        source_link_id: str,
        max_interview_turns: int,
        max_total_wakes: int,
        max_invalid_outputs_per_agent: int,
        max_runtime_minutes: int,
    ) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        result = await self.live_handover.run_workflow(
            goal=goal,
            interviewer_link_id=interviewer_link_id,
            source_link_id=source_link_id,
            max_interview_turns=max_interview_turns,
            max_total_wakes=max_total_wakes,
            max_invalid_outputs_per_agent=max_invalid_outputs_per_agent,
            max_runtime_minutes=max_runtime_minutes,
        )
        return {
            "workflow_id": result.workflow_id,
            "status": result.status,
            "stop_reason": result.stop_reason,
            "final_artifact_id": result.final_artifact_id,
            "audit_ok": result.audit_ok,
            "run_dir": result.run_dir,
        }

    async def get_live_handover_workflow(self, workflow_id: str) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        workflow = self.live_handover.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        return workflow

    async def get_live_handover_audit(self, workflow_id: str) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        return self.live_handover.get_workflow_audit(workflow_id)

    async def ask_agent(
        self,
        *,
        question: str,
        source_driver_id: str,
        source_session_ref: str,
        source_label: str | None = None,
        interviewer_driver_id: str = "codex-1",
        interviewer_session_ref: str | None = None,
        interviewer_label: str | None = None,
        max_interview_turns: int = 1,
        max_total_wakes: int = 4,
        max_invalid_outputs_per_agent: int = 2,
        max_runtime_minutes: int = 10,
    ) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        result = await self.live_handover.ask_agent(
            question=question,
            source_driver_id=source_driver_id,
            source_session_ref=source_session_ref,
            source_label=source_label,
            interviewer_driver_id=interviewer_driver_id,
            interviewer_session_ref=interviewer_session_ref,
            interviewer_label=interviewer_label,
            max_interview_turns=max_interview_turns,
            max_total_wakes=max_total_wakes,
            max_invalid_outputs_per_agent=max_invalid_outputs_per_agent,
            max_runtime_minutes=max_runtime_minutes,
        )
        return {
            "workflow_id": result.workflow_id,
            "status": result.status,
            "audit_ok": result.audit_ok,
            "interviewer_link_id": result.interviewer_link_id,
            "source_link_id": result.source_link_id,
            "final_artifact_path": result.final_artifact_path,
            "final_artifact_markdown": result.final_artifact_markdown,
            "run_dir": result.run_dir,
        }

    async def send_agent_message(
        self,
        *,
        to_link_id: str,
        body: str,
        from_link_id: str | None = None,
        subject: str | None = None,
        requires_ack: bool = True,
    ) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        result = self.live_handover.send_agent_message(
            from_link_id=from_link_id,
            to_link_id=to_link_id,
            subject=subject,
            body=body,
            requires_ack=requires_ack,
        )
        return {
            "message_id": result.message_id,
            "from_link_id": result.from_link_id,
            "to_link_id": result.to_link_id,
            "status": result.status,
            "body": result.body,
            "response_body": result.response_body,
            "response": result.response,
            "error_text": result.error_text,
            "requires_ack": result.requires_ack,
        }

    async def list_agent_messages(
        self,
        *,
        to_link_id: str | None = None,
        status: str | None = None,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        return {
            "messages": self.live_handover.list_agent_messages(
                to_link_id=to_link_id,
                status=status,
                include_terminal=include_terminal,
                limit=limit,
            )
        }

    async def mark_agent_message_read(self, message_id: str) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        result = self.live_handover.mark_agent_message_read(message_id)
        return {
            "message_id": result.message_id,
            "status": result.status,
            "response_body": result.response_body,
            "response": result.response,
            "error_text": result.error_text,
        }

    async def acknowledge_agent_message(self, message_id: str) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        result = self.live_handover.acknowledge_agent_message(message_id)
        return {
            "message_id": result.message_id,
            "status": result.status,
            "response_body": result.response_body,
            "response": result.response,
            "error_text": result.error_text,
        }

    async def process_agent_inbox_once(self, to_link_id: str) -> dict[str, object]:
        if self.live_handover is None:
            raise ValueError("live handover service is not configured")
        result = await self.live_handover.process_agent_inbox_once(to_link_id=to_link_id)
        if result is None:
            return {"processed": False}
        return {
            "processed": True,
            "message_id": result.message_id,
            "status": result.status,
            "response_body": result.response_body,
            "response": result.response,
            "error_text": result.error_text,
        }
