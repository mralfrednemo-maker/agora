from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.drivers.base import Driver, DriverError, DriverReply, DriverTimeoutError


CODEX_COMPANION = "C:/Users/chris/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs"
CODEX_LIB = "C:/Users/chris/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/lib/codex.mjs"


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


@dataclass(slots=True)
class CodexDriver(Driver):
    id: str
    display_name: str
    token_ceiling: int = 180_000
    model: str = "gpt-5.4"
    effort: str | None = None
    sessions: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.kind = "codex"
        self._state_root = Path("C:/Users/chris/PROJECTS/agora/data/driver-state") / self.id
        self._sessions_dir = self._state_root / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_replies: dict[str, DriverReply] = {}
        self._rehydrate_sessions()

    def _rehydrate_sessions(self) -> None:
        if not self._sessions_dir.exists():
            return
        for file_path in self._sessions_dir.glob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            room_id = file_path.stem
            session_id = payload.get("session_id")
            if isinstance(session_id, str) and session_id:
                self.sessions[room_id] = session_id

    def _session_file(self, room_id: str) -> Path:
        return self._sessions_dir / f"{room_id}.json"

    def _persist_session(self, room_id: str, session_id: str) -> None:
        _atomic_write_json(
            self._session_file(room_id),
            {
                "session_id": session_id,
                "created_at": _utc_now_iso(),
                "driver_kind": self.kind,
            },
        )

    async def _run_codex(self, prompt: str, resume_id: str | None = None, thread_name: str | None = None) -> DriverReply:
        script_path: Path | None = None
        payload = {
            "cwd": "C:/Users/chris/PROJECTS/agora",
            "prompt": prompt,
            "resumeThreadId": resume_id,
            "model": self.model,
            "effort": self.effort,
            "threadName": thread_name,
        }
        script = f"""
import {{ runAppServerTurn }} from {json.dumps(Path(CODEX_LIB).as_uri())};

const payload = {json.dumps(payload)};
const result = await runAppServerTurn(payload.cwd, {{
  resumeThreadId: payload.resumeThreadId || null,
  prompt: payload.prompt,
  defaultPrompt: payload.resumeThreadId ? "Continue from the current thread state." : "",
  model: payload.model,
  effort: payload.effort,
  sandbox: "read-only",
  persistThread: true,
  threadName: payload.resumeThreadId ? null : payload.threadName,
}});
process.stdout.write(JSON.stringify(result));
"""
        fd, temp_name = tempfile.mkstemp(suffix=".mjs")
        os.close(fd)
        script_path = Path(temp_name)
        script_path.write_text(script, encoding="utf-8")
        cmd = ["node", str(script_path)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="C:/Users/chris/PROJECTS/agora",
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            raise DriverTimeoutError("Codex driver timed out after 300s")
        except asyncio.CancelledError:
            # Propagate cancellation but make sure the subprocess is killed,
            # otherwise a timed-out task leaves codex running in the background.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            raise
        finally:
            if script_path is not None:
                try:
                    script_path.unlink(missing_ok=True)
                except OSError:
                    pass
        raw = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        combined = raw + ("\n" + err if err else "")
        if proc.returncode != 0:
            raise DriverError(f"codex app-server exit {proc.returncode}: {(err.strip() or raw.strip())[:500]}")
        content, thread_id = self._extract_result(raw)
        if not content:
            content = "[codex-extractor-fallback] Unable to parse final assistant reply."
        return DriverReply(content=content, raw_output=combined, resume_id=thread_id)

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        reply = await self._run_codex(system_frame, resume_id=None, thread_name=f"Agora Room {room_id}")
        session_id = reply.resume_id
        if not session_id:
            raise DriverError("codex session id missing from startup output")
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
        reply = await self._run_codex(user_message, resume_id=session_id)
        if reply.resume_id and reply.resume_id != session_id:
            self.sessions[room_id] = reply.resume_id
            self._persist_session(room_id, reply.resume_id)
        return reply

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
        return await self._run_codex(prompt, resume_id=None)

    def _extract_resume_id(self, text: str) -> str | None:
        match = re.search(r'"threadId"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_result(self, raw: str) -> tuple[str, str | None]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip(), self._extract_resume_id(raw)
        if not isinstance(payload, dict):
            return raw.strip(), None
        content = str(payload.get("finalMessage") or "").strip()
        thread_id = payload.get("threadId")
        return content, thread_id if isinstance(thread_id, str) and thread_id else None
