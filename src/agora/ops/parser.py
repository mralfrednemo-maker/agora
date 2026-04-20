from __future__ import annotations

import json
import re
from dataclasses import dataclass


TOOL_BLOCK_RE = re.compile(
    r'<tool\s+name="(?P<name>[a-zA-Z0-9_\-]+)"\s*>(?P<body>.*?)</tool>',
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: dict


@dataclass(frozen=True, slots=True)
class ParseResult:
    cleaned_text: str        # reply with <tool> blocks removed for clean display
    tool_calls: list[ToolCall]
    parse_errors: list[str]  # non-fatal — reported back to the admin


def parse_admin_reply(text: str) -> ParseResult:
    """Extract <tool name="x">{json}</tool> blocks from an admin reply."""
    calls: list[ToolCall] = []
    errors: list[str] = []
    for match in TOOL_BLOCK_RE.finditer(text):
        name = match.group("name")
        body = match.group("body").strip()
        try:
            args = json.loads(body) if body else {}
            if not isinstance(args, dict):
                errors.append(f"tool '{name}' args must be a JSON object, got {type(args).__name__}")
                continue
            calls.append(ToolCall(name=name, args=args))
        except json.JSONDecodeError as exc:
            errors.append(f"tool '{name}' has invalid JSON: {exc.msg} (at char {exc.pos})")
    cleaned = TOOL_BLOCK_RE.sub("", text).strip()
    return ParseResult(cleaned_text=cleaned, tool_calls=calls, parse_errors=errors)


def format_tool_result(name: str, payload: dict | str) -> str:
    """Render a tool result as a message the admin sees on its next turn."""
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    return f'<tool-result name="{name}">\n{body}\n</tool-result>'
