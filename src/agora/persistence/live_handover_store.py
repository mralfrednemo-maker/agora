from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


TERMINAL_WORKFLOW_STATES = {"completed", "needs_user_review", "failed", "cancelled"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class LiveHandoverStore:
    root_dir: Path
    db_path: Path = field(init=False)
    links_dir: Path = field(init=False)
    workflows_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root_dir / "live-handover.db"
        self.links_dir = self.root_dir / "links"
        self.workflows_dir = self.root_dir / "workflows"
        self.links_dir.mkdir(parents=True, exist_ok=True)
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_links (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    driver_id TEXT NOT NULL,
                    driver_kind TEXT NOT NULL,
                    model_identity TEXT,
                    external_session_ref TEXT NOT NULL,
                    canonical_external_session_ref TEXT NOT NULL,
                    binding_room_id TEXT NOT NULL UNIQUE,
                    verification_status TEXT NOT NULL,
                    verification_prompt_path TEXT,
                    verification_prompt_sha256 TEXT,
                    ack_reply_path TEXT,
                    ack_reply_sha256 TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    verified_at TEXT
                );

                CREATE TABLE IF NOT EXISTS handover_workflows (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    interviewer_link_id TEXT NOT NULL REFERENCES live_links(id),
                    source_link_id TEXT NOT NULL REFERENCES live_links(id),
                    status TEXT NOT NULL,
                    stop_reason TEXT,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    final_artifact_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES handover_workflows(id) ON DELETE CASCADE,
                    target_link_id TEXT NOT NULL REFERENCES live_links(id),
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT,
                    answered_at TEXT,
                    error_text TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES handover_workflows(id) ON DELETE CASCADE,
                    live_link_id TEXT NOT NULL REFERENCES live_links(id),
                    delivery_id TEXT NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_text TEXT
                );

                CREATE TABLE IF NOT EXISTS driver_invocations (
                    id TEXT PRIMARY KEY,
                    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    driver_id TEXT NOT NULL,
                    driver_kind TEXT NOT NULL,
                    model_identity TEXT,
                    external_session_ref TEXT NOT NULL,
                    prompt_path TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    reply_path TEXT NOT NULL,
                    reply_sha256 TEXT NOT NULL,
                    raw_output_path TEXT,
                    raw_output_sha256 TEXT,
                    exit_code INTEGER,
                    timeout_status INTEGER NOT NULL DEFAULT 0,
                    stderr_summary TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES handover_workflows(id) ON DELETE CASCADE,
                    live_link_id TEXT NOT NULL REFERENCES live_links(id),
                    delivery_id TEXT NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,
                    agent_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    references_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workflow_id, seq)
                );

                CREATE TABLE IF NOT EXISTS workflow_events (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES handover_workflows(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    previous_state TEXT,
                    next_state TEXT,
                    caused_by_delivery_id TEXT,
                    caused_by_agent_run_id TEXT,
                    caused_by_message_id TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES handover_workflows(id) ON DELETE CASCADE,
                    produced_by_link_id TEXT NOT NULL REFERENCES live_links(id),
                    schema_version TEXT NOT NULL,
                    artifact_markdown TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    referenced_message_ids_json TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    mirror_path TEXT,
                    mirror_sha256 TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    from_link_id TEXT REFERENCES live_links(id),
                    to_link_id TEXT NOT NULL REFERENCES live_links(id),
                    subject TEXT,
                    body TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    requires_ack INTEGER NOT NULL DEFAULT 1,
                    response_body TEXT,
                    response_json TEXT,
                    error_text TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    read_at TEXT,
                    answered_at TEXT,
                    acknowledged_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_live_links_active_identity
                ON live_links(driver_kind, canonical_external_session_ref)
                WHERE status = 'active';

                CREATE UNIQUE INDEX IF NOT EXISTS idx_active_run_per_link
                ON agent_runs(live_link_id)
                WHERE status = 'running';

                CREATE INDEX IF NOT EXISTS idx_deliveries_workflow_status
                ON deliveries(workflow_id, status, created_at);

                CREATE INDEX IF NOT EXISTS idx_agent_messages_to_status
                ON agent_messages(to_link_id, status, created_at);
                """
            )

    def list_live_links(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM live_links WHERE status = 'active' ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_live_link(self, link_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM live_links WHERE id = ?", (link_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_active_live_link_by_identity(self, driver_kind: str, canonical_external_session_ref: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM live_links
                WHERE driver_kind = ?
                  AND canonical_external_session_ref = ?
                  AND status = 'active'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (driver_kind, canonical_external_session_ref),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def first_active_live_link_for_driver(self, driver_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM live_links
                WHERE driver_id = ?
                  AND status = 'active'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (driver_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def record_live_link(self, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO live_links (
                    id, label, driver_id, driver_kind, model_identity,
                    external_session_ref, canonical_external_session_ref, binding_room_id,
                    verification_status, verification_prompt_path, verification_prompt_sha256,
                    ack_reply_path, ack_reply_sha256, status, metadata_json,
                    created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["label"],
                    payload["driver_id"],
                    payload["driver_kind"],
                    payload.get("model_identity"),
                    payload["external_session_ref"],
                    payload["canonical_external_session_ref"],
                    payload["binding_room_id"],
                    payload["verification_status"],
                    payload.get("verification_prompt_path"),
                    payload.get("verification_prompt_sha256"),
                    payload.get("ack_reply_path"),
                    payload.get("ack_reply_sha256"),
                    payload.get("status", "active"),
                    json.dumps(payload.get("metadata", {}), ensure_ascii=False),
                    payload["created_at"],
                    payload.get("verified_at"),
                ),
            )

    def create_workflow(self, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO handover_workflows (
                    id, goal, interviewer_link_id, source_link_id, status, stop_reason,
                    config_json, metrics_json, final_artifact_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["goal"],
                    payload["interviewer_link_id"],
                    payload["source_link_id"],
                    payload["status"],
                    payload.get("stop_reason"),
                    json.dumps(payload["config"], ensure_ascii=False),
                    json.dumps(payload.get("metrics", {}), ensure_ascii=False),
                    payload.get("final_artifact_id"),
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM handover_workflows WHERE id = ?", (workflow_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list_workflow_messages(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE workflow_id = ? ORDER BY seq ASC",
                (workflow_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY created_at ASC, id ASC",
                (workflow_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_workflow_artifact(self, workflow_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_pending_deliveries(self, workflow_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM deliveries WHERE workflow_id = ? AND status = 'pending' ORDER BY created_at ASC",
                (workflow_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def create_agent_message(
        self,
        *,
        message_id: str,
        to_link_id: str,
        body: str,
        from_link_id: str | None = None,
        subject: str | None = None,
        payload: dict[str, Any] | None = None,
        requires_ack: bool = True,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (
                    id, from_link_id, to_link_id, subject, body, payload_json,
                    status, requires_ack, created_at, delivered_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'delivered', ?, ?, ?, ?)
                """,
                (
                    message_id,
                    from_link_id,
                    to_link_id,
                    subject,
                    body,
                    json.dumps(payload or {}, ensure_ascii=False),
                    1 if requires_ack else 0,
                    now,
                    now,
                    now,
                ),
            )
        message = self.get_agent_message(message_id)
        assert message is not None
        return message

    def get_agent_message(self, message_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list_agent_messages(
        self,
        *,
        to_link_id: str | None = None,
        status: str | None = None,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if to_link_id:
            clauses.append("to_link_id = ?")
            params.append(to_link_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        elif not include_terminal:
            clauses.append("status IN ('delivered', 'read')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM agent_messages
                {where}
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def mark_agent_message_read(self, message_id: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_messages
                SET status = CASE WHEN status = 'delivered' THEN 'read' ELSE status END,
                    read_at = COALESCE(read_at, ?),
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('delivered', 'read')
                """,
                (now, now, message_id),
            )
        return self.get_agent_message(message_id)

    def answer_agent_message(
        self,
        *,
        message_id: str,
        response_body: str,
        response: dict[str, Any],
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_messages
                SET status = 'answered',
                    response_body = ?,
                    response_json = ?,
                    answered_at = ?,
                    updated_at = ?,
                    error_text = NULL
                WHERE id = ?
                  AND status IN ('delivered', 'read')
                """,
                (response_body, json.dumps(response, ensure_ascii=False), now, now, message_id),
            )
        return self.get_agent_message(message_id)

    def acknowledge_agent_message(self, message_id: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_messages
                SET status = 'acknowledged',
                    acknowledged_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = 'answered'
                """,
                (now, now, message_id),
            )
        return self.get_agent_message(message_id)

    def fail_agent_message(self, *, message_id: str, error_text: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_messages
                SET status = 'failed',
                    error_text = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('delivered', 'read')
                """,
                (error_text, now, message_id),
            )
        return self.get_agent_message(message_id)

    def create_delivery(
        self,
        *,
        delivery_id: str,
        workflow_id: str,
        target_link_id: str,
        kind: str,
        payload: dict[str, Any],
        workflow_state_from: str,
        workflow_state_to: str,
        event_id: str,
        reason: str,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            workflow = conn.execute(
                "SELECT status FROM handover_workflows WHERE id = ?",
                (workflow_id,),
            ).fetchone()
            if workflow is None:
                conn.execute("ROLLBACK")
                raise ValueError(f"workflow not found: {workflow_id}")
            current_status = str(workflow["status"])
            if current_status in TERMINAL_WORKFLOW_STATES:
                conn.execute("ROLLBACK")
                raise ValueError(f"workflow is terminal: {workflow_id}")
            conn.execute(
                """
                INSERT INTO deliveries (
                    id, workflow_id, target_link_id, kind, payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (delivery_id, workflow_id, target_link_id, kind, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.execute(
                """
                UPDATE handover_workflows
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (workflow_state_to, now, workflow_id),
            )
            conn.execute(
                """
                INSERT INTO workflow_events (
                    id, workflow_id, event_type, previous_state, next_state, reason, created_at
                ) VALUES (?, ?, 'delivery_created', ?, ?, ?, ?)
                """,
                (event_id, workflow_id, workflow_state_from, workflow_state_to, reason, now),
            )
            conn.execute("COMMIT")

    def claim_delivery(self, *, delivery_id: str, live_link_id: str, run_id: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT d.*, w.status AS workflow_status
                FROM deliveries d
                JOIN handover_workflows w ON w.id = d.workflow_id
                WHERE d.id = ?
                """,
                (delivery_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            if row["status"] != "pending" or row["target_link_id"] != live_link_id:
                conn.execute("ROLLBACK")
                return None
            if row["workflow_status"] in TERMINAL_WORKFLOW_STATES:
                conn.execute("ROLLBACK")
                return None
            active = conn.execute(
                "SELECT 1 FROM agent_runs WHERE live_link_id = ? AND status = 'running' LIMIT 1",
                (live_link_id,),
            ).fetchone()
            if active is not None:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE deliveries SET status = 'claimed', claimed_at = ? WHERE id = ?",
                (now, delivery_id),
            )
            conn.execute(
                """
                INSERT INTO agent_runs (
                    id, workflow_id, live_link_id, delivery_id, status, created_at
                ) VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (run_id, row["workflow_id"], live_link_id, delivery_id, now),
            )
            conn.execute("COMMIT")
        claimed = self.get_delivery(delivery_id)
        run = self.get_run(run_id)
        if claimed is None or run is None:
            return None
        return {"delivery": claimed, "run": run}

    def complete_run_with_message(
        self,
        *,
        workflow_id: str,
        delivery_id: str,
        run_id: str,
        live_link_id: str,
        invocation: dict[str, Any],
        message: dict[str, Any],
        workflow_state_from: str,
        workflow_state_to: str,
        workflow_event: dict[str, Any],
        artifact: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM messages WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            next_seq = int(seq_row["seq"]) + 1 if seq_row else 1
            conn.execute(
                """
                INSERT INTO driver_invocations (
                    id, agent_run_id, driver_id, driver_kind, model_identity, external_session_ref,
                    prompt_path, prompt_sha256, reply_path, reply_sha256, raw_output_path, raw_output_sha256,
                    exit_code, timeout_status, stderr_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invocation["id"],
                    run_id,
                    invocation["driver_id"],
                    invocation["driver_kind"],
                    invocation.get("model_identity"),
                    invocation["external_session_ref"],
                    invocation["prompt_path"],
                    invocation["prompt_sha256"],
                    invocation["reply_path"],
                    invocation["reply_sha256"],
                    invocation.get("raw_output_path"),
                    invocation.get("raw_output_sha256"),
                    invocation.get("exit_code"),
                    invocation.get("timeout_status", 0),
                    invocation.get("stderr_summary"),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO messages (
                    id, workflow_id, live_link_id, delivery_id, agent_run_id, seq,
                    type, body_json, references_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    workflow_id,
                    live_link_id,
                    delivery_id,
                    run_id,
                    next_seq,
                    message["type"],
                    json.dumps(message["body"], ensure_ascii=False),
                    json.dumps(message["references"], ensure_ascii=False),
                    now,
                ),
            )
            if artifact is not None:
                conn.execute(
                    """
                    INSERT INTO artifacts (
                        id, workflow_id, produced_by_link_id, schema_version, artifact_markdown,
                        artifact_path, artifact_sha256, referenced_message_ids_json, validation_status,
                        mirror_path, mirror_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact["id"],
                        workflow_id,
                        artifact["produced_by_link_id"],
                        artifact["schema_version"],
                        artifact["artifact_markdown"],
                        artifact["artifact_path"],
                        artifact["artifact_sha256"],
                        json.dumps(artifact["referenced_message_ids"], ensure_ascii=False),
                        artifact["validation_status"],
                        artifact.get("mirror_path"),
                        artifact.get("mirror_sha256"),
                        now,
                    ),
                )
            conn.execute(
                "UPDATE deliveries SET status = 'answered', answered_at = ? WHERE id = ?",
                (now, delivery_id),
            )
            conn.execute(
                "UPDATE agent_runs SET status = 'succeeded', finished_at = ? WHERE id = ?",
                (now, run_id),
            )
            conn.execute(
                """
                UPDATE handover_workflows
                SET status = ?, updated_at = ?, stop_reason = ?, final_artifact_id = COALESCE(?, final_artifact_id)
                WHERE id = ?
                """,
                (
                    workflow_state_to,
                    now,
                    workflow_event.get("stop_reason"),
                    artifact["id"] if artifact else None,
                    workflow_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO workflow_events (
                    id, workflow_id, event_type, previous_state, next_state,
                    caused_by_delivery_id, caused_by_agent_run_id, caused_by_message_id,
                    reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_event["id"],
                    workflow_id,
                    workflow_event["event_type"],
                    workflow_state_from,
                    workflow_state_to,
                    delivery_id,
                    run_id,
                    message["id"],
                    workflow_event.get("reason"),
                    now,
                ),
            )
            conn.execute("COMMIT")

    def fail_run(
        self,
        *,
        workflow_id: str,
        delivery_id: str,
        run_id: str,
        workflow_state_from: str,
        workflow_state_to: str,
        reason: str,
        terminal: bool,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE deliveries SET status = 'failed', error_text = ?, answered_at = ? WHERE id = ?",
                (reason, now, delivery_id),
            )
            conn.execute(
                "UPDATE agent_runs SET status = 'failed', error_text = ?, finished_at = ? WHERE id = ?",
                (reason, now, run_id),
            )
            next_status = workflow_state_to if terminal else workflow_state_from
            stop_reason = reason if terminal else None
            conn.execute(
                "UPDATE handover_workflows SET status = ?, stop_reason = COALESCE(?, stop_reason), updated_at = ? WHERE id = ?",
                (next_status, stop_reason, now, workflow_id),
            )
            conn.execute(
                """
                INSERT INTO workflow_events (
                    id, workflow_id, event_type, previous_state, next_state,
                    caused_by_delivery_id, caused_by_agent_run_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"evt-{delivery_id}",
                    workflow_id,
                    "run_failed",
                    workflow_state_from,
                    next_status,
                    delivery_id,
                    run_id,
                    reason,
                    now,
                ),
            )
            conn.execute("COMMIT")

    def update_workflow_metrics(self, workflow_id: str, metrics: dict[str, Any]) -> None:
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError(f"workflow not found: {workflow_id}")
        merged = dict(workflow.get("metrics_json") or {})
        merged.update(metrics)
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                "UPDATE handover_workflows SET metrics_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False), now, workflow_id),
            )

    def counts_for_workflow(self, workflow_id: str) -> dict[str, int]:
        with self.connect() as conn:
            wakes = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_runs WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            invalid = conn.execute(
                """
                SELECT live_link_id, COUNT(*) AS c
                FROM agent_runs
                WHERE workflow_id = ? AND status = 'failed' AND error_text LIKE 'invalid_output:%'
                GROUP BY live_link_id
                """,
                (workflow_id,),
            ).fetchall()
            source_answers = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM messages
                WHERE workflow_id = ?
                  AND type IN ('ANSWER', 'CANNOT_ANSWER', 'NEEDS_CLARIFICATION')
                """,
                (workflow_id,),
            ).fetchone()
        invalid_counts = {str(row["live_link_id"]): int(row["c"]) for row in invalid}
        return {
            "total_wakes": int(wakes["c"]) if wakes else 0,
            "source_turns": int(source_answers["c"]) if source_answers else 0,
            **{f"invalid:{key}": value for key, value in invalid_counts.items()},
        }

    def workflow_audit(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError(f"workflow not found: {workflow_id}")
        messages = self.list_workflow_messages(workflow_id)
        events = self.list_workflow_events(workflow_id)
        artifact = self.get_workflow_artifact(workflow_id)
        with self.connect() as conn:
            invocations = [
                self._row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT di.*
                    FROM driver_invocations di
                    JOIN agent_runs ar ON ar.id = di.agent_run_id
                    WHERE ar.workflow_id = ?
                    ORDER BY di.created_at ASC, di.id ASC
                    """,
                    (workflow_id,),
                ).fetchall()
            ]
            deliveries = [
                self._row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM deliveries WHERE workflow_id = ? ORDER BY created_at ASC, id ASC",
                    (workflow_id,),
                ).fetchall()
            ]
            runs = [
                self._row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM agent_runs WHERE workflow_id = ? ORDER BY created_at ASC, id ASC",
                    (workflow_id,),
                ).fetchall()
            ]
        issues: list[str] = []
        if any(delivery["status"] == "pending" for delivery in deliveries) and workflow["status"] in TERMINAL_WORKFLOW_STATES:
            issues.append("terminal workflow still has pending deliveries")
        if any(run["status"] == "running" for run in runs) and workflow["status"] in TERMINAL_WORKFLOW_STATES:
            issues.append("terminal workflow still has active runs")
        if artifact and workflow["final_artifact_id"] != artifact["id"]:
            issues.append("workflow final_artifact_id does not match latest artifact")
        if len(messages) != len(invocations):
            issues.append("message/invocation count mismatch")
        return {
            "workflow": workflow,
            "deliveries": deliveries,
            "runs": runs,
            "invocations": invocations,
            "messages": messages,
            "events": events,
            "artifact": artifact,
            "ok": not issues,
            "issues": issues,
        }

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        for key in (
            "metadata_json",
            "config_json",
            "metrics_json",
            "payload_json",
            "body_json",
            "references_json",
            "referenced_message_ids_json",
            "payload_json",
        ):
            if key in payload and isinstance(payload[key], str):
                payload[key] = json.loads(payload[key])
        if "response_json" in payload and isinstance(payload["response_json"], str) and payload["response_json"]:
            payload["response_json"] = json.loads(payload["response_json"])
        if "requires_ack" in payload:
            payload["requires_ack"] = bool(payload["requires_ack"])
        return payload
