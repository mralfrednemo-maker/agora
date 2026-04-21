from __future__ import annotations

import asyncio
import json
import os
import re
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
class ClaudeCodeNewDriver(Driver):
    id: str
    display_name: str
    token_ceiling: int = 180_000
    timeout_s: int = 300
    model: str | None = None  # None = Claude Code's own default; set via --model flag
    sessions: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.kind = "claude-code-new"
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

    def _router_command(self, session_id: str | None = None) -> str:
        parts = [
            "& 'C:\\Users\\chris\\PROJECTS\\scripts\\claude-code-router.ps1'",
            "-Provider minimax",
        ]
        if self.model:
            parts.append(f"--model {self.model}")
        parts.extend(
            [
                "--dangerously-skip-permissions",
                "--print --verbose --output-format stream-json",
            ]
        )
        if session_id:
            parts.append(f"--resume {session_id}")
        return " ".join(parts)

    async def _run_claude(self, prompt: str, room_id: str, session_id: str | None = None) -> DriverReply:
        cmd = [
            "powershell.exe",
            "-ExecutionPolicy", "Bypass",
            "-NoProfile",
            "-Command",
            self._router_command(session_id=session_id),
        ]
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
            # Kill the orphaned subprocess so it doesn't linger consuming resources.
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
        output = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise DriverError(
                f"claude --print exit {proc.returncode}: "
                f"{(err.strip() or output.strip())[:500]}"
            )
        content = self._extract_text(output)
        if not content:
            content = err.strip() or ""
        resume_id = self._extract_session_id(output + "\n" + err)
        return DriverReply(content=content, raw_output=output + ("\n" + err if err else ""), resume_id=resume_id)

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        # Send the system frame and capture session_id.
        # If prime_reply is True (default, debate use-case), the frame's response
        # is cached and returned on the next send_in_session call — the engine
        # treats the frame as the Phase 1 prompt so the frame's reply IS the
        # first turn. If False (ops use-case), the reply is discarded so the
        # user's first real message produces a fresh response.
        reply = await self._run_claude(system_frame, room_id=room_id, session_id=None)
        session_id = reply.resume_id
        if not session_id:
            raise DriverError("claude session id missing from startup output")
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
        return await self._run_claude(prompt, room_id="legacy", session_id=None)

    def _extract_session_id(self, text: str) -> str | None:
        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                for key in ("session_id", "sessionId"):
                    value = obj.get(key)
                    if isinstance(value, str) and value:
                        return value
                if isinstance(obj.get("session"), dict):
                    value = obj["session"].get("id")
                    if isinstance(value, str) and value:
                        return value
        match = re.search(r'"session_id"\s*:\s*"([^"]+)"', text)
        if match:
            return match.group(1)
        return None

    def _extract_text(self, output: str) -> str:
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
                result = obj.get("result")
                if isinstance(result, str) and result.strip():
                    result_text = result.strip()
            elif obj.get("type") == "assistant":
                message = obj.get("message", {})
                content = message.get("content", []) if isinstance(message, dict) else []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        value = block.get("text")
                        if isinstance(value, str) and value.strip():
                            assistant_texts.append(value.strip())
        if result_text:
            return result_text
        return "\n".join(assistant_texts).strip()
