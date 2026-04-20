from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.drivers.base import Driver, DriverError, DriverReply


CODEX_COMPANION = "C:/Users/chris/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs"


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

    async def _run_codex(self, prompt: str, resume_id: str | None = None) -> DriverReply:
        flags = ["--read-only"]
        if resume_id is None:
            flags.append("--fresh")
        else:
            flags.extend(["--resume", resume_id])

        cmd = ["node", CODEX_COMPANION, "task", "--model", self.model, *flags, prompt]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="C:/Users/chris/PROJECTS/agora",
        )
        try:
            stdout, stderr = await proc.communicate()
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
        raw = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        combined = raw + ("\n" + err if err else "")
        new_resume_id = self._extract_resume_id(combined)
        content = self._extract_reply(raw)
        if not content:
            content = "[codex-extractor-fallback] Unable to parse final assistant reply."
        return DriverReply(content=content, raw_output=combined, resume_id=new_resume_id)

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        reply = await self._run_codex(system_frame, resume_id=None)
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
        patterns = [r'"resume_id"\s*:\s*"([^"]+)"', r'--resume\s+([\w-]+)', r'resume id[:\s]+([\w-]+)']
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_reply(self, raw: str) -> str:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        json_texts: list[str] = []
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                candidate = obj.get("assistant") or obj.get("text") or obj.get("content")
                if isinstance(candidate, str) and candidate.strip():
                    json_texts.append(candidate.strip())
        if json_texts:
            return json_texts[-1]
        if lines:
            return lines[-1]
        return ""
