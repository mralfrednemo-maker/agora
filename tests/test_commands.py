from __future__ import annotations

import pytest

from agora.commands.parser import CommandParseError, parse_command


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("/new topic", "new"),
        ("/participants a b", "participants"),
        ("/rounds 10", "rounds"),
        ("/rounds +2", "rounds"),
        ("/start", "start"),
        ("/pause", "pause"),
        ("/resume", "resume"),
        ("/stop", "stop"),
        ("/inject text", "inject"),
        ("/to p1 hello", "to"),
        ("/phase verdict", "phase"),
        ("/synthesize", "synthesize"),
        ("/synthesize haiku", "synthesize"),
        ("/list", "list"),
        ("/attach room1", "attach"),
        ("/drivers", "drivers"),
    ],
)
def test_parse_commands(text: str, name: str) -> None:
    assert parse_command(text).name == name


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("/new hello world", ("hello world",)),
        ("/participants a b c", ("a", "b", "c")),
        ("/rounds +4", ("+4",)),
        ("/rounds 7", ("7",)),
        ("/inject   keep it strict", ("keep it strict",)),
        ("/to p9 say hi", ("p9", "say hi")),
        ("/phase debate", ("debate",)),
        ("/attach abc-123", ("abc-123",)),
    ],
)
def test_parse_command_args(text: str, args: tuple[str, ...]) -> None:
    assert parse_command(text).args == args


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "/participants",
        "/phase",
        "/rounds",
        "/unknown command",
    ],
)
def test_parse_invalid(text: str) -> None:
    with pytest.raises(CommandParseError):
        parse_command(text)
