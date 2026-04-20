from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agora.drivers.base import Driver
from agora.ops.config import (
    MAX_TOOL_CALLS_PER_TURN,
    OPS_ROOM_DIR,
    OPS_ROOM_ID,
)
from agora.ops.parser import format_tool_result, parse_admin_reply
from agora.ops.tools import NOW_SPEC, ToolRegistry

EventEmitter = Callable[[dict[str, object]], Awaitable[None]]

# Extra slack on top of the driver's own timeout_s. Keep > 5s so the driver's
# kill-and-wait path can run cleanly before the outer wait_for fires.
DRIVER_TIMEOUT_SLACK_S = 30


def _driver_timeout(driver: Driver) -> float:
    inner = int(getattr(driver, "timeout_s", 300) or 300)
    return float(inner + DRIVER_TIMEOUT_SLACK_S)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


SYSTEM_FRAME_TEMPLATE = """\
You are "Ops", Christo's chief of staff and personal AI assistant.

You help him:
- run and track multi-LLM debates in the Agora system
- send and receive messages on Telegram and WhatsApp (when those integrations are enabled)
- stay on top of inbound communications
- answer questions directly when tools are not needed

Tool use:
You have structured tools. To invoke one, emit a block EXACTLY in this format (nothing else wrapping it):

<tool name="tool_name">
{json args}
</tool>

You can emit multiple tool blocks in a single reply — they will be executed in order.
Do NOT invent tool names. Only use the ones listed below.

After a tool runs, the result comes back in the next user turn as:

<tool-result name="tool_name">
{result json}
</tool-result>

If a tool fails, the result will include "ok": false and an "error" field. Handle gracefully.

Style:
- Be concise. Prefer bullets for lists. No filler phrases.
- If you can answer Christo without tools, answer directly.
- If you need a tool, call it without asking permission first, unless the action is destructive
  (stopping a debate, sending an outbound message to a third party).

Available tools:
{tools_listing}
"""


