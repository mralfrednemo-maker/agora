from __future__ import annotations

import asyncio
import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest

from agora.drivers.base import Driver, DriverError, DriverReply
from agora.engine.live_handover import ATTACH_ACK, LiveHandoverService
from agora.persistence.live_handover_store import LiveHandoverStore


def _workspace_tmp(name: str) -> Path:
    root = Path("C:/Users/chris/PROJECTS/agora/data/test-temp")
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{name}-{uuid4()}"
    target.mkdir(parents=True, exist_ok=True)
    return target


@dataclass(slots=True)
class AttachableScriptedDriver(Driver):
    id: str
    kind: str
    model: str
    display_name: str = "Scripted"
    effort: str | None = None
    token_ceiling: int = 100_000
    sessions: dict[str, str] = field(default_factory=dict)
    attached_replies: dict[str, list[object]] = field(default_factory=dict)
    sent_messages: list[tuple[str, str, str]] = field(default_factory=list)
    session_cwds: dict[str, str | None] = field(default_factory=dict)
    start_calls: int = 0

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        self.start_calls += 1
        raise DriverError("start_session should not be used in live handover attach flow")

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        session_ref = self.sessions.get(room_id)
        if not session_ref:
            raise DriverError(f"missing attached session for {room_id}")
        self.sent_messages.append((room_id, session_ref, user_message))
        queue = self.attached_replies.get(session_ref)
        if not queue:
            raise DriverError(f"no scripted replies for session {session_ref}")
        item = queue.pop(0)
        content = str(item(user_message) if callable(item) else item)
        return DriverReply(content=content, raw_output=content, resume_id=session_ref)

    async def close_session(self, room_id: str) -> None:
        self.sessions.pop(room_id, None)

    async def has_session(self, room_id: str) -> bool:
        return room_id in self.sessions

    def set_session_cwd(self, room_id: str, cwd: str | Path | None) -> None:
        self.session_cwds[room_id] = str(cwd) if cwd is not None else None


def _service(temp_dir: Path, drivers: dict[str, Driver]) -> LiveHandoverService:
    store = LiveHandoverStore(temp_dir / "live-handover")
    return LiveHandoverService(store=store, drivers=drivers)


def _grounded_body(answer: str) -> dict[str, object]:
    return {
        "answer": answer,
        "provenance": {
            "qmd_techlib": {"checked": False, "reason": "scripted test reply"},
            "local_filesystem": {"checked": False, "reason": "scripted test reply"},
            "git": {"checked": False, "reason": "scripted test reply"},
            "agora_ledger": {"checked": True, "ids": ["scripted-test"], "result": "message prompt inspected"},
            "unverified_claims": [],
            "confidence": "high",
        },
    }


