from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.drivers.base import Driver, DriverError, DriverReply, DriverTimeoutError


SESSION_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


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
class GeminiCliDriver(Driver):
    id: str
    display_name: str
    model: str = "gemini-3-flash-preview"
    token_ceiling: int = 900_000
    sessions: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.kind = "gemini-cli"
        self._cmd_path: str | None = None
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

    def _room_cwd(self, room_id: str) -> Path:
        path = self._state_root / f"room-{room_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist_session(self, room_id: str, session_id: str) -> None:
        _atomic_write_json(
            self._session_file(room_id),
            {
                "session_id": session_id,
                "created_at": _utc_now_iso(),
                "driver_kind": self.kind,
            },
        )

    async def health_check(self) -> tuple[bool, str]:
        self._cmd_path = shutil.which("gemini.cmd") or shutil.which("gemini")
        if not self._cmd_path:
            return False, "gemini CLI not found (looked for gemini.cmd and gemini)"
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cmd_path,
                "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except (FileNotFoundError, NotImplementedError, PermissionError, OSError) as exc:
            return False, f"gemini CLI spawn failed: {exc}"
        return True, "ok"

    async def _run(self, prompt: str, resume_id: str | None = None, room_id: str = "legacy") -> DriverReply:
        if self._cmd_path is None:
            ok, msg = await self.health_check()
            if not ok:
                raise DriverError(msg)
        cmd = [self._cmd_path or "gemini.cmd", "--yolo", "--model", self.model]
        if resume_id:
            cmd.extend(["--resume", resume_id])
        cmd.extend(["--prompt", ""])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._room_cwd(room_id)),
        )
        assert proc.stdin is not None
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
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
            raise DriverTimeoutError("Gemini CLI driver timed out after 300s")
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
        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        raw = out + ("\n" + err if err else "")
        content = out.strip() or err.strip()
        if proc.returncode != 0 or content.startswith("Error resuming session:"):
            raise DriverError(f"gemini CLI exit {proc.returncode}: {content[:500]}")
        resume = self._extract_session_uuid_from_chat_store(self._room_cwd(room_id)) or self._extract_session_uuid(raw) or self._extract_session_uuid_from_logs()
        return DriverReply(content=content, raw_output=raw, resume_id=resume)

    def _extract_session_uuid(self, text: str) -> str | None:
        match = SESSION_UUID_RE.search(text)
        return match.group(0) if match else None

    def _extract_session_uuid_from_logs(self) -> str | None:
        candidates: list[Path] = []
        home = Path.home()
        for base in (home / ".gemini", home / "AppData" / "Roaming" / "gemini"):
            if not base.exists():
                continue
            candidates.extend(base.rglob("*.log"))
            candidates.extend(base.rglob("*.json"))
        if not candidates:
            return None
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in candidates[:15]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            match = SESSION_UUID_RE.search(text)
            if match:
                return match.group(0)
        return None

    def _extract_session_uuid_from_chat_store(self, cwd: Path) -> str | None:
        workspace_name = Path(os.path.abspath(cwd)).name
        candidates = [workspace_name, workspace_name.replace("_", "-")]
        session_files: list[Path] = []
        for candidate in dict.fromkeys(candidates):
            chat_dir = Path.home() / ".gemini" / "tmp" / candidate / "chats"
            if chat_dir.exists():
                session_files.extend(chat_dir.glob("session-*.jsonl"))
        if not session_files:
            return None
        session_files = sorted(session_files, key=lambda path: path.stat().st_mtime, reverse=True)
        for path in session_files[:10]:
            try:
                first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                payload = json.loads(first_line)
            except Exception:
                continue
            session_id = payload.get("sessionId")
            if isinstance(session_id, str) and session_id:
                return session_id
        return None

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        reply = await self._run(system_frame, room_id=room_id)
        session_id = reply.resume_id
        if not session_id:
            raise DriverError("session expired")
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
        return await self._run(user_message, resume_id=session_id, room_id=room_id)

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
        return await self._run(prompt)
