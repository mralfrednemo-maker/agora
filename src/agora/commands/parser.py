from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

CommandName = Literal[
    "new",
    "participants",
    "rounds",
    "start",
    "pause",
    "resume",
    "stop",
    "inject",
    "to",
    "phase",
    "synthesize",
    "list",
    "attach",
    "drivers",
]


@dataclass(slots=True)
class ParsedCommand:
    name: CommandName
    args: tuple[str, ...]


class CommandParseError(ValueError):
    pass


def parse_command(text: str) -> ParsedCommand:
    raw = text.strip()
    if not raw.startswith("/"):
        raise CommandParseError("Commands must start with '/'")

    if match := re.fullmatch(r"/new\s+(.+)", raw):
        return ParsedCommand("new", (match.group(1).strip(),))
    if match := re.fullmatch(r"/participants\s+(.+)", raw):
        ids = tuple(part for part in match.group(1).split() if part)
        if not ids:
            raise CommandParseError("/participants requires at least one id")
        return ParsedCommand("participants", ids)
    if match := re.fullmatch(r"/rounds\s+([+]?)\s*(\d+)", raw):
        prefix, number = match.groups()
        return ParsedCommand("rounds", (prefix + number,))
    if raw == "/start":
        return ParsedCommand("start", ())
    if raw == "/pause":
        return ParsedCommand("pause", ())
    if raw == "/resume":
        return ParsedCommand("resume", ())
    if raw == "/stop":
        return ParsedCommand("stop", ())
    if match := re.fullmatch(r"/inject\s+(.+)", raw):
        return ParsedCommand("inject", (match.group(1),))
    if match := re.fullmatch(r"/to\s+([\w\-]+)\s+(.+)", raw):
        return ParsedCommand("to", (match.group(1), match.group(2)))
    if match := re.fullmatch(r"/phase\s+([\w\-]+)", raw):
        return ParsedCommand("phase", (match.group(1),))
    if match := re.fullmatch(r"/synthesize(?:\s+(.+))?", raw):
        model = (match.group(1) or "").strip()
        return ParsedCommand("synthesize", (model,) if model else ())
    if raw == "/list":
        return ParsedCommand("list", ())
    if match := re.fullmatch(r"/attach\s+([\w\-]+)", raw):
        return ParsedCommand("attach", (match.group(1),))
    if raw == "/drivers":
        return ParsedCommand("drivers", ())

    raise CommandParseError(f"Unrecognized command: {raw}")