@pytest.mark.asyncio
async def test_claude_title_attach_uses_projects_cwd() -> None:
    temp_dir = _workspace_tmp("live-handover-claude-title")
    driver = AttachableScriptedDriver(
        id="claude-1",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        attached_replies={"LISTORA": [ATTACH_ACK]},
    )
    service = _service(temp_dir, {"claude-1": driver})

    result = await service.attach_live_link(
        label="Claude LISTORA",
        driver_id="claude-1",
        external_session_ref="LISTORA",
    )

    assert result.canonical_external_session_ref == "LISTORA"
    assert driver.session_cwds[result.binding_room_id] == "C:/Users/chris/PROJECTS"
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_claude_attach_retries_after_compaction_recap() -> None:
    temp_dir = _workspace_tmp("live-handover-claude-recap-retry")
    driver = AttachableScriptedDriver(
        id="claude-1",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        attached_replies={"BODYSCAN": ["Compacted session recap, not the requested ack.", ATTACH_ACK]},
    )
    service = _service(temp_dir, {"claude-1": driver})

    result = await service.attach_live_link(
        label="Claude BODYSCAN",
        driver_id="claude-1",
        external_session_ref="BODYSCAN",
    )

    assert result.canonical_external_session_ref == "BODYSCAN"
    assert len(driver.sent_messages) == 2
    assert "Control-plane handshake retry" in driver.sent_messages[1][2]
    assert (temp_dir / "live-handover" / "links" / result.live_link_id / "attach.reply-1.txt").exists()
    assert (temp_dir / "live-handover" / "links" / result.live_link_id / "attach.reply.txt").exists()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_attach_uses_resume_path_and_rejects_duplicate_ref() -> None:
    temp_dir = _workspace_tmp("live-handover-attach")
    driver = AttachableScriptedDriver(
        id="codex-1",
        kind="codex",
        model="gpt-5.4-mini",
        attached_replies={"thread-a": [ATTACH_ACK], "thread-b": [ATTACH_ACK]},
    )
    service = _service(temp_dir, {"codex-1": driver})

    first = await service.attach_live_link(label="Interviewer", driver_id="codex-1", external_session_ref="thread-a")
    assert first.canonical_external_session_ref == "thread-a"
    assert driver.start_calls == 0
    assert driver.sent_messages[0][1] == "thread-a"

    with pytest.raises(Exception):
        await service.attach_live_link(label="Duplicate", driver_id="codex-1", external_session_ref="thread-a")

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_workflow_records_auditable_sqlite_chain() -> None:
    temp_dir = _workspace_tmp("live-handover-run")
    source_message_ids: list[str] = []

    def delivery_id_from_prompt(prompt: str) -> str:
        return prompt.split("delivery_id: ", 1)[1].splitlines()[0].strip()

    codex = AttachableScriptedDriver(
        id="codex-1",
        kind="codex",
        model="gpt-5.4-mini",
        attached_replies={
            "codex-thread": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "NEXT_QUESTION",
                        "body": {"question": "Which boundary definition should we use?"},
                        "references": [],
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
                lambda prompt: json.dumps(
                    {
                        "type": "COMPLETE",
                        "body": {
                            "artifact_markdown": "Use official statistics and define the boundary explicitly.",
                            "referenced_message_ids": list(source_message_ids),
                        },
                        "references": list(source_message_ids),
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    gemini = AttachableScriptedDriver(
        id="gemini-1",
        kind="gemini-cli",
        model="gemini-2.5-flash-lite",
        attached_replies={
            "gemini-thread": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": _grounded_body("Use municipality vs metro consistently and state the date."),
                        "references": [],
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    service = _service(temp_dir, {"codex-1": codex, "gemini-1": gemini})
    interviewer = await service.attach_live_link(label="Interviewer", driver_id="codex-1", external_session_ref="codex-thread")
    source = await service.attach_live_link(label="Source", driver_id="gemini-1", external_session_ref="gemini-thread")

    original_process = service._process_delivery

    async def wrapped_process(workflow, delivery, link):
        await original_process(workflow, delivery, link)
        messages = service.store.list_workflow_messages(str(workflow["id"]))
        source_message_ids[:] = [row["id"] for row in messages if row["live_link_id"] == source.live_link_id]

    service._process_delivery = wrapped_process  # type: ignore[method-assign]
    result = await service.run_workflow(
        goal="What is the best way to calculate the population of Athens?",
        interviewer_link_id=interviewer.live_link_id,
        source_link_id=source.live_link_id,
        max_interview_turns=1,
        max_total_wakes=4,
    )

    audit = service.get_workflow_audit(result.workflow_id)
    assert result.status == "completed"
    assert result.audit_ok
    assert audit["ok"], audit["issues"]
    assert len(audit["messages"]) == 3
    assert len(audit["invocations"]) == 3
    assert audit["artifact"] is not None
    assert Path(audit["artifact"]["mirror_path"]).exists()
    assert any("Grounding/provenance contract" in prompt for _, _, prompt in gemini.sent_messages)
    assert any('"provenance"' in prompt for _, _, prompt in gemini.sent_messages)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_ask_agent_reuses_interviewer_and_returns_artifact() -> None:
    temp_dir = _workspace_tmp("live-handover-ask-agent")

    def delivery_id_from_prompt(prompt: str) -> str:
        return prompt.split("delivery_id: ", 1)[1].splitlines()[0].strip()

    source_message_ids: list[str] = []
    codex = AttachableScriptedDriver(
        id="codex-1",
        kind="codex",
        model="gpt-5.4",
        attached_replies={
            "codex-thread": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "NEXT_QUESTION",
                        "body": {"question": "What was the last topic?"},
                        "references": [],
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
                lambda prompt: json.dumps(
                    {
                        "type": "COMPLETE",
                        "body": {
                            "artifact_markdown": "The source was working on demo readiness.",
                            "referenced_message_ids": list(source_message_ids),
                        },
                        "references": list(source_message_ids),
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    claude = AttachableScriptedDriver(
        id="claude-1",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        attached_replies={
            "LISTORA": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": _grounded_body("Demo readiness."),
                        "references": [],
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    service = _service(temp_dir, {"codex-1": codex, "claude-1": claude})
    interviewer = await service.attach_live_link(
        label="Codex interviewer",
        driver_id="codex-1",
        external_session_ref="codex-thread",
    )
    original_process = service._process_delivery

    async def wrapped_process(workflow, delivery, link):
        await original_process(workflow, delivery, link)
        messages = service.store.list_workflow_messages(str(workflow["id"]))
        source_message_ids[:] = [row["id"] for row in messages if row["live_link_id"] != interviewer.live_link_id]

    service._process_delivery = wrapped_process  # type: ignore[method-assign]
    result = await service.ask_agent(
        question="What was the last topic you were working on?",
        source_driver_id="claude-1",
        source_session_ref="LISTORA",
    )

    assert result.status == "completed"
    assert result.audit_ok
    assert result.interviewer_link_id == interviewer.live_link_id
    assert result.final_artifact_markdown == "The source was working on demo readiness."
    assert result.final_artifact_path is not None
    assert Path(result.final_artifact_path).exists()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_ask_agent_rebinds_existing_source_after_restart() -> None:
    temp_dir = _workspace_tmp("live-handover-ask-agent-rebind")

    def delivery_id_from_prompt(prompt: str) -> str:
        return prompt.split("delivery_id: ", 1)[1].splitlines()[0].strip()

    source_message_ids: list[str] = []
    codex = AttachableScriptedDriver(
        id="codex-1",
        kind="codex",
        model="gpt-5.4",
        attached_replies={
            "codex-thread": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "NEXT_QUESTION",
                        "body": {"question": "Ask source."},
                        "references": [],
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
                lambda prompt: json.dumps(
                    {
                        "type": "COMPLETE",
                        "body": {
                            "artifact_markdown": "Rebind worked.",
                            "referenced_message_ids": list(source_message_ids),
                        },
                        "references": list(source_message_ids),
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    claude = AttachableScriptedDriver(
        id="claude-1",
        kind="claude-code-new",
        model="MiniMax-M2.7-highspeed",
        attached_replies={
            "LISTORA": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": _grounded_body("Still here."),
                        "references": [],
                        "in_reply_to_delivery_id": delivery_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    service = _service(temp_dir, {"codex-1": codex, "claude-1": claude})
    interviewer = await service.attach_live_link(
        label="Codex interviewer",
        driver_id="codex-1",
        external_session_ref="codex-thread",
    )
    await service.attach_live_link(
        label="Claude LISTORA",
        driver_id="claude-1",
        external_session_ref="LISTORA",
    )
    claude.sessions.clear()
    claude.session_cwds.clear()

    original_process = service._process_delivery

    async def wrapped_process(workflow, delivery, link):
        await original_process(workflow, delivery, link)
        messages = service.store.list_workflow_messages(str(workflow["id"]))
        source_message_ids[:] = [row["id"] for row in messages if row["live_link_id"] != interviewer.live_link_id]

    service._process_delivery = wrapped_process  # type: ignore[method-assign]
    result = await service.ask_agent(
        question="Are you reachable?",
        source_driver_id="claude-1",
        source_session_ref="LISTORA",
    )

    assert result.status == "completed"
    assert result.final_artifact_markdown == "Rebind worked."
    assert any(value == "C:/Users/chris/PROJECTS" for value in claude.session_cwds.values())
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_claim_delivery_allows_only_one_active_runner() -> None:
    temp_dir = _workspace_tmp("live-handover-claim")
    store = LiveHandoverStore(temp_dir / "live-handover")
    link_id = "link-1"
    store.record_live_link(
        {
            "id": link_id,
            "label": "L",
            "driver_id": "codex-1",
            "driver_kind": "codex",
            "model_identity": "gpt-5.4-mini",
            "external_session_ref": "thread-1",
            "canonical_external_session_ref": "thread-1",
            "binding_room_id": "binding-1",
            "verification_status": "verified",
            "verification_prompt_path": "p",
            "verification_prompt_sha256": "h",
            "ack_reply_path": "r",
            "ack_reply_sha256": "h2",
            "created_at": "2026-04-28T00:00:00Z",
            "verified_at": "2026-04-28T00:00:00Z",
        }
    )
    store.create_workflow(
        {
            "id": "wf-1",
            "goal": "goal",
            "interviewer_link_id": link_id,
            "source_link_id": link_id,
            "status": "interviewer_pending",
            "config": {"max_interview_turns": 1, "max_total_wakes": 2, "max_invalid_outputs_per_agent": 1, "max_runtime_minutes": 1},
            "created_at": "2026-04-28T00:00:00Z",
            "updated_at": "2026-04-28T00:00:00Z",
        }
    )
    store.create_delivery(
        delivery_id="delivery-1",
        workflow_id="wf-1",
        target_link_id=link_id,
        kind="interviewer_open",
        payload={"goal": "goal"},
        workflow_state_from="created",
        workflow_state_to="interviewer_pending",
        event_id="evt-1",
        reason="init",
    )

    results: list[dict[str, object] | None] = [None, None]

    def claimer(index: int) -> None:
        results[index] = store.claim_delivery(delivery_id="delivery-1", live_link_id=link_id, run_id=f"run-{index}")

    t1 = threading.Thread(target=claimer, args=(0,))
    t2 = threading.Thread(target=claimer, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sum(1 for result in results if result is not None) == 1
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_agent_inbox_records_read_answer_and_ack_receipts() -> None:
    temp_dir = _workspace_tmp("agent-inbox-receipts")

    def agent_message_id_from_prompt(prompt: str) -> str:
        return prompt.split("agent_message_id: ", 1)[1].splitlines()[0].strip()

    codex = AttachableScriptedDriver(
        id="codex-1",
        kind="codex",
        model="gpt-5.4",
        attached_replies={
            "codex-listora-thread": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": _grounded_body("I received the durable inbox message and have no questions."),
                        "references": [],
                        "in_reply_to_agent_message_id": agent_message_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    service = _service(temp_dir, {"codex-1": codex})
    link = await service.attach_live_link(
        label="Codex LISTORA source",
        driver_id="codex-1",
        external_session_ref="codex-listora-thread",
    )

    sent = service.send_agent_message(
        to_link_id=link.live_link_id,
        subject="Playwright headful guidance",
        body="Use one persistent headful Playwright session. Do you have questions?",
    )

    assert sent.status == "delivered"
    processed = await service.process_agent_inbox_once(to_link_id=link.live_link_id)
    assert processed is not None
    assert processed.message_id == sent.message_id
    assert processed.status == "answered"
    assert processed.response_body == "I received the durable inbox message and have no questions."
    assert "Grounding/provenance contract" in codex.sent_messages[-1][2]
    assert '"provenance"' in codex.sent_messages[-1][2]

    row = service.store.get_agent_message(sent.message_id)
    assert row is not None
    assert row["read_at"] is not None
    assert row["answered_at"] is not None
    assert row["acknowledged_at"] is None

    acked = service.acknowledge_agent_message(sent.message_id)
    assert acked.status == "acknowledged"
    row = service.store.get_agent_message(sent.message_id)
    assert row is not None
    assert row["acknowledged_at"] is not None
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_agent_inbox_rejects_answers_without_provenance() -> None:
    temp_dir = _workspace_tmp("agent-inbox-missing-provenance")

    def agent_message_id_from_prompt(prompt: str) -> str:
        return prompt.split("agent_message_id: ", 1)[1].splitlines()[0].strip()

    codex = AttachableScriptedDriver(
        id="codex-1",
        kind="codex",
        model="gpt-5.4",
        attached_replies={
            "codex-thread": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": {"answer": "This answer is missing provenance."},
                        "references": [],
                        "in_reply_to_agent_message_id": agent_message_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    service = _service(temp_dir, {"codex-1": codex})
    link = await service.attach_live_link(label="Codex source", driver_id="codex-1", external_session_ref="codex-thread")
    sent = service.send_agent_message(to_link_id=link.live_link_id, body="Which repo is canonical?")

    processed = await service.process_agent_inbox_once(to_link_id=link.live_link_id)

    assert processed is not None
    assert processed.message_id == sent.message_id
    assert processed.status == "failed"
    assert processed.error_text is not None
    assert "body.provenance is required" in processed.error_text
    row = service.store.get_agent_message(sent.message_id)
    assert row is not None
    assert "body.provenance is required" in str(row["error_text"])
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_agent_inbox_supports_multi_turn_agent_dialogue() -> None:
    temp_dir = _workspace_tmp("agent-inbox-dialogue")

    def agent_message_id_from_prompt(prompt: str) -> str:
        return prompt.split("agent_message_id: ", 1)[1].splitlines()[0].strip()

    agent_a = AttachableScriptedDriver(
        id="codex-a",
        kind="codex",
        model="gpt-5.4",
        attached_replies={
            "thread-a": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": _grounded_body("A received the opening and asks B to confirm the browser profile."),
                        "references": [],
                        "in_reply_to_agent_message_id": agent_message_id_from_prompt(prompt),
                    }
                ),
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": _grounded_body("A accepts B's confirmation and closes the dialogue."),
                        "references": [],
                        "in_reply_to_agent_message_id": agent_message_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    agent_b = AttachableScriptedDriver(
        id="codex-b",
        kind="codex",
        model="gpt-5.4",
        attached_replies={
            "thread-b": [
                ATTACH_ACK,
                lambda prompt: json.dumps(
                    {
                        "type": "ANSWER",
                        "body": _grounded_body("B confirms the persistent profile and asks A to close if sufficient."),
                        "references": [],
                        "in_reply_to_agent_message_id": agent_message_id_from_prompt(prompt),
                    }
                ),
            ]
        },
    )
    service = _service(temp_dir, {"codex-a": agent_a, "codex-b": agent_b})
    link_a = await service.attach_live_link(label="Agent A", driver_id="codex-a", external_session_ref="thread-a")
    link_b = await service.attach_live_link(label="Agent B", driver_id="codex-b", external_session_ref="thread-b")

    msg1 = service.send_agent_message(to_link_id=link_a.live_link_id, body="Opening message for A.")
    ans1 = await service.process_agent_inbox_once(to_link_id=link_a.live_link_id)
    assert ans1 is not None
    service.acknowledge_agent_message(msg1.message_id)

    msg2 = service.send_agent_message(
        from_link_id=link_a.live_link_id,
        to_link_id=link_b.live_link_id,
        body=f"A said: {ans1.response_body}",
    )
    ans2 = await service.process_agent_inbox_once(to_link_id=link_b.live_link_id)
    assert ans2 is not None
    service.acknowledge_agent_message(msg2.message_id)

    msg3 = service.send_agent_message(
        from_link_id=link_b.live_link_id,
        to_link_id=link_a.live_link_id,
        body=f"B said: {ans2.response_body}",
    )
    ans3 = await service.process_agent_inbox_once(to_link_id=link_a.live_link_id)
    assert ans3 is not None
    service.acknowledge_agent_message(msg3.message_id)

    messages = service.list_agent_messages(include_terminal=True, limit=10)
    dialogue_messages = [message for message in messages if str(message["id"]) in {msg1.message_id, msg2.message_id, msg3.message_id}]
    assert [message["status"] for message in dialogue_messages] == ["acknowledged", "acknowledged", "acknowledged"]
    assert ans1.response_body == "A received the opening and asks B to confirm the browser profile."
    assert ans2.response_body == "B confirms the persistent profile and asks A to close if sufficient."
    assert ans3.response_body == "A accepts B's confirmation and closes the dialogue."
    shutil.rmtree(temp_dir, ignore_errors=True)
