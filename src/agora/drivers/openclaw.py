from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.drivers.base import Driver, DriverError, DriverReply, DriverTimeoutError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


OPENCLAW_FORBIDDEN_AGENTS = {"main"}  # per feedback_alfred_cli_poisons_telegram.md
DEFAULT_CONTAINER = "openclaw-stack-openclaw-gateway-1"


@dataclass(slots=True)
class OpenClawDriver(Driver):
    """Relays debate prompts to an OpenClaw agent running inside WSL Docker.

    Uses:
        wsl docker exec <container> node openclaw.mjs agent
            --agent <agent_id> --message "<msg>" --json --timeout 300
            [--session-id <uuid>]

    Session continuity is provided by the agent runtime: on first call we
    capture `result.meta.agentMeta.sessionId` and pass it on subsequent calls.

    NOTE: `--agent main` (Alfred) is REFUSED at the driver level because the
    CLI poisons Alfred's Telegram bridge session. See feedback memory.
    """

    id: str
    display_name: str
    agent: str                              # e.g. "daedalus", "turing", "socrates"
    container: str = DEFAULT_CONTAINER
    token_ceiling: int = 120_000
    timeout_s: int = 300
    sessions: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.kind = "openclaw"
        if self.agent in OPENCLAW_FORBIDDEN_AGENTS:
            raise DriverError(
                f"openclaw agent '{self.agent}' is forbidden via CLI "
                f"(poisons Telegram bridge session). See feedback_alfred_cli_poisons_telegram.md"
            )
        self._state_root = Path("C:/Users/chris/PROJECTS/agora/data/driver-state") / self.id
        self._sessions_dir = self._state_root / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_replies: dict[str, DriverReply] = {}
        self._rehydrate_sessions()

    def _rehydrate_sessions(self) -> None:
        for file_path in self._sessions_dir.glob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            session_id = payload.get("session_id")
            if isinstance(session_id, str) and session_id:
                self.sessions[file_path.stem] = session_id

    def _session_file(self, room_id: str) -> Path:
        return self._sessions_dir / f"{room_id}.json"

    def _persist_session(self, room_id: str, session_id: str) -> None:
        _atomic_write_json(
            self._session_file(room_id),
            {"session_id": session_id, "agent": self.agent, "created_at": _utc_now_iso(), "driver_kind": self.kind},
        )

    async def health_check(self) -> tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "wsl", "docker", "exec", self.container, "node", "-e", "process.exit(0)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=15)
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
                return False, "wsl docker exec timed out"
        except FileNotFoundError:
            return False, "wsl not available on PATH"
        except Exception as exc:  # noqa: BLE001
            return False, f"health check failed: {exc}"
        if proc.returncode != 0:
            return False, f"docker exec exit {proc.returncode}"
        return True, f"ok (agent={self.agent})"

    async def _run(self, message: str, resume_id: str | None = None) -> DriverReply:
        cmd = [
            "wsl", "docker", "exec", self.container,
            "node", "openclaw.mjs", "agent",
            "--agent", self.agent,
            "--message", message,
            "--json",
            "--timeout", str(self.timeout_s),
        ]
        if resume_id:
            cmd.extend(["--session-id", resume_id])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s + 30)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            raise DriverTimeoutError(f"openclaw agent '{self.agent}' timed out") from exc
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            raise

        raw_out = stdout.decode("utf-8", errors="replace")
        raw_err = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise DriverError(f"openclaw exit {proc.returncode}: {raw_err.strip() or raw_out.strip()}")

        try:
            payload = json.loads(raw_out)
        except json.JSONDecodeError as exc:
            raise DriverError(f"openclaw returned non-JSON: {exc.msg}") from exc

        content = self._extract_text(payload)
        session_id = self._extract_session_id(payload)
        return DriverReply(
            content=content or raw_err.strip() or "",
            raw_output=raw_out + ("\n" + raw_err if raw_err else ""),
            resume_id=session_id,
        )

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        reply = await self._run(system_frame, resume_id=None)
        session_id = reply.resume_id
        if not session_id:
            raise DriverError("openclaw did not return a session id")
        self.sessions[room_id] = session_id
        self._persist_session(room_id, session_id)
        if prime_reply:
            self._bootstrap_replies[room_id] = reply
        return session_id

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        if room_id in self._bootstrap_replies:
            return self._bootstrap_replies.pop(room_id)
        session_id = self.sessions.get(room_id)
        if not session_id:
            raise DriverError("session expired")
        return await self._run(user_message, resume_id=session_id)

    async def close_session(self, room_id: str) -> None:
        self.sessions.pop(room_id, None)
        self._bootstrap_replies.pop(room_id, None)
        try:
            self._session_file(room_id).unlink(missing_ok=True)
        except OSError:
            return

    async def has_session(self, room_id: str) -> bool:
        return room_id in self.sessions

    async def send(self, prompt: str) -> DriverReply:
        return await self._run(prompt, resume_id=None)

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        result = payload.get("result")
        if not isinstance(result, dict):
            return ""
        payloads = result.get("payloads")
        if isinstance(payloads, list):
            for item in payloads:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        # Fallback shapes sometimes used by older agent versions.
        for key in ("text", "content", "reply"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _extract_session_id(payload: dict[str, Any]) -> str | None:
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        meta = result.get("meta", {})
        if isinstance(meta, dict):
            agent_meta = meta.get("agentMeta", {})
            if isinstance(agent_meta, dict):
                sid = agent_meta.get("sessionId")
                if isinstance(sid, str) and sid:
                    return sid
            sid = meta.get("sessionId")
            if isinstance(sid, str) and sid:
                return sid
        return None
