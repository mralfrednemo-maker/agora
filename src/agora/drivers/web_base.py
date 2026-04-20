from __future__ import annotations

import asyncio
import json
import os
from abc import abstractmethod
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


PYTHON_EXE = "python.exe"


@dataclass(slots=True)
class WebBrowserDriver(Driver):
    """Shared base for web drivers that shell out to the existing
    the-thinker/browser-automation Selenium scripts. Each script attaches to
    the user's already-running Chrome (uc-mode, logged-in profile) so no new
    browser window opens — the automation drives whatever tab is configured
    by the script.

    Subclasses define `script_path` and optionally override `_build_args`.
    Each `send_in_session` call invokes the script with `--prompt <text>`;
    first call includes `--new` so a fresh conversation thread is started for
    the room. The scripts manage thread continuity on the browser side.
    """

    id: str
    display_name: str
    script_path: str                         # absolute path to the browser script
    token_ceiling: int = 180_000
    timeout_s: int = 900                      # browser work can be slow
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    sessions: dict[str, str] = field(default_factory=dict, init=False)

    @property
    @abstractmethod
    def _kind_tag(self) -> str:
        """Short kind tag used for directory naming and driver.kind."""

    def __post_init__(self) -> None:
        self.kind = self._kind_tag
        self._state_root = Path("C:/Users/chris/PROJECTS/agora/data/driver-state") / self.id
        self._sessions_dir = self._state_root / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_replies: dict[str, DriverReply] = {}
        self._rehydrate_sessions()

    def _rehydrate_sessions(self) -> None:
        """Intentionally a no-op. Browser-side thread state is not durable —
        after a gateway restart the underlying Chrome tab may have been closed
        or navigated. Forcing a fresh `start_session(new_thread=True)` keeps
        the engine and browser threads in lockstep.
        """
        return

    def _session_file(self, room_id: str) -> Path:
        return self._sessions_dir / f"{room_id}.json"

    def _persist_session(self, room_id: str) -> None:
        _atomic_write_json(
            self._session_file(room_id),
            {"started": _utc_now_iso(), "driver_kind": self.kind},
        )

    async def health_check(self) -> tuple[bool, str]:
        path = Path(self.script_path)
        if not path.exists():
            return False, f"browser automation script not found: {self.script_path}"
        return True, "ok"

    def _build_args(self, prompt: str, new_thread: bool) -> list[str]:
        args = [PYTHON_EXE, self.script_path, "--prompt", prompt]
        if new_thread:
            args.append("--new")
        args.extend(self.extra_args)
        return args

    async def _run(self, prompt: str, new_thread: bool) -> DriverReply:
        cmd = self._build_args(prompt, new_thread=new_thread)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            raise DriverTimeoutError(f"{self.kind} timed out after {self.timeout_s}s") from exc
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

        # Detect browser_safety.py rate-limit markers and treat them as fatal
        # for the debate. The global rule ("NEVER retry a failed browser
        # automation more than once") is enforced here — we raise DriverError
        # with a dedicated prefix so the engine can stop the room.
        rl_markers = ("[SAFETY] RATE LIMIT", "[SAFETY] LOCKED", "[SAFETY] QUOTA")
        combined = raw_out + "\n" + raw_err
        if any(marker in combined for marker in rl_markers):
            raise DriverError(
                f"{self.kind} rate-limited (browser_safety.py): "
                f"{(raw_err.strip() or raw_out.strip())[:400]}"
            )

        if proc.returncode != 0:
            raise DriverError(
                f"{self.kind} browser automation exit {proc.returncode}: "
                f"{(raw_err.strip() or raw_out.strip())[:500]}"
            )

        content = raw_out.strip()
        if not content:
            raise DriverError(f"{self.kind} returned empty reply (stderr: {raw_err.strip()[:200]})")
        return DriverReply(
            content=content,
            raw_output=raw_out + ("\n" + raw_err if raw_err else ""),
            resume_id=None,
        )

    async def start_session(self, room_id: str, system_frame: str, prime_reply: bool = True) -> str:
        reply = await self._run(system_frame, new_thread=True)
        self.sessions[room_id] = _utc_now_iso()
        self._persist_session(room_id)
        if prime_reply:
            self._bootstrap_replies[room_id] = reply
        return self.sessions[room_id]

    async def send_in_session(self, room_id: str, user_message: str) -> DriverReply:
        if room_id in self._bootstrap_replies:
            return self._bootstrap_replies.pop(room_id)
        if room_id not in self.sessions:
            raise DriverError("session expired")
        return await self._run(user_message, new_thread=False)

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
        return await self._run(prompt, new_thread=False)
