from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agora.drivers.base import Driver, DriverError


PRIMARY_PAIR_VERSION = "primary-pair-backend-v1"

REQUIRED_DOCUMENT_SECTIONS = (
    "VERDICT",
    "AGREEMENTS",
    "REMAINING DISAGREEMENTS",
    "REASONING AND EVIDENCE",
    "FINAL DOCUMENT",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preview_text(text: str, limit: int = 360) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed[:limit] + ("..." if len(collapsed) > limit else "")


def marker_from_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "MISSING"
    last = lines[-1]
    if last == "CONVERGED":
        return "CONVERGED"
    if last.startswith("NOT_CONVERGED"):
        return "NOT_CONVERGED"
    return "MISSING"


def has_required_sections(text: str) -> bool:
    upper = text.upper()
    return all(section in upper for section in REQUIRED_DOCUMENT_SECTIONS)


def extract_section(text: str, heading: str) -> str:
    upper = text.upper()
    heading_upper = heading.upper()
    start = upper.find(heading_upper)
    if start < 0:
        return ""
    start += len(heading)
    end = len(text)
    for candidate in (
        "AGREEMENTS",
        "REMAINING DISAGREEMENTS",
        "REASONING AND EVIDENCE",
        "FINAL DOCUMENT",
    ):
        candidate_upper = candidate.upper()
        if candidate_upper == heading_upper:
            continue
        index = upper.find(candidate_upper, start)
        if index >= 0:
            end = min(end, index)
    return text[start:end].strip()


def remaining_disagreements_allow_convergence(text: str) -> bool:
    remaining = extract_section(text, "REMAINING DISAGREEMENTS").lower()
    if not remaining:
        return False
    none_phrases = (
        "none",
        "no substantive disagreement",
        "no material disagreement",
        "no remaining disagreement",
        "only difference is emphasis",
        "only residual difference is emphasis",
        "differences in emphasis",
    )
    if any(phrase in remaining for phrase in none_phrases):
        return True
    return False


@dataclass(slots=True)
class RoleSpec:
    role_key: str
    label: str
    driver: Driver
    logical_role: str
    model: str | None = None
    effort: str | None = None


@dataclass(slots=True)
class ArtifactRef:
    artifact_id: str
    role_key: str
    label: str
    phase: str
    path: str
    sha256: str
    preview: str
    marker: str = "NA"


@dataclass(slots=True)
class PrimaryPairConfig:
    brief: str
    primary_a: RoleSpec
    primary_b: RoleSpec
    secondary: RoleSpec
    run_id: str | None = None
    max_revision_turns: int = 4
    root_dir: Path = Path("C:/Users/chris/PROJECTS/agora/data/primary-pair-runs")
    event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None


@dataclass(slots=True)
class PrimaryPairResult:
    run_id: str
    status: str
    stop_reason: str
    run_dir: str
    ledger_path: str
    final_artifact: ArtifactRef | None
    validation: dict[str, Any]
    turns: int


class PrimaryPairRunner:
    def __init__(self, config: PrimaryPairConfig) -> None:
        self.config = config
        self.run_id = config.run_id or f"pp-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.run_dir = config.root_dir / self.run_id
        self.artifact_dir = self.run_dir / "artifacts"
        self.ledger_path = self.run_dir / "turn-ledger.jsonl"
        self.prompts_path = self.run_dir / "prompt-ledger.jsonl"
        self.roles = {
            "primary_a": config.primary_a,
            "primary_b": config.primary_b,
            "secondary": config.secondary,
        }
        self.artifacts: dict[str, ArtifactRef] = {}
        self.turn_count = 0
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> PrimaryPairResult:
        self._write_run_manifest("running")
        await self._record_event(
            {
                "event": "run_start",
                "protocol": PRIMARY_PAIR_VERSION,
                "brief_hash": sha256_text(self.config.brief),
                "brief_preview": preview_text(self.config.brief),
                "roles": {key: self._role_snapshot(role) for key, role in self.roles.items()},
                "max_revision_turns": self.config.max_revision_turns,
            }
        )
        try:
            await self._record_event({"event": "logical_parallel_group_start", "group": "R1", "artifacts": ["A0", "B0", "S0"]})
            a0, b0, s0 = await asyncio.gather(
                self._turn("R1", "A0", self.config.primary_a, self._seed_prompt(self.config.primary_a)),
                self._turn("R1", "B0", self.config.primary_b, self._seed_prompt(self.config.primary_b)),
                self._turn("R1", "S0", self.config.secondary, self._seed_prompt(self.config.secondary)),
            )
            await self._record_event({"event": "logical_parallel_group_complete", "group": "R1", "artifacts": ["A0", "B0", "S0"]})

            await self._record_event({"event": "logical_parallel_group_start", "group": "R2", "artifacts": ["A1", "B1"]})
            a1, b1 = await asyncio.gather(
                self._turn("R2A", "A1", self.config.primary_a, self._first_document_prompt(self.config.primary_a, [a0, b0, s0])),
                self._turn("R2B", "B1", self.config.primary_b, self._first_document_prompt(self.config.primary_b, [a0, b0, s0])),
            )
            await self._record_event({"event": "logical_parallel_group_complete", "group": "R2", "artifacts": ["A1", "B1"]})

            latest_a = a1
            latest_b = b1
            final_artifact: ArtifactRef | None = None
            stop_reason = "revision_cap_reached"
            revision_index = 0
            while revision_index < self.config.max_revision_turns:
                if revision_index % 2 == 0:
                    artifact_label = f"B{2 + revision_index // 2}"
                    latest_b = await self._turn(
                        f"R{3 + revision_index}B",
                        artifact_label,
                        self.config.primary_b,
                        self._revision_prompt(self.config.primary_b, latest_b, latest_a),
                    )
                    final_artifact = latest_b
                else:
                    artifact_label = f"A{2 + revision_index // 2}"
                    latest_a = await self._turn(
                        f"R{3 + revision_index}A",
                        artifact_label,
                        self.config.primary_a,
                        self._revision_prompt(self.config.primary_a, latest_a, latest_b),
                    )
                    final_artifact = latest_a
                revision_index += 1
                if self._is_reciprocal_convergence(latest_a, latest_b, final_artifact):
                    stop_reason = "reciprocal_convergence"
                    break

            validation = analyze_primary_pair_ledger(
                self.ledger_path,
                expected_roles={key: self._role_snapshot(role) for key, role in self.roles.items()},
            )
            converged = stop_reason == "reciprocal_convergence"
            if not validation["ok"]:
                status = "failed_validation"
            elif converged:
                status = "converged"
            else:
                status = "stopped_at_cap"
            await self._record_event(
                {
                    "event": "run_complete",
                    "status": status,
                    "stop_reason": stop_reason,
                    "converged": converged,
                    "final_artifact": asdict(final_artifact) if final_artifact else None,
                    "validation": validation,
                    "turns": self.turn_count,
                }
            )
            self._write_run_manifest(
                status,
                stop_reason=stop_reason,
                converged=converged,
                validation=validation,
                final_artifact=final_artifact,
            )
            return PrimaryPairResult(
                run_id=self.run_id,
                status=status,
                stop_reason=stop_reason,
                run_dir=str(self.run_dir),
                ledger_path=str(self.ledger_path),
                final_artifact=final_artifact,
                validation=validation,
                turns=self.turn_count,
            )
        except Exception as exc:
            await self._record_event({"event": "run_error", "error_type": type(exc).__name__, "error": str(exc)})
            self._write_run_manifest("failed", error=f"{type(exc).__name__}: {exc}")
            raise

    def _is_reciprocal_convergence(
        self,
        latest_a: ArtifactRef,
        latest_b: ArtifactRef,
        final_artifact: ArtifactRef | None,
    ) -> bool:
        if final_artifact is None:
            return False
        # Revisions start with LLM2 reviewing LLM1, then LLM1 reviewing LLM2.
        # An LLM2-side CONVERGED marker can be one-sided because LLM1 has not
        # yet seen LLM2's latest text. Final convergence requires LLM1 to
        # confirm after receiving LLM2's latest converged document.
        return (
            final_artifact.role_key == "primary_a"
            and latest_a.marker == "CONVERGED"
            and latest_b.marker == "CONVERGED"
        )

    async def _turn(self, phase: str, artifact_id: str, role: RoleSpec, prompt: str) -> ArtifactRef:
        self.turn_count += 1
        turn_id = f"{self.turn_count:03d}-{phase}-{artifact_id}-{role.role_key}"
        session_key = f"{self.run_id}-{role.role_key}"
        attempt_prompt = prompt
        last_issues: list[str] = []
        for attempt in range(1, 4):
            suffix = "" if attempt == 1 else f".attempt{attempt}"
            prompt_file = self.artifact_dir / f"{turn_id}{suffix}.prompt.md"
            prompt_file.write_text(attempt_prompt, encoding="utf-8", newline="\n")
            prompt_hash = sha256_text(attempt_prompt)
            session_before = self._session_id(role, session_key)
            self._append_prompt(
                {
                    "turn_id": turn_id,
                    "attempt": attempt,
                    "phase": phase,
                    "artifact_id": artifact_id,
                    "role_key": role.role_key,
                    "prompt_path": str(prompt_file),
                    "prompt_sha256": prompt_hash,
                    "prompt_preview": preview_text(attempt_prompt),
                }
            )
            await self._record_event(
                {
                    "event": "turn_start",
                    "turn_id": turn_id,
                    "attempt": attempt,
                    "phase": phase,
                    "artifact_id": artifact_id,
                    "role": self._role_snapshot(role),
                    "session_key": session_key,
                    "session_before": session_before,
                    "prompt_path": str(prompt_file),
                    "prompt_sha256": prompt_hash,
                    "prompt_preview": preview_text(attempt_prompt),
                    "input_artifacts": self._extract_input_refs(attempt_prompt),
                }
            )
            started = time.perf_counter()
            try:
                if await role.driver.has_session(session_key):
                    reply = await role.driver.send_in_session(session_key, attempt_prompt)
                else:
                    await role.driver.start_session(session_key, attempt_prompt, prime_reply=True)
                    reply = await role.driver.send_in_session(session_key, "__return_bootstrap_reply__")
            except DriverError:
                raise
            except Exception as exc:
                raise DriverError(f"{role.label} turn {turn_id} failed: {exc}") from exc
            latency_ms = round((time.perf_counter() - started) * 1000)
            session_after = self._session_id(role, session_key) or reply.resume_id
            output_file = self.artifact_dir / f"{turn_id}{suffix}.reply.md"
            output_file.write_text(reply.content, encoding="utf-8", newline="\n")
            last_issues = self._reply_issues(artifact_id, reply.content)
            if last_issues and attempt < 3:
                await self._record_event(
                    {
                        "event": "turn_invalid_retry",
                        "turn_id": turn_id,
                        "attempt": attempt,
                        "phase": phase,
                        "artifact_id": artifact_id,
                        "role": self._role_snapshot(role),
                        "session_key": session_key,
                        "session_after": session_after,
                        "reply_path": str(output_file),
                        "reply_sha256": sha256_text(reply.content),
                        "issues": last_issues,
                        "reply_preview": preview_text(reply.content),
                    }
                )
                attempt_prompt = self._repair_prompt(artifact_id, prompt, reply.content, last_issues)
                continue
            artifact = ArtifactRef(
                artifact_id=artifact_id,
                role_key=role.role_key,
                label=role.label,
                phase=phase,
                path=str(output_file),
                sha256=sha256_text(reply.content),
                preview=preview_text(reply.content),
                marker=marker_from_text(reply.content),
            )
            self.artifacts[artifact_id] = artifact
            await self._record_event(
                {
                    "event": "turn_complete",
                    "turn_id": turn_id,
                    "attempt": attempt,
                    "phase": phase,
                    "artifact_id": artifact_id,
                    "role": self._role_snapshot(role),
                    "session_key": session_key,
                    "session_before": session_before,
                    "session_after": session_after,
                    "prompt_path": str(prompt_file),
                    "prompt_sha256": prompt_hash,
                    "reply_path": str(output_file),
                    "reply_sha256": artifact.sha256,
                    "reply_preview": artifact.preview,
                    "reply_marker": artifact.marker,
                    "reply_has_required_sections": has_required_sections(reply.content),
                    "reply_validation_issues": last_issues,
                    "latency_ms": latency_ms,
                    "raw_output_sha256": sha256_text(reply.raw_output or ""),
                }
            )
            return artifact
        raise DriverError(f"{role.label} turn {turn_id} failed validation: {last_issues}")

    def _seed_prompt(self, role: RoleSpec) -> str:
        return f"""Produce an independent written view.

Participant label: {role.label}.

Brief:
{self.config.brief}

Round R1 / seed output.
Produce your independent view. You have no other documents in this round.

Output exactly these sections:
WORKING INTERPRETATION
DIRECT ANSWER
SUPPORTING REASONS
MATERIAL UNCERTAINTIES

Keep it concise and substantive. Record only uncertainties that could materially change the answer.
The first line of your answer must be WORKING INTERPRETATION.
"""

    def _first_document_prompt(self, role: RoleSpec, seed_artifacts: list[ArtifactRef]) -> str:
        attached = "\n\n".join(self._artifact_block(ref) for ref in seed_artifacts)
        return f"""Produce a complete candidate final document.

Participant label: {role.label}.

Task: create your first complete candidate final document from the original brief and all three seed views.

Brief:
{self.config.brief}

Seed inputs:
{attached}

Round R2 first document requirements:
- Decide which seed claims you accept.
- Decide which seed claims you disagree with.
- Carry a full agreement register, not only deltas.
- State remaining disagreements explicitly and give thorough explanation why you disagree.
- Produce a complete answer usable by the requestor.
- The FINAL DOCUMENT section must be requestor-facing prose and must not repeat the internal section headings.
- End with exactly one final line.
- If you believe the three seed documents already agree on all material points, the final line must be exactly: CONVERGED
- Otherwise the final line must be exactly: NOT_CONVERGED: <short reason>
- Do not write CONVERGED with a colon, reason, punctuation, or explanation after it.
- You may write CONVERGED only if REMAINING DISAGREEMENTS says there are no material/substantive disagreements. If that section lists unresolved bullets, you must write NOT_CONVERGED.

Output exactly these top-level sections:
VERDICT
AGREEMENTS
REMAINING DISAGREEMENTS
REASONING AND EVIDENCE
FINAL DOCUMENT
"""

    def _revision_prompt(self, role: RoleSpec, own_latest: ArtifactRef, other_latest: ArtifactRef) -> str:
        return f"""Produce a revised complete candidate final document.

Participant label: {role.label}.

You are in a persistent session. You already have the original brief and your own prior context.
Revise your complete candidate document after reading the other LLM's latest full document.

Your latest document reference:
{self._artifact_block(own_latest)}

Other LLM's latest document:
{self._artifact_block(other_latest)}

Update your current document based on these revision requirements:
- Accept improvements from the other LLM where they are stronger.
- Reject ideas that are wrong, unsupported, irrelevant, or weaker.
- Preserve and update the full agreement register; do not output only deltas.
- Preserve and update remaining disagreements, with thorough explanation why you disagree.
- Produce a complete answer usable by the requestor.
- The FINAL DOCUMENT section must be requestor-facing prose and must not repeat the internal section headings.
- End with exactly one final line.
- If you believe your revised document and the other LLM's latest document now agree on all material points, the final line must be exactly: CONVERGED
- Otherwise the final line must be exactly: NOT_CONVERGED: <short reason>
- Do not write CONVERGED with a colon, reason, punctuation, or explanation after it.
- You may write CONVERGED only if REMAINING DISAGREEMENTS says there are no material/substantive disagreements. If that section lists unresolved bullets, you must write NOT_CONVERGED.

Output exactly these top-level sections:
VERDICT
AGREEMENTS
REMAINING DISAGREEMENTS
REASONING AND EVIDENCE
FINAL DOCUMENT
"""

    def _repair_prompt(self, artifact_id: str, original_prompt: str, invalid_reply: str, issues: list[str]) -> str:
        return f"""Your previous reply for artifact {artifact_id} failed output validation.

Validation issues:
{chr(10).join(f"- {issue}" for issue in issues)}

Previous invalid reply:
{invalid_reply}

Produce a replacement answer now. Do not explain the validation failure.

Original task follows:
{original_prompt}
"""

    def _reply_issues(self, artifact_id: str, text: str) -> list[str]:
        issues: list[str] = []
        upper = text.upper()
        if artifact_id in {"A0", "B0", "S0"}:
            for heading in (
                "WORKING INTERPRETATION",
                "DIRECT ANSWER",
                "SUPPORTING REASONS",
                "MATERIAL UNCERTAINTIES",
            ):
                if heading not in upper:
                    issues.append(f"seed reply missing {heading}")
            if len(text.strip()) < 300:
                issues.append("seed reply too short to be substantive")
            return issues
        if not has_required_sections(text):
            issues.append("full document missing one or more required sections")
        final_document = text.upper().split("FINAL DOCUMENT", 1)[-1]
        if any(
            heading in final_document
            for heading in (
                "\nWORKING INTERPRETATION",
                "\nDIRECT ANSWER",
                "\nSUPPORTING REASONS",
                "\nMATERIAL UNCERTAINTIES",
            )
        ):
            issues.append("final document contains internal seed headings")
        marker = marker_from_text(text)
        if marker == "MISSING":
            issues.append("full document missing exact final convergence marker")
        if marker == "CONVERGED" and not remaining_disagreements_allow_convergence(text):
            issues.append("CONVERGED marker conflicts with unresolved remaining disagreements")
        return issues

    def _artifact_block(self, ref: ArtifactRef) -> str:
        text = Path(ref.path).read_text(encoding="utf-8")
        return f"""[{ref.artifact_id} | {ref.label} | {ref.phase} | sha256={ref.sha256}]
{text}
[/end {ref.artifact_id}]"""

    def _extract_input_refs(self, prompt: str) -> list[str]:
        return re.findall(r"\[([ABS]\d+)\s+\|", prompt)

    def _role_snapshot(self, role: RoleSpec) -> dict[str, Any]:
        return {
            "role_key": role.role_key,
            "label": role.label,
            "logical_role": role.logical_role,
            "driver_id": role.driver.id,
            "driver_kind": getattr(role.driver, "kind", "unknown"),
            "model": role.model or getattr(role.driver, "model", None),
            "effort": role.effort or getattr(role.driver, "effort", None),
        }

    def _session_id(self, role: RoleSpec, session_key: str) -> str | None:
        sessions = getattr(role.driver, "sessions", None)
        if isinstance(sessions, dict):
            value = sessions.get(session_key)
            return value if isinstance(value, str) else None
        return None

    def _append_event(self, payload: dict[str, Any]) -> None:
        payload = {"ts": utc_now_iso(), "run_id": self.run_id, **payload}
        with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def _record_event(self, payload: dict[str, Any]) -> None:
        full_payload = {"ts": utc_now_iso(), "run_id": self.run_id, **payload}
        with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(full_payload, ensure_ascii=False) + "\n")
        if self.config.event_callback is not None:
            await self.config.event_callback(full_payload)

    def _append_prompt(self, payload: dict[str, Any]) -> None:
        payload = {"ts": utc_now_iso(), "run_id": self.run_id, **payload}
        with self.prompts_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_run_manifest(
        self,
        status: str,
        *,
        stop_reason: str | None = None,
        converged: bool = False,
        validation: dict[str, Any] | None = None,
        final_artifact: ArtifactRef | None = None,
        error: str | None = None,
    ) -> None:
        payload = {
            "run_id": self.run_id,
            "status": status,
            "stop_reason": stop_reason,
            "converged": converged,
            "protocol": PRIMARY_PAIR_VERSION,
            "updated_at": utc_now_iso(),
            "brief_hash": sha256_text(self.config.brief),
            "roles": {key: self._role_snapshot(role) for key, role in self.roles.items()},
            "ledger_path": str(self.ledger_path),
            "prompt_ledger_path": str(self.prompts_path),
            "artifact_dir": str(self.artifact_dir),
            "validation": validation,
            "final_artifact": asdict(final_artifact) if final_artifact else None,
            "error": error,
        }
        (self.run_dir / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def analyze_primary_pair_ledger(path: str | Path, expected_roles: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    ledger_path = Path(path)
    rows = read_jsonl(ledger_path)
    issues: list[str] = []
    complete = [row for row in rows if row.get("event") == "turn_complete"]
    starts = [row for row in rows if row.get("event") == "turn_start"]
    by_artifact = {row.get("artifact_id"): row for row in complete}

    initial = {row.get("artifact_id") for row in complete if row.get("artifact_id") in {"A0", "B0", "S0", "A1", "B1"}}
    expected_initial = {"A0", "B0", "S0", "A1", "B1"}
    if initial != expected_initial:
        issues.append(f"unexpected initial artifacts: {sorted(initial)}; expected {sorted(expected_initial)}")

    expected_inputs = {
        "A0": [],
        "B0": [],
        "S0": [],
        "A1": ["A0", "B0", "S0"],
        "B1": ["A0", "B0", "S0"],
        "B2": ["B1", "A1"],
        "A2": ["A1", "B2"],
        "B3": ["B2", "A2"],
        "A3": ["A2", "B3"],
    }
    for start in starts:
        artifact_id = str(start.get("artifact_id") or "")
        if artifact_id in expected_inputs:
            refs = start.get("input_artifacts") or []
            if refs != expected_inputs[artifact_id]:
                issues.append(f"{artifact_id} input refs {refs}; expected {expected_inputs[artifact_id]}")

    for row in complete:
        artifact_id = str(row.get("artifact_id") or "")
        validation_issues = row.get("reply_validation_issues") or []
        if validation_issues:
            issues.append(f"{artifact_id} completed with validation issues: {validation_issues}")
        if re.match(r"^[AB][1-9]\d*$", artifact_id):
            if not row.get("reply_has_required_sections"):
                issues.append(f"{artifact_id} missing required full-document sections")
            if row.get("reply_marker") == "MISSING":
                issues.append(f"{artifact_id} missing convergence marker")

    sessions_by_role: dict[str, set[str]] = {}
    for row in complete:
        role_key = ((row.get("role") or {}).get("role_key")) or ""
        session = row.get("session_after")
        if role_key and session:
            sessions_by_role.setdefault(role_key, set()).add(str(session))
    for role_key, sessions in sessions_by_role.items():
        if len(sessions) != 1:
            issues.append(f"{role_key} used multiple session ids: {sorted(sessions)}")

    expected_models = expected_roles or {}
    for row in complete:
        role = row.get("role") or {}
        role_key = role.get("role_key")
        expected = expected_models.get(role_key) if isinstance(expected_models, dict) else None
        if not expected:
            continue
        expected_kind = expected.get("driver_kind")
        expected_model = expected.get("model")
        expected_effort = expected.get("effort")
        if expected_kind is not None and role.get("driver_kind") != expected_kind:
            issues.append(f"{role_key} driver kind {role.get('driver_kind')}; expected {expected_kind}")
        if expected_model is not None and role.get("model") != expected_model:
            issues.append(f"{role_key} model {role.get('model')}; expected {expected_model}")
        if expected_effort is not None and role.get("effort") != expected_effort:
            issues.append(f"{role_key} effort {role.get('effort')}; expected {expected_effort}")

    for row in complete:
        path_value = row.get("reply_path")
        if path_value and Path(path_value).exists():
            text = Path(path_value).read_text(encoding="utf-8", errors="replace")
            lowered = text.lower()
            if "what is agora" in lowered or "i don't know what agora" in lowered:
                issues.append(f"{row.get('artifact_id')} appears to analyze Agora instead of the brief")
            if any(phrase in lowered for phrase in ("recent search", "i searched", "web search", "workspace files")):
                issues.append(f"{row.get('artifact_id')} appears to rely on tools/search/workspace context")
            if re.match(r"^[AB][1-9]\d*$", str(row.get("artifact_id") or "")):
                final_document = text.upper().split("FINAL DOCUMENT", 1)[-1]
                if any(
                    heading in final_document
                    for heading in (
                        "\nWORKING INTERPRETATION",
                        "\nDIRECT ANSWER",
                        "\nSUPPORTING REASONS",
                        "\nMATERIAL UNCERTAINTIES",
                    )
                ):
                    issues.append(f"{row.get('artifact_id')} final document contains internal seed headings")
                if row.get("reply_marker") == "CONVERGED" and not remaining_disagreements_allow_convergence(text):
                    issues.append(f"{row.get('artifact_id')} CONVERGED marker conflicts with unresolved remaining disagreements")

    return {
        "ok": not issues,
        "issues": issues,
        "turns": len(complete),
        "artifacts": [row.get("artifact_id") for row in complete],
        "sessions_by_role": {key: sorted(value) for key, value in sessions_by_role.items()},
    }
