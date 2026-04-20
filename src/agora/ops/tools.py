from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

ToolFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    args_schema: str  # human-readable, shown to admin in its system prompt
    func: ToolFn


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def system_prompt_listing(self) -> str:
        if not self._tools:
            return "(no tools available)"
        lines: list[str] = []
        for spec in self._tools.values():
            lines.append(f"- {spec.name} — {spec.description}")
            lines.append(f"    args: {spec.args_schema}")
        return "\n".join(lines)

    async def invoke(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self.get(name)
        if not spec:
            return {"ok": False, "error": f"unknown tool '{name}'"}
        try:
            result = await spec.func(args)
            # Ensure the result is a dict and has an 'ok' field.
            if not isinstance(result, dict):
                return {"ok": True, "data": result}
            if "ok" not in result:
                result = {"ok": True, **result}
            return result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---- Stateless tools ----------------------------------------------------

async def tool_now(args: dict[str, Any]) -> dict[str, Any]:
    return {"iso": _utc_now_iso()}


NOW_SPEC = ToolSpec(
    name="now",
    description="Get current UTC time in ISO format",
    args_schema="{}",
    func=tool_now,
)