@dataclass(slots=True)
class OpsMessage:
    seq: int
    ts: str
    role: str  # "user" | "admin" | "tool_result" | "system"
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class OpsManager:
    driver: Driver
    registry: ToolRegistry
    emit: EventEmitter
    transcript: list[OpsMessage] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _transcript_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _session_started: bool = field(default=False, init=False)
    _seq_counter: "itertools.count[int]" = field(default_factory=lambda: itertools.count(0), init=False)
    _pending_system: list[str] = field(default_factory=list, init=False)

    @classmethod
    def create(cls, driver: Driver, emit: EventEmitter) -> "OpsManager":
        registry = ToolRegistry()
        registry.register(NOW_SPEC)
        mgr = cls(driver=driver, registry=registry, emit=emit)
        mgr._load_transcript()
        mgr._ensure_room_file()
        return mgr

    # ---- Persistence ----------------------------------------------------

    @property
    def transcript_path(self) -> Path:
        OPS_ROOM_DIR.mkdir(parents=True, exist_ok=True)
        return OPS_ROOM_DIR / "transcript.jsonl"

    @property
    def room_file_path(self) -> Path:
        return OPS_ROOM_DIR / "room.json"

    def _ensure_room_file(self) -> None:
        """Write the singleton ops room file so on-disk readers see it."""
        OPS_ROOM_DIR.mkdir(parents=True, exist_ok=True)
        path = self.room_file_path
        if path.exists():
            return
        payload = {
            "id": OPS_ROOM_ID,
            "kind": "ops",
            "status": "idle",
            "participants": [self.driver.id],
            "created_at": _utc_now_iso(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_transcript(self) -> None:
        path = self.transcript_path
        if not path.exists():
            return
        entries: list[OpsMessage] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(
                OpsMessage(
                    seq=int(obj.get("seq", len(entries))),
                    ts=str(obj.get("ts", _utc_now_iso())),
                    role=str(obj.get("role", "system")),
                    content=str(obj.get("content", "")),
                    tool_calls=list(obj.get("tool_calls", [])),
                )
            )
        self.transcript = entries
        # Resume the sequence counter from the highest persisted seq + 1.
        if entries:
            start = max(e.seq for e in entries) + 1
            self._seq_counter = itertools.count(start)
        # Rehydrate pending system events: any system messages that appear after
        # the last user turn were queued for the admin's next turn and haven't
        # been delivered yet. Survives gateway restarts.
        last_user_idx = -1
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].role == "user":
                last_user_idx = i
                break
        for entry in entries[last_user_idx + 1:]:
            if entry.role == "system" and entry.content:
                self._pending_system.append(entry.content)

    def _persist_message_sync(self, message: OpsMessage) -> None:
        path = self.transcript_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(
                    {
                        "seq": message.seq,
                        "ts": message.ts,
                        "role": message.role,
                        "content": message.content,
                        "tool_calls": message.tool_calls,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    async def _append(self, role: str, content: str, tool_calls: list[dict[str, Any]] | None = None) -> OpsMessage:
        async with self._transcript_lock:
            msg = OpsMessage(
                seq=next(self._seq_counter),
                ts=_utc_now_iso(),
                role=role,
                content=content,
                tool_calls=tool_calls or [],
            )
            self.transcript.append(msg)
            await asyncio.to_thread(self._persist_message_sync, msg)
        await self.emit(
            {
                "type": "ops.message",
                "message": {
                    "seq": msg.seq,
                    "ts": msg.ts,
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                },
            }
        )
        return msg

    # ---- Session lifecycle ---------------------------------------------

    async def _ensure_session(self) -> None:
        if self._session_started:
            return
        if await self.driver.has_session(OPS_ROOM_ID):
            self._session_started = True
            return
        frame = SYSTEM_FRAME_TEMPLATE.format(tools_listing=self.registry.system_prompt_listing())
        # prime_reply=False: discard the frame's response so user's first real
        # message produces a direct reply, not a frame-acknowledgement.
        await self.driver.start_session(OPS_ROOM_ID, frame, prime_reply=False)
        self._session_started = True

    # ---- Main loop ------------------------------------------------------

    async def handle_user_text(self, text: str) -> OpsMessage:
        async with self._lock:
            await self._ensure_session()

            # Prepend any pending system events so admin sees inbound TG/WA.
            prefix = self._drain_pending_system()
            user_message = f"{prefix}\n\n{text}" if prefix else text
            await self._append("user", user_message)

            return await self._run_loop(user_message)

    async def _run_loop(self, initial_input: str) -> OpsMessage:
        remaining = MAX_TOOL_CALLS_PER_TURN
        current_input = initial_input
        last_admin: OpsMessage | None = None

        while True:
            outer_timeout = _driver_timeout(self.driver)
            try:
                reply = await asyncio.wait_for(
                    self.driver.send_in_session(OPS_ROOM_ID, current_input),
                    timeout=outer_timeout,
                )
            except asyncio.TimeoutError:
                return await self._append("system", f"driver timed out after {outer_timeout:.0f}s")
            except Exception as exc:  # noqa: BLE001
                return await self._append("system", f"driver error: {type(exc).__name__}: {exc}")

            parsed = parse_admin_reply(reply.content)
            admin_msg = await self._append(
                "admin",
                parsed.cleaned_text or reply.content,
                tool_calls=[{"name": c.name, "args": c.args} for c in parsed.tool_calls],
            )
            last_admin = admin_msg

            # Feed parse errors back so admin can self-correct.
            if parsed.parse_errors:
                err_payload = {"ok": False, "error": "; ".join(parsed.parse_errors)}
                current_input = format_tool_result("__parse_error__", err_payload)
                await self._append("system", f"parse errors: {err_payload['error']}")
                remaining -= 1
                if remaining <= 0:
                    return await self._append(
                        "system",
                        f"hit MAX_TOOL_CALLS_PER_TURN={MAX_TOOL_CALLS_PER_TURN}; stopping loop",
                    )
                continue

            if not parsed.tool_calls:
                return admin_msg

            # Execute tool calls in order; honor the per-turn cap across all calls.
            exec_count = min(len(parsed.tool_calls), remaining)
            if exec_count < len(parsed.tool_calls):
                await self._append(
                    "system",
                    (
                        f"admin emitted {len(parsed.tool_calls)} tool calls but only "
                        f"{exec_count} remaining in this turn; the overflow was skipped"
                    ),
                )

            results_text: list[str] = []
            for call in parsed.tool_calls[:exec_count]:
                result = await self.registry.invoke(call.name, call.args)
                results_text.append(format_tool_result(call.name, result))
                await self._append(
                    "tool_result",
                    _json_or_text(result),
                    tool_calls=[{"name": call.name, "args": call.args, "result": result}],
                )
                remaining -= 1

            if remaining <= 0 or exec_count < len(parsed.tool_calls):
                return last_admin or await self._append(
                    "system",
                    f"hit MAX_TOOL_CALLS_PER_TURN={MAX_TOOL_CALLS_PER_TURN}; stopping loop",
                )
            current_input = "\n\n".join(results_text)

    # ---- System events (webhooks) ---------------------------------------

    def _drain_pending_system(self) -> str:
        if not self._pending_system:
            return ""
        lines = "\n".join(f"[SYSTEM] {e}" for e in self._pending_system)
        self._pending_system.clear()
        return f"The following system events have arrived since your last turn:\n{lines}"

    async def deliver_system_event(self, text: str) -> None:
        """Push a system event into the ops transcript without prompting admin.
        Queues the event so it is injected into the admin's next user turn.
        """
        self._pending_system.append(text)
        await self._append("system", text)

    async def snapshot(self) -> dict[str, Any]:
        return {
            "id": OPS_ROOM_ID,
            "driver_id": self.driver.id,
            "transcript": [
                {
                    "seq": m.seq,
                    "ts": m.ts,
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": m.tool_calls,
                }
                for m in self.transcript
            ],
        }


def _json_or_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        return str(value)
