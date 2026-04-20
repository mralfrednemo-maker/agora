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


@dataclass(slots=True)
class ClaudeCodeResumeDriver(Driver):
    """Driver that attaches to an *already-existing* Claude Code session on disk
    via `claude --resume <session-id>`. Use this when you want a debate
    participant that has the memory of a prior Claude Code conversation.

    Safety: only the gateway-owned process runs `claude --resume <id>` at a
    time. If the original terminal is actively prompting the same session,
    both processes would append to the same JSONL and race — we guard with a
    per-session file lock.
    """

    id: str
    display_name: str
    existing_session_id: str  # set at construction — the id to attach to
    token_ceiling: int = 180_000
    timeout_s: int = 300
    sessions: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.kind = "claude-code-resume"
        self._state_root = Path("C:/Users/chris/PROJECTS/agora/data/driver-state") / self.id
        self._sessions_dir = self._state_root / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_replies: dict[str, DriverReply] = {}

    def _room_cwd(self, room_id: str) -> Path:
        path = self._state_root / f"room-{room_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _session_file(self, room_id: str) -> Path:
        return self._sessions_dir / f"{room_id}.json"

    def _lock_file(self, session_id: str) -> Path:
        return self._state_root / f"session-{session_id}.lock"

    async def health_check(self) -> tuple[bool, str]:
        if not self.existing_session_id:
            return False, "existing_session_id is empty"
        # We can't easily probe a specific session exists without launching
        # `claude`. Delegate to the first real call.
        return True, "ok"

    async def _acquire_lock(self, session_id: str) -> Path:
        """Atomic file lock via O_CREAT|O_EXCL. Returns the lock path (caller
        must release via _release_lock). If the lock already exists AND is
        stale (> timeout_s old), it is reclaimed; fresh locks raise.
        """
        lock_path = self._lock_file(session_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(lock_path), flags)
        except FileExistsError:
            # Check staleness — may be a crashed prior run.
            try:
                age = datetime.now(timezone.utc).timestamp() - lock_path.stat().st_mtime
            except FileNotFoundError:
                # Race: lock vanished between our attempts. Retry once.
                fd = os.open(str(lock_path), flags)
            else:
                if age < self.timeout_s:
                    raise DriverError(
                        f"claude-code-resume: another process is using session "
                        f"{session_id} (lock age {age:.0f}s)"
                    )
                # Stale: reclaim atomically.
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                try:
                    fd = os.open(str(lock_path), flags)
                except FileExistsError as exc:
                    raise DriverError(
                        f"claude-code-resume: lock race on session {session_id}"
                    ) from exc
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return lock_path

    def _release_lock(self, lock_path: Path) -> None:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    async def _run_claude(self, prompt: str, room_id: str, session_id: str) -> DriverReply:
        cmd = [
            "claude",
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--resume",
            session_id,
        ]
        lock = await self._acquire_lock(session_id)
        try:
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
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
            except TimeoutError as exc:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
                raise DriverTimeoutError(f"Driver timed out after {self.timeout_s}s") from exc
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
        finally:
            self._release_lock(lock)

        output = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise DriverError(
                f"claude --resume exit {proc.returncode}: "
                f"{(err.strip() or output.strip())[:500]}"
            )
        content = self._extract_text(output) or err.strip() or ""
        # Resumed sessions echo their own id on the init line; capture for telemetry.
        resume_id = self._extract_session_id(output + "\n" + err) or session_id
        return DriverReply(content=content, raw_output=output + ("\n" + err if err else ""), resume_id=resume_id)

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        # A resume driver does NOT create a new session — it attaches to an
        # existing one. The system_frame is injected as the first user-turn
        # so the attached session sees the room's framing on its timeline.
        reply = await self._run_claude(system_frame, room_id=room_id, session_id=self.existing_session_id)
        self.sessions[room_id] = self.existing_session_id
        _atomic_write_json(
            self._session_file(room_id),
            {
                "session_id": self.existing_session_id,
                "attached_at": _utc_now_iso(),
                "driver_kind": self.kind,
            },
        )
        if prime_reply:
            self._bootstrap_replies[room_id] = reply
        return self.existing_session_id

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        if room_id in self._bootstrap_replies:
            return self._bootstrap_replies.pop(room_id)
        session_id = self.sessions.get(room_id) or self.existing_session_id
        if not session_id:
            raise DriverError("session expired")
        return await self._run_claude(user_message, room_id=room_id, session_id=session_id)

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
        return await self._run_claude(prompt, room_id="legacy", session_id=self.existing_session_id)

    @staticmethod
    def _extract_text(output: str) -> str:
        result_text = ""
        assistant_texts: list[str] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "result":
                r = obj.get("result")
                if isinstance(r, str) and r.strip():
                    result_text = r.strip()
            elif obj.get("type") == "assistant":
                msg = obj.get("message", {})
                content = msg.get("content", []) if isinstance(msg, dict) else []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text")
                        if isinstance(t, str) and t.strip():
                            assistant_texts.append(t.strip())
        return result_text or "\n".join(assistant_texts).strip()

    @staticmethod
    def _extract_session_id(text: str) -> str | None:
        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                value = obj.get("session_id") or obj.get("sessionId")
                if isinstance(value, str) and value:
                    return value
        return None
