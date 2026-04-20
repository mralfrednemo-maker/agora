from __future__ import annotations

import pytest

from agora.ops.parser import format_tool_result, parse_admin_reply


def test_parse_single_tool_call() -> None:
    reply = 'Sure, let me check.\n<tool name="now">\n{}\n</tool>\nDone.'
    result = parse_admin_reply(reply)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "now"
    assert result.tool_calls[0].args == {}
    assert "Sure, let me check." in result.cleaned_text
    assert "<tool" not in result.cleaned_text
    assert result.parse_errors == []


def test_parse_multiple_tools() -> None:
    reply = (
        '<tool name="list_debates">{}</tool>\n'
        '<tool name="tg_send">{"text": "hi"}</tool>'
    )
    result = parse_admin_reply(reply)
    assert [c.name for c in result.tool_calls] == ["list_debates", "tg_send"]
    assert result.tool_calls[1].args == {"text": "hi"}


def test_parse_malformed_json_emits_error_but_no_call() -> None:
    reply = '<tool name="tg_send">\n{not json}\n</tool>'
    result = parse_admin_reply(reply)
    assert result.tool_calls == []
    assert any("invalid JSON" in e for e in result.parse_errors)


def test_parse_rejects_non_object_json() -> None:
    reply = '<tool name="foo">[]</tool>'
    result = parse_admin_reply(reply)
    assert result.tool_calls == []
    assert result.parse_errors


def test_parse_plain_text_no_tools() -> None:
    result = parse_admin_reply("Hello Christo. How can I help?")
    assert result.tool_calls == []
    assert result.cleaned_text.startswith("Hello Christo")


def test_format_tool_result_with_dict() -> None:
    output = format_tool_result("now", {"iso": "2026-04-20T15:00:00Z"})
    assert output.startswith('<tool-result name="now">')
    assert "iso" in output
    assert output.endswith("</tool-result>")


def test_format_tool_result_with_string() -> None:
    output = format_tool_result("foo", "hello")
    assert "hello" in output


@pytest.mark.asyncio
async def test_registry_invoke_unknown_tool() -> None:
    from agora.ops.tools import ToolRegistry

    reg = ToolRegistry()
    result = await reg.invoke("ghost", {})
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


@pytest.mark.asyncio
async def test_registry_invoke_passes_error() -> None:
    from agora.ops.tools import ToolRegistry, ToolSpec

    async def boom(_args: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("kaboom")

    reg = ToolRegistry()
    reg.register(ToolSpec(name="boom", description="x", args_schema="{}", func=boom))
    result = await reg.invoke("boom", {})
    assert result["ok"] is False
    assert "kaboom" in result["error"]


@pytest.mark.asyncio
async def test_registry_invoke_ok_adds_ok_field() -> None:
    from agora.ops.tools import ToolRegistry, ToolSpec

    async def hello(_args: dict[str, object]) -> dict[str, object]:
        return {"greeting": "hi"}

    reg = ToolRegistry()
    reg.register(ToolSpec(name="hello", description="x", args_schema="{}", func=hello))
    result = await reg.invoke("hello", {})
    assert result["ok"] is True
    assert result["greeting"] == "hi"


def test_registry_listing_includes_names() -> None:
    from agora.ops.tools import NOW_SPEC, ToolRegistry

    reg = ToolRegistry()
    reg.register(NOW_SPEC)
    listing = reg.system_prompt_listing()
    assert "now" in listing
    assert "Get current UTC time" in listing
