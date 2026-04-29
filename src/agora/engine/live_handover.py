from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agora.drivers.base import Driver, DriverError
from agora.persistence.live_handover_store import LiveHandoverStore, TERMINAL_WORKFLOW_STATES, utc_now_iso


SUPPORTED_DRIVER_KINDS = {"codex", "gemini-cli", "claude-code-new"}
ATTACH_ACK = "AGORA_LINK_ACK"

GROUNDING_CONTRACT = """Grounding/provenance contract:
- Before answering any factual or project-state ask, check durable memory and local evidence from this machine. This includes asks about status, blockers, repos, files, commits, paths, prior work, deployments, handovers, or what another agent did.
- Start with QMD/TechLib when tools are available: run or inspect equivalent results for `node C:\\Users\\chris\\PROJECTS\\qmd-wrap.mjs search "<project/topic>" -c techlib`. If tools are unavailable, say so in provenance.
- If your answer names local paths, verify them with filesystem checks. If it names repos, remotes, branches, or commits, verify them with git. If it references Agora handover or agent messages, inspect the workflow/message ids.
- Do not present memory/session claims as verified facts. Separate verified facts from unverified or conflicting claims in the answer.
- Include `body.provenance` in every ANSWER, CANNOT_ANSWER, or NEEDS_CLARIFICATION response with qmd_techlib, local_filesystem, git, agora_ledger, unverified_claims, and confidence fields."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("reply did not contain a JSON object")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("reply JSON was not an object")
    return payload


@dataclass(slots=True)
class LiveLinkAttachResult:
    live_link_id: str
    driver_id: str
    driver_kind: str
    external_session_ref: str
    canonical_external_session_ref: str
    binding_room_id: str
    model_identity: str | None


@dataclass(slots=True)
class LiveHandoverWorkflowResult:
    workflow_id: str
    status: str
    stop_reason: str | None
    final_artifact_id: str | None
    audit_ok: bool
    run_dir: str


@dataclass(slots=True)
class AgentAskResult:
    workflow_id: str
    status: str
    audit_ok: bool
    interviewer_link_id: str
    source_link_id: str
    final_artifact_path: str | None
    final_artifact_markdown: str | None
    run_dir: str


@dataclass(slots=True)
class AgentMessageResult:
    message_id: str
    from_link_id: str | None
    to_link_id: str
    status: str
    body: str
    response_body: str | None
    response: dict[str, Any] | None
    error_text: str | None
    requires_ack: bool


class LiveHandoverService:
    def __init__(self, *, store: LiveHandoverStore, drivers: dict[str, Driver]) -> None:
        self.store = store
        self.drivers = drivers
        self._lock = threading.Lock()

    def list_live_links(self) -> list[dict[str, Any]]:
        return self.store.list_live_links()

    def get_live_link(self, link_id: str) -> dict[str, Any] | None:
        return self.store.get_live_link(link_id)

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            return None
        workflow["messages"] = self.store.list_workflow_messages(workflow_id)
        workflow["artifact"] = self.store.get_workflow_artifact(workflow_id)
        return workflow

    def get_workflow_audit(self, workflow_id: str) -> dict[str, Any]:
        return self.store.workflow_audit(workflow_id)

    def list_agent_messages(
        self,
        *,
        to_link_id: str | None = None,
        status: str | None = None,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.store.list_agent_messages(
            to_link_id=to_link_id,
            status=status,
            include_terminal=include_terminal,
            limit=limit,
        )

    def send_agent_message(
        self,
        *,
        to_link_id: str,
        body: str,
        from_link_id: str | None = None,
        subject: str | None = None,
        payload: dict[str, Any] | None = None,
        requires_ack: bool = True,
    ) -> AgentMessageResult:
        if not body.strip():
            raise ValueError("message body is required")
        self._require_live_link(to_link_id)
        if from_link_id is not None:
            self._require_live_link(from_link_id)
        row = self.store.create_agent_message(
            message_id=f"agent-msg-{uuid.uuid4().hex[:12]}",
            from_link_id=from_link_id,
            to_link_id=to_link_id,
            subject=subject,
            body=body.strip(),
            payload=payload or {},
            requires_ack=requires_ack,
        )
        return self._agent_message_result(row)

    def mark_agent_message_read(self, message_id: str) -> AgentMessageResult:
        row = self.store.mark_agent_message_read(message_id)
        if row is None:
            raise ValueError(f"agent message not found or not readable: {message_id}")
        return self._agent_message_result(row)

    def acknowledge_agent_message(self, message_id: str) -> AgentMessageResult:
        row = self.store.acknowledge_agent_message(message_id)
        if row is None:
            raise ValueError(f"agent message not found or not acknowledgeable: {message_id}")
        return self._agent_message_result(row)

    async def process_agent_inbox_once(self, *, to_link_id: str) -> AgentMessageResult | None:
        link = self._require_live_link(to_link_id)
        pending = self.store.list_agent_messages(
            to_link_id=to_link_id,
            include_terminal=False,
            limit=1,
        )
        if not pending:
            return None
        message = self.store.mark_agent_message_read(str(pending[0]["id"]))
        if message is None:
            return None
        driver = self._require_driver(str(link["driver_id"]))
        self._ensure_driver_bound(link)
        prompt = self._compose_agent_message_prompt(message, link)
        try:
            reply = await driver.send_in_session(str(link["binding_room_id"]), prompt)
            parsed = extract_json_object(reply.content)
            if parsed.get("in_reply_to_agent_message_id") != message["id"]:
                raise ValueError("in_reply_to_agent_message_id mismatch")
            message_type = parsed.get("type")
            if message_type not in {"ANSWER", "CANNOT_ANSWER", "NEEDS_CLARIFICATION"}:
                raise ValueError(f"invalid agent inbox message type: {message_type}")
            body = parsed.get("body")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            self._validate_grounded_response(message_type, body)
            response_text = self._agent_response_text(parsed)
            row = self.store.answer_agent_message(
                message_id=str(message["id"]),
                response_body=response_text,
                response=parsed,
            )
            if row is None:
                raise ValueError(f"agent message no longer answerable: {message['id']}")
            return self._agent_message_result(row)
        except Exception as exc:
            row = self.store.fail_agent_message(
                message_id=str(message["id"]),
                error_text=f"{type(exc).__name__}: {exc}",
            )
            if row is None:
                raise
            return self._agent_message_result(row)

    async def attach_live_link(
        self,
        *,
        label: str,
        driver_id: str,
        external_session_ref: str,
    ) -> LiveLinkAttachResult:
        driver = self._require_driver(driver_id)
        canonical_ref = self._canonicalize_external_ref(external_session_ref)
        link_id = f"link-{uuid.uuid4().hex[:12]}"
        binding_room_id = f"live-link-{link_id}"
        link_dir = self.store.links_dir / link_id
        link_dir.mkdir(parents=True, exist_ok=True)
        prompt = (
            "You are being attached to Agora over an existing resumed session.\n"
            "Reply with exactly this text and nothing else:\n"
            f"{ATTACH_ACK}\n"
        )
        prompt_path = link_dir / "attach.prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
        self._bind_external_session(driver, binding_room_id, canonical_ref)
        try:
            reply = await self._send_attach_handshake(
                driver=driver,
                binding_room_id=binding_room_id,
                prompt=prompt,
                link_dir=link_dir,
            )
        except Exception:
            self._unbind_external_session(driver, binding_room_id)
            raise
        reply_text = reply.content.strip()
        reply_path = link_dir / "attach.reply.txt"
        reply_path.write_text(reply.content, encoding="utf-8", newline="\n")
        if reply_text != ATTACH_ACK:
            self._unbind_external_session(driver, binding_room_id)
            raise DriverError(f"attach handshake failed: expected {ATTACH_ACK!r}, got {reply_text!r}")
        payload = {
            "id": link_id,
            "label": label,
            "driver_id": driver.id,
            "driver_kind": driver.kind,
            "model_identity": getattr(driver, "model", None),
            "external_session_ref": external_session_ref.strip(),
            "canonical_external_session_ref": canonical_ref,
            "binding_room_id": binding_room_id,
            "verification_status": "verified",
            "verification_prompt_path": str(prompt_path),
            "verification_prompt_sha256": sha256_text(prompt),
            "ack_reply_path": str(reply_path),
            "ack_reply_sha256": sha256_text(reply.content),
            "metadata": {"reply_resume_id": reply.resume_id},
            "created_at": utc_now_iso(),
            "verified_at": utc_now_iso(),
        }
        try:
            self.store.record_live_link(payload)
        except sqlite3.IntegrityError as exc:
            self._unbind_external_session(driver, binding_room_id)
            raise ValueError(
                f"duplicate active external session ref for {driver.kind}: {canonical_ref}"
            ) from exc
        return LiveLinkAttachResult(
            live_link_id=link_id,
            driver_id=driver.id,
            driver_kind=driver.kind,
            external_session_ref=external_session_ref.strip(),
            canonical_external_session_ref=canonical_ref,
            binding_room_id=binding_room_id,
            model_identity=getattr(driver, "model", None),
        )

    async def _send_attach_handshake(
        self,
        *,
        driver: Driver,
        binding_room_id: str,
        prompt: str,
        link_dir: Path,
    ) -> DriverReply:
        """Send attach prompt, retrying Claude sessions that emit compaction recap first."""

        max_attempts = 2 if getattr(driver, "kind", None) == "claude-code-new" else 1
        last_reply: DriverReply | None = None
        for attempt in range(1, max_attempts + 1):
            attempt_prompt = prompt
            if attempt > 1:
                attempt_prompt = (
                    "Control-plane handshake retry. Ignore any prior project recap or status update.\n"
                    "Reply with exactly this text and nothing else:\n"
                    f"{ATTACH_ACK}\n"
                )
                (link_dir / f"attach.retry-{attempt}.prompt.txt").write_text(
                    attempt_prompt,
                    encoding="utf-8",
                    newline="\n",
                )
            reply = await driver.send_in_session(binding_room_id, attempt_prompt)
            reply_text = reply.content.strip()
            reply_path = link_dir / ("attach.reply.txt" if attempt == max_attempts or reply_text == ATTACH_ACK else f"attach.reply-{attempt}.txt")
            reply_path.write_text(reply.content, encoding="utf-8", newline="\n")
            last_reply = reply
            if reply_text == ATTACH_ACK:
                return reply
        assert last_reply is not None
        return last_reply

    async def ensure_live_link(
        self,
        *,
        label: str,
        driver_id: str,
        external_session_ref: str,
    ) -> LiveLinkAttachResult:
        driver = self._require_driver(driver_id)
        canonical_ref = self._canonicalize_external_ref(external_session_ref)
        existing = self.store.get_active_live_link_by_identity(driver.kind, canonical_ref)
        if existing is not None:
            self._bind_external_session(
                driver,
                str(existing["binding_room_id"]),
                str(existing["canonical_external_session_ref"]),
            )
            return LiveLinkAttachResult(
                live_link_id=str(existing["id"]),
                driver_id=str(existing["driver_id"]),
                driver_kind=str(existing["driver_kind"]),
                external_session_ref=str(existing["external_session_ref"]),
                canonical_external_session_ref=str(existing["canonical_external_session_ref"]),
                binding_room_id=str(existing["binding_room_id"]),
                model_identity=existing.get("model_identity"),
            )
        return await self.attach_live_link(
            label=label,
            driver_id=driver_id,
            external_session_ref=external_session_ref,
        )

    def _compose_agent_message_prompt(self, message: dict[str, Any], link: dict[str, Any]) -> str:
        from_link = self.store.get_live_link(str(message["from_link_id"])) if message.get("from_link_id") else None
        sender = (
            f"{from_link['label']} ({from_link['driver_id']})"
            if from_link
            else "external/user"
        )
        return (
            "You are an Agora-managed agent inbox worker.\n"
            "Answer the delivered inter-agent message and produce exactly one JSON object.\n"
            "Allowed types: ANSWER, CANNOT_ANSWER, NEEDS_CLARIFICATION.\n"
            "Do not emit commentary outside JSON.\n\n"
            f"{GROUNDING_CONTRACT}\n\n"
            f"agent_message_id: {message['id']}\n"
            f"recipient_link_id: {link['id']}\n"
            f"recipient_label: {link['label']}\n"
            f"sender: {sender}\n"
            f"subject: {message.get('subject') or ''}\n"
            f"payload: {json.dumps(message.get('payload_json') or {}, ensure_ascii=False)}\n\n"
            f"Message:\n{message['body']}\n\n"
            "Required JSON shape:\n"
            "{\n"
            '  "type": "ANSWER",\n'
            '  "body": {\n'
            '    "answer": "...",\n'
            '    "provenance": {\n'
            '      "qmd_techlib": { "checked": true, "query": "...", "result": "..." },\n'
            '      "local_filesystem": { "checked": true, "paths": ["..."], "result": "..." },\n'
            '      "git": { "checked": true, "repos_or_commits": ["..."], "result": "..." },\n'
            '      "agora_ledger": { "checked": true, "ids": ["..."], "result": "..." },\n'
            '      "unverified_claims": ["..."],\n'
            '      "confidence": "high"\n'
            '    }\n'
            '  },\n'
            '  "references": [],\n'
            '  "in_reply_to_agent_message_id": "' + str(message["id"]) + '"\n'
            "}\n"
        )

    def _agent_response_text(self, parsed: dict[str, Any]) -> str:
        body = parsed.get("body")
        if isinstance(body, dict):
            for key in ("answer", "message", "reason"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return json.dumps(body, ensure_ascii=False)
        return json.dumps(parsed, ensure_ascii=False)

    def _validate_grounded_response(self, message_type: str, body: dict[str, Any]) -> None:
        if message_type not in {"ANSWER", "CANNOT_ANSWER", "NEEDS_CLARIFICATION"}:
            return
        provenance = body.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError("body.provenance is required")
        required_objects = ("qmd_techlib", "local_filesystem", "git", "agora_ledger")
        for key in required_objects:
            value = provenance.get(key)
            if not isinstance(value, dict):
                raise ValueError(f"body.provenance.{key} must be an object")
            if not isinstance(value.get("checked"), bool):
                raise ValueError(f"body.provenance.{key}.checked must be boolean")
        unverified_claims = provenance.get("unverified_claims")
        if not isinstance(unverified_claims, list) or not all(isinstance(item, str) for item in unverified_claims):
            raise ValueError("body.provenance.unverified_claims must be a list of strings")
        confidence = provenance.get("confidence")
        if confidence not in {"high", "medium", "low"}:
            raise ValueError("body.provenance.confidence must be high, medium, or low")

    def _agent_message_result(self, row: dict[str, Any]) -> AgentMessageResult:
        return AgentMessageResult(
            message_id=str(row["id"]),
            from_link_id=str(row["from_link_id"]) if row.get("from_link_id") else None,
            to_link_id=str(row["to_link_id"]),
            status=str(row["status"]),
            body=str(row["body"]),
            response_body=str(row["response_body"]) if row.get("response_body") else None,
            response=row.get("response_json") if isinstance(row.get("response_json"), dict) else None,
            error_text=str(row["error_text"]) if row.get("error_text") else None,
            requires_ack=bool(row.get("requires_ack")),
        )

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
    ) -> AgentAskResult:
        if not question.strip():
            raise ValueError("question is required")
        source = await self.ensure_live_link(
            label=source_label or f"{source_driver_id} source",
            driver_id=source_driver_id,
            external_session_ref=source_session_ref,
        )
        if interviewer_session_ref:
            interviewer = await self.ensure_live_link(
                label=interviewer_label or f"{interviewer_driver_id} interviewer",
                driver_id=interviewer_driver_id,
                external_session_ref=interviewer_session_ref,
            )
        else:
            existing_interviewer = self.store.first_active_live_link_for_driver(interviewer_driver_id)
            if existing_interviewer is None:
                raise ValueError(
                    "interviewer_session_ref is required when no active interviewer link exists "
                    f"for {interviewer_driver_id}"
                )
            interviewer = LiveLinkAttachResult(
                live_link_id=str(existing_interviewer["id"]),
                driver_id=str(existing_interviewer["driver_id"]),
                driver_kind=str(existing_interviewer["driver_kind"]),
                external_session_ref=str(existing_interviewer["external_session_ref"]),
                canonical_external_session_ref=str(existing_interviewer["canonical_external_session_ref"]),
                binding_room_id=str(existing_interviewer["binding_room_id"]),
                model_identity=existing_interviewer.get("model_identity"),
            )
        result = await self.run_workflow(
            goal=(
                "Ask the source conversation this question and produce a concise final answer.\n\n"
                f"Question:\n{question.strip()}"
            ),
            interviewer_link_id=interviewer.live_link_id,
            source_link_id=source.live_link_id,
            max_interview_turns=max_interview_turns,
            max_total_wakes=max_total_wakes,
            max_invalid_outputs_per_agent=max_invalid_outputs_per_agent,
            max_runtime_minutes=max_runtime_minutes,
        )
        artifact = self.store.get_workflow_artifact(result.workflow_id)
        return AgentAskResult(
            workflow_id=result.workflow_id,
            status=result.status,
            audit_ok=result.audit_ok,
            interviewer_link_id=interviewer.live_link_id,
            source_link_id=source.live_link_id,
            final_artifact_path=artifact.get("mirror_path") if artifact else None,
            final_artifact_markdown=artifact.get("artifact_markdown") if artifact else None,
            run_dir=result.run_dir,
        )

    async def run_workflow(
        self,
        *,
        goal: str,
        interviewer_link_id: str,
        source_link_id: str,
        max_interview_turns: int = 3,
        max_total_wakes: int = 8,
        max_invalid_outputs_per_agent: int = 2,
        max_runtime_minutes: int = 10,
    ) -> LiveHandoverWorkflowResult:
        if interviewer_link_id == source_link_id:
            raise ValueError("interviewer and source must be different live links")
        interviewer = self._require_live_link(interviewer_link_id)
        source = self._require_live_link(source_link_id)
        workflow_id = f"workflow-{uuid.uuid4().hex[:12]}"
        config = {
            "max_interview_turns": max_interview_turns,
            "max_total_wakes": max_total_wakes,
            "max_invalid_outputs_per_agent": max_invalid_outputs_per_agent,
            "max_runtime_minutes": max_runtime_minutes,
        }
        self.store.create_workflow(
            {
                "id": workflow_id,
                "goal": goal,
                "interviewer_link_id": interviewer_link_id,
                "source_link_id": source_link_id,
                "status": "interviewer_pending",
                "config": config,
                "metrics": {"started_at": utc_now_iso()},
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            }
        )
        self.store.create_delivery(
            delivery_id=f"delivery-{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            target_link_id=interviewer_link_id,
            kind="interviewer_open",
            payload={"goal": goal},
            workflow_state_from="created",
            workflow_state_to="interviewer_pending",
            event_id=f"evt-{uuid.uuid4().hex[:12]}",
            reason="initial interviewer wake",
        )
        self._refresh_workflow_mirrors(workflow_id)

        while True:
            workflow = self.store.get_workflow(workflow_id)
            assert workflow is not None
            if workflow["status"] in TERMINAL_WORKFLOW_STATES:
                break
            self._enforce_limits_before_wake(workflow_id)
            workflow = self.store.get_workflow(workflow_id)
            assert workflow is not None
            if workflow["status"] in TERMINAL_WORKFLOW_STATES:
                break
            delivery = self._next_pending_delivery(workflow_id)
            if delivery is None:
                raise RuntimeError(f"workflow {workflow_id} has no pending delivery")
            link = self._require_live_link(str(delivery["target_link_id"]))
            await self._process_delivery(workflow, delivery, link)
            self._refresh_workflow_mirrors(workflow_id)

        audit = self.store.workflow_audit(workflow_id)
        artifact = self.store.get_workflow_artifact(workflow_id)
        final_workflow = self.store.get_workflow(workflow_id)
        assert final_workflow is not None
        return LiveHandoverWorkflowResult(
            workflow_id=workflow_id,
            status=str(final_workflow["status"]),
            stop_reason=final_workflow.get("stop_reason"),
            final_artifact_id=artifact["id"] if artifact else None,
            audit_ok=bool(audit["ok"]),
            run_dir=str(self.store.workflows_dir / workflow_id),
        )

    async def _process_delivery(self, workflow: dict[str, Any], delivery: dict[str, Any], link: dict[str, Any]) -> None:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        claimed = self.store.claim_delivery(delivery_id=str(delivery["id"]), live_link_id=str(link["id"]), run_id=run_id)
        if claimed is None:
            raise RuntimeError(f"unable to claim delivery {delivery['id']}")
        driver = self._require_driver(str(link["driver_id"]))
        self._ensure_driver_bound(link)
        prompt = self._compose_prompt(workflow, delivery, link)
        workflow_dir = self.store.workflows_dir / str(workflow["id"])
        prompts_dir = workflow_dir / "prompts"
        replies_dir = workflow_dir / "replies"
        raw_dir = workflow_dir / "raw"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        replies_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompts_dir / f"{delivery['id']}.prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
        try:
            reply = await driver.send_in_session(str(link["binding_room_id"]), prompt)
        except Exception as exc:
            self.store.fail_run(
                workflow_id=str(workflow["id"]),
                delivery_id=str(delivery["id"]),
                run_id=run_id,
                workflow_state_from=str(workflow["status"]),
                workflow_state_to="failed",
                reason=f"driver_error:{type(exc).__name__}:{exc}",
                terminal=True,
            )
            raise
        reply_path = replies_dir / f"{delivery['id']}.reply.txt"
        reply_path.write_text(reply.content, encoding="utf-8", newline="\n")
        raw_output = reply.raw_output or reply.content
        raw_path = raw_dir / f"{delivery['id']}.raw.txt"
        raw_path.write_text(raw_output, encoding="utf-8", newline="\n")

        try:
            parsed = extract_json_object(reply.content)
            message = self._validate_message(
                workflow=workflow,
                delivery=delivery,
                link=link,
                parsed=parsed,
            )
        except Exception as exc:
            terminal = self._invalid_output_is_terminal(str(workflow["id"]), str(link["id"]), workflow)
            self.store.fail_run(
                workflow_id=str(workflow["id"]),
                delivery_id=str(delivery["id"]),
                run_id=run_id,
                workflow_state_from=str(workflow["status"]),
                workflow_state_to="failed" if terminal else str(workflow["status"]),
                reason=f"invalid_output:{exc}",
                terminal=terminal,
            )
            if terminal:
                return
            retry_delivery_id = f"delivery-{uuid.uuid4().hex[:12]}"
            self.store.create_delivery(
                delivery_id=retry_delivery_id,
                workflow_id=str(workflow["id"]),
                target_link_id=str(link["id"]),
                kind=str(delivery["kind"]),
                payload={**delivery["payload_json"], "repair_reason": str(exc)},
                workflow_state_from=str(workflow["status"]),
                workflow_state_to=str(workflow["status"]),
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                reason="repair after invalid output",
            )
            return

        invocation = {
            "id": f"inv-{uuid.uuid4().hex[:12]}",
            "driver_id": driver.id,
            "driver_kind": driver.kind,
            "model_identity": getattr(driver, "model", None),
            "external_session_ref": str(link["canonical_external_session_ref"]),
            "prompt_path": str(prompt_path),
            "prompt_sha256": sha256_text(prompt),
            "reply_path": str(reply_path),
            "reply_sha256": sha256_text(reply.content),
            "raw_output_path": str(raw_path),
            "raw_output_sha256": sha256_text(raw_output),
            "exit_code": 0,
            "timeout_status": 0,
            "stderr_summary": None,
        }
        await self._apply_valid_message(
            workflow=workflow,
            delivery=delivery,
            run_id=run_id,
            link=link,
            invocation=invocation,
            message=message,
        )

    def _compose_prompt(self, workflow: dict[str, Any], delivery: dict[str, Any], link: dict[str, Any]) -> str:
        payload = delivery["payload_json"]
        role = "interviewer" if link["id"] == workflow["interviewer_link_id"] else "source"
        messages = self.store.list_workflow_messages(str(workflow["id"]))
        history_lines = []
        for row in messages[-8:]:
            body = row["body_json"]
            history_lines.append(
                f"- seq={row['seq']} type={row['type']} from={row['live_link_id']} body={json.dumps(body, ensure_ascii=False)}"
            )
        history = "\n".join(history_lines) if history_lines else "- None yet."
        if role == "interviewer" and delivery["kind"] == "interviewer_open":
            task = (
                "You are the interviewer. Produce exactly one JSON object.\n"
                "Allowed types: NEXT_QUESTION, REQUEST_ARTIFACT, COMPLETE, NEEDS_USER_REVIEW.\n"
                "If type is COMPLETE, include artifact_markdown and referenced_message_ids.\n"
                "Do not choose recipients. Do not emit extra text."
            )
        elif role == "interviewer":
            task = (
                "You are the interviewer reviewing the source's latest response.\n"
                "Produce exactly one JSON object.\n"
                "Allowed types: NEXT_QUESTION, REQUEST_ARTIFACT, COMPLETE, NEEDS_USER_REVIEW.\n"
                "If type is COMPLETE, include artifact_markdown and referenced_message_ids.\n"
                "Do not emit commentary outside JSON."
            )
        elif delivery["kind"] == "source_reply":
            task = (
                "You are the source. Answer the interviewer's latest request.\n"
                "Produce exactly one JSON object.\n"
                "Allowed types: ANSWER, CANNOT_ANSWER, NEEDS_CLARIFICATION.\n"
                "Do not emit commentary outside JSON."
            )
        else:
            task = (
                "You are the source. Provide the requested evidence.\n"
                "Produce exactly one JSON object.\n"
                "Allowed types: ANSWER, CANNOT_ANSWER, NEEDS_CLARIFICATION."
            )
        grounding = f"\n\n{GROUNDING_CONTRACT}" if role == "source" else ""
        body_shape = (
            '{ "answer": "...", "provenance": { "qmd_techlib": { "checked": true }, '
            '"local_filesystem": { "checked": true }, "git": { "checked": true }, '
            '"agora_ledger": { "checked": true }, "unverified_claims": [], "confidence": "high" } }'
            if role == "source"
            else "{ ... }"
        )
        return (
            f"{task}{grounding}\n\n"
            f"workflow_id: {workflow['id']}\n"
            f"delivery_id: {delivery['id']}\n"
            f"role: {role}\n"
            f"goal: {workflow['goal']}\n"
            f"pending_payload: {json.dumps(payload, ensure_ascii=False)}\n"
            f"context_history:\n{history}\n\n"
            "Required JSON shape:\n"
            "{\n"
            '  "type": "...",\n'
            f'  "body": {body_shape},\n'
            '  "references": ["message-id-1", "..."],\n'
            '  "in_reply_to_delivery_id": "' + str(delivery["id"]) + '"\n'
            "}\n"
        )

    def _validate_message(
        self,
        *,
        workflow: dict[str, Any],
        delivery: dict[str, Any],
        link: dict[str, Any],
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        message_type = parsed.get("type")
        if not isinstance(message_type, str):
            raise ValueError("missing message type")
        body = parsed.get("body")
        if not isinstance(body, dict):
            raise ValueError("body must be an object")
        refs = parsed.get("references")
        if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
            raise ValueError("references must be a list of message ids")
        if parsed.get("in_reply_to_delivery_id") != delivery["id"]:
            raise ValueError("in_reply_to_delivery_id mismatch")
        role = "interviewer" if link["id"] == workflow["interviewer_link_id"] else "source"
        allowed = {
            "interviewer": {"NEXT_QUESTION", "REQUEST_ARTIFACT", "COMPLETE", "NEEDS_USER_REVIEW"},
            "source": {"ANSWER", "CANNOT_ANSWER", "NEEDS_CLARIFICATION"},
        }[role]
        if message_type not in allowed:
            raise ValueError(f"{role} cannot emit {message_type}")
        if role == "source":
            self._validate_grounded_response(message_type, body)
        valid_refs = {row["id"] for row in self.store.list_workflow_messages(str(workflow["id"]))}
        for ref in refs:
            if ref not in valid_refs:
                raise ValueError(f"unknown or cross-workflow reference: {ref}")
        if message_type == "COMPLETE":
            artifact_markdown = body.get("artifact_markdown")
            artifact_refs = body.get("referenced_message_ids")
            if not isinstance(artifact_markdown, str) or not artifact_markdown.strip():
                raise ValueError("COMPLETE requires artifact_markdown")
            if not isinstance(artifact_refs, list) or not artifact_refs or not all(isinstance(item, str) for item in artifact_refs):
                raise ValueError("COMPLETE requires referenced_message_ids")
            for ref in artifact_refs:
                if ref not in valid_refs:
                    raise ValueError(f"artifact reference outside workflow: {ref}")
        return {
            "id": f"msg-{uuid.uuid4().hex[:12]}",
            "type": message_type,
            "body": body,
            "references": refs,
        }

    async def _apply_valid_message(
        self,
        *,
        workflow: dict[str, Any],
        delivery: dict[str, Any],
        run_id: str,
        link: dict[str, Any],
        invocation: dict[str, Any],
        message: dict[str, Any],
    ) -> None:
        current_state = str(workflow["status"])
        next_state = current_state
        event_type = "message_recorded"
        reason = message["type"]
        artifact_payload: dict[str, Any] | None = None
        workflow_id = str(workflow["id"])
        if message["type"] in {"NEXT_QUESTION", "REQUEST_ARTIFACT"}:
            next_state = "source_pending"
        elif message["type"] in {"ANSWER", "CANNOT_ANSWER", "NEEDS_CLARIFICATION"}:
            source_turns = self.store.counts_for_workflow(workflow_id)["source_turns"] + 1
            max_interview_turns = int(workflow["config_json"]["max_interview_turns"])
            next_state = "artifact_pending" if source_turns >= max_interview_turns else "interviewer_pending"
        elif message["type"] == "COMPLETE":
            next_state = "completed"
            body = message["body"]
            workflow_dir = self.store.workflows_dir / workflow_id
            artifact_dir = workflow_dir / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / f"{message['id']}.md"
            artifact_markdown = str(body["artifact_markdown"]).strip()
            artifact_path.write_text(artifact_markdown, encoding="utf-8", newline="\n")
            artifact_payload = {
                "id": f"artifact-{uuid.uuid4().hex[:12]}",
                "produced_by_link_id": str(link["id"]),
                "schema_version": "v1",
                "artifact_markdown": artifact_markdown,
                "artifact_path": str(artifact_path),
                "artifact_sha256": sha256_text(artifact_markdown),
                "referenced_message_ids": list(body["referenced_message_ids"]),
                "validation_status": "valid",
            }
            event_type = "workflow_completed"
        elif message["type"] == "NEEDS_USER_REVIEW":
            next_state = "needs_user_review"
            event_type = "workflow_needs_user_review"

        self.store.complete_run_with_message(
            workflow_id=workflow_id,
            delivery_id=str(delivery["id"]),
            run_id=run_id,
            live_link_id=str(link["id"]),
            invocation=invocation,
            message=message,
            workflow_state_from=current_state,
            workflow_state_to=next_state,
            workflow_event={
                "id": f"evt-{uuid.uuid4().hex[:12]}",
                "event_type": event_type,
                "reason": reason,
                "stop_reason": reason if next_state in TERMINAL_WORKFLOW_STATES else None,
            },
            artifact=artifact_payload,
        )

        if next_state == "source_pending":
            self.store.create_delivery(
                delivery_id=f"delivery-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                target_link_id=str(workflow["source_link_id"]),
                kind="source_reply",
                payload={"from_message_id": message["id"], "request": message["body"]},
                workflow_state_from=next_state,
                workflow_state_to="source_pending",
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                reason="route interviewer output to source",
            )
        elif next_state == "interviewer_pending":
            self.store.create_delivery(
                delivery_id=f"delivery-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                target_link_id=str(workflow["interviewer_link_id"]),
                kind="interviewer_review",
                payload={"from_message_id": message["id"], "source_response": message["body"]},
                workflow_state_from=next_state,
                workflow_state_to="interviewer_pending",
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                reason="route source output to interviewer",
            )
        elif next_state == "artifact_pending":
            self.store.create_delivery(
                delivery_id=f"delivery-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                target_link_id=str(workflow["interviewer_link_id"]),
                kind="interviewer_artifact",
                payload={"from_message_id": message["id"], "source_response": message["body"]},
                workflow_state_from=next_state,
                workflow_state_to="artifact_pending",
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                reason="request final artifact from interviewer",
            )

    def _invalid_output_is_terminal(self, workflow_id: str, live_link_id: str, workflow: dict[str, Any]) -> bool:
        counts = self.store.counts_for_workflow(workflow_id)
        invalid_count = counts.get(f"invalid:{live_link_id}", 0) + 1
        limit = int(workflow["config_json"]["max_invalid_outputs_per_agent"])
        return invalid_count >= limit

    def _next_pending_delivery(self, workflow_id: str) -> dict[str, Any] | None:
        pending = self.store.get_pending_deliveries(workflow_id)
        return pending[0] if pending else None

    def _enforce_limits_before_wake(self, workflow_id: str) -> None:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError(f"workflow not found: {workflow_id}")
        counts = self.store.counts_for_workflow(workflow_id)
        config = workflow["config_json"]
        if counts["total_wakes"] >= int(config["max_total_wakes"]):
            self._force_terminal(workflow_id, "failed", "max_total_wakes_reached")
            return
        started_at = workflow["metrics_json"].get("started_at")
        if isinstance(started_at, str):
            elapsed = datetime_from_iso(utc_now_iso()) - datetime_from_iso(started_at)
            if elapsed.total_seconds() >= int(config["max_runtime_minutes"]) * 60:
                self._force_terminal(workflow_id, "failed", "max_runtime_minutes_reached")

    def _force_terminal(self, workflow_id: str, state: str, reason: str) -> None:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None or workflow["status"] in TERMINAL_WORKFLOW_STATES:
            return
        with self.store.connect() as conn:
            now = utc_now_iso()
            conn.execute(
                """
                UPDATE deliveries
                SET status = 'cancelled', error_text = ?, answered_at = ?
                WHERE workflow_id = ? AND status = 'pending'
                """,
                (reason, now, workflow_id),
            )
            conn.execute(
                "UPDATE handover_workflows SET status = ?, stop_reason = ?, updated_at = ? WHERE id = ?",
                (state, reason, now, workflow_id),
            )
            conn.execute(
                """
                INSERT INTO workflow_events (
                    id, workflow_id, event_type, previous_state, next_state, reason, created_at
                ) VALUES (?, ?, 'forced_terminal', ?, ?, ?, ?)
                """,
                (f"evt-{uuid.uuid4().hex[:12]}", workflow_id, workflow["status"], state, reason, now),
            )

    def _refresh_workflow_mirrors(self, workflow_id: str) -> None:
        audit = self.store.workflow_audit(workflow_id)
        workflow_dir = self.store.workflows_dir / workflow_id
        workflow_dir.mkdir(parents=True, exist_ok=True)
        events_path = workflow_dir / "workflow-events.jsonl"
        with events_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in audit["events"]:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        transcript_path = workflow_dir / "messages.jsonl"
        with transcript_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in audit["messages"]:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        artifact = audit["artifact"]
        if artifact:
            mirror_path = workflow_dir / "final-artifact.md"
            mirror_path.write_text(str(artifact["artifact_markdown"]), encoding="utf-8", newline="\n")
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE artifacts SET mirror_path = ?, mirror_sha256 = ? WHERE id = ?",
                    (str(mirror_path), sha256_text(str(artifact["artifact_markdown"])), artifact["id"]),
                )

    def _require_driver(self, driver_id: str) -> Driver:
        driver = self.drivers.get(driver_id)
        if driver is None:
            raise ValueError(f"unknown driver: {driver_id}")
        if getattr(driver, "kind", None) not in SUPPORTED_DRIVER_KINDS:
            raise ValueError(f"driver kind not supported for live handover: {getattr(driver, 'kind', 'unknown')}")
        return driver

    def _require_live_link(self, link_id: str) -> dict[str, Any]:
        link = self.store.get_live_link(link_id)
        if link is None:
            raise ValueError(f"live link not found: {link_id}")
        return link

    def _canonicalize_external_ref(self, external_ref: str) -> str:
        return external_ref.strip()

    def _bind_external_session(self, driver: Driver, room_id: str, session_ref: str) -> None:
        sessions = getattr(driver, "sessions", None)
        if not isinstance(sessions, dict):
            raise ValueError(f"driver does not support external session binding: {driver.id}")
        sessions[room_id] = session_ref
        set_session_cwd = getattr(driver, "set_session_cwd", None)
        if callable(set_session_cwd) and getattr(driver, "kind", None) == "claude-code-new":
            set_session_cwd(room_id, "C:/Users/chris/PROJECTS")
        persist = getattr(driver, "_persist_session", None)
        if callable(persist):
            persist(room_id, session_ref)

    def _ensure_driver_bound(self, link: dict[str, Any]) -> None:
        driver = self._require_driver(str(link["driver_id"]))
        self._bind_external_session(
            driver,
            str(link["binding_room_id"]),
            str(link["canonical_external_session_ref"]),
        )

    def _unbind_external_session(self, driver: Driver, room_id: str) -> None:
        sessions = getattr(driver, "sessions", None)
        if isinstance(sessions, dict):
            sessions.pop(room_id, None)
        set_session_cwd = getattr(driver, "set_session_cwd", None)
        if callable(set_session_cwd):
            set_session_cwd(room_id, None)
        session_file = getattr(driver, "_session_file", None)
        if callable(session_file):
            try:
                Path(session_file(room_id)).unlink(missing_ok=True)
            except OSError:
                pass


def datetime_from_iso(value: str):
    normalized = value.replace("Z", "+00:00")
    from datetime import datetime

    return datetime.fromisoformat(normalized)
