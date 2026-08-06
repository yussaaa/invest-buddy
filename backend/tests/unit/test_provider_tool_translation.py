"""Unit tests for OpenAI ↔ Anthropic tool-call translation — no network, no key.

The registry emits OpenAI function schemas, so that shape is canonical and the
Anthropic path is a translation. Translation is where this gets subtly wrong in
ways that only show up mid-conversation — a tool result attached to the wrong
role, parallel calls split across turns, a reply whose first block is not text.
Those are exactly the cases below.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.provider import (
    ToolCall,
    parse_anthropic_content,
    parse_openai_tool_calls,
    to_anthropic_messages,
    to_anthropic_tool_choice,
    to_anthropic_tools,
)


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(id: str, name: str, input: dict):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def openai_call(id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


# ── Parsing an Anthropic reply ──────────────────────────────────────────────


def test_a_plain_text_reply_parses():
    text, calls = parse_anthropic_content([text_block("RSI is oversold.")])

    assert text == "RSI is oversold."
    assert calls == []


def test_every_text_block_is_concatenated_not_just_the_first():
    text, _ = parse_anthropic_content([text_block("One. "), text_block("Two.")])

    assert text == "One. Two."


def test_a_tool_use_block_does_not_raise_on_a_missing_text_attribute():
    """The bug this replaced: content[0].text when content[0] is a ToolUseBlock."""
    text, calls = parse_anthropic_content([
        tool_use_block("tu_1", "compute_rsi", {"ticker": "AAPL"})
    ])

    assert text == ""
    assert calls == [ToolCall(id="tu_1", name="compute_rsi", arguments={"ticker": "AAPL"})]


def test_text_before_a_tool_use_block_is_preserved():
    text, calls = parse_anthropic_content([
        text_block("Let me check. "),
        tool_use_block("tu_1", "compute_rsi", {"ticker": "AAPL"}),
    ])

    assert text == "Let me check. "
    assert len(calls) == 1


def test_parallel_tool_use_blocks_all_come_through():
    _, calls = parse_anthropic_content([
        tool_use_block("tu_1", "compute_rsi", {"ticker": "AAPL"}),
        tool_use_block("tu_2", "get_recent_news", {"ticker": "AAPL"}),
    ])

    assert [c.name for c in calls] == ["compute_rsi", "get_recent_news"]


def test_unknown_block_types_are_skipped_rather_than_crashing():
    """Anthropic adds block types over time; an unfamiliar one is not fatal."""
    text, calls = parse_anthropic_content([
        SimpleNamespace(type="thinking", thinking="..."),
        text_block("Done."),
    ])

    assert text == "Done."
    assert calls == []


def test_empty_content_is_handled():
    assert parse_anthropic_content([]) == ("", [])
    assert parse_anthropic_content(None) == ("", [])


# ── Parsing an OpenAI reply ─────────────────────────────────────────────────


def test_openai_tool_calls_parse_with_json_arguments():
    message = SimpleNamespace(
        tool_calls=[openai_call("call_1", "compute_rsi", '{"ticker": "AAPL"}')]
    )

    assert parse_openai_tool_calls(message) == [
        ToolCall(id="call_1", name="compute_rsi", arguments={"ticker": "AAPL"})
    ]


def test_a_message_without_tool_calls_yields_none():
    assert parse_openai_tool_calls(SimpleNamespace(content="hi")) == []


def test_unparseable_arguments_degrade_to_empty_rather_than_raising():
    """A malformed call should reach the tool layer and be rejected there.

    The model can act on 'that argument was invalid'; it cannot act on a
    500 that ended the turn.
    """
    message = SimpleNamespace(
        tool_calls=[openai_call("call_1", "compute_rsi", "{not json")]
    )

    assert parse_openai_tool_calls(message)[0].arguments == {}


def test_non_object_arguments_degrade_to_empty():
    message = SimpleNamespace(tool_calls=[openai_call("call_1", "compute_rsi", "[1,2]")])

    assert parse_openai_tool_calls(message)[0].arguments == {}


# ── Tool schema translation ─────────────────────────────────────────────────


OPENAI_TOOL = {
    "type": "function",
    "function": {
        "name": "compute_rsi",
        "description": "Compute RSI.",
        "parameters": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
}


def test_a_function_schema_becomes_an_anthropic_tool():
    assert to_anthropic_tools([OPENAI_TOOL]) == [{
        "name": "compute_rsi",
        "description": "Compute RSI.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    }]


def test_no_tools_translates_to_no_tools():
    assert to_anthropic_tools(None) == []
    assert to_anthropic_tools([]) == []


def test_a_schema_without_parameters_still_produces_a_valid_input_schema():
    """Anthropic rejects a tool with no input_schema, so a default is required."""
    result = to_anthropic_tools([{"function": {"name": "ping"}}])

    assert result[0]["input_schema"] == {"type": "object", "properties": {}}


@pytest.mark.parametrize(
    "openai_choice,expected",
    [(None, {"type": "auto"}), ("auto", {"type": "auto"}), ("required", {"type": "any"})],
)
def test_tool_choice_translates(openai_choice, expected):
    assert to_anthropic_tool_choice(openai_choice) == expected


def test_tool_choice_none_has_no_anthropic_equivalent():
    """Expressed by omitting tools entirely, which the caller handles."""
    assert to_anthropic_tool_choice("none") is None


# ── Conversation translation ────────────────────────────────────────────────


def test_plain_turns_pass_through():
    assert to_anthropic_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_an_assistant_turn_with_tool_calls_becomes_tool_use_blocks():
    result = to_anthropic_messages([{
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "compute_rsi", "arguments": '{"ticker": "AAPL"}'},
        }],
    }])

    assert result == [{
        "role": "assistant",
        "content": [{
            "type": "tool_use", "id": "call_1",
            "name": "compute_rsi", "input": {"ticker": "AAPL"},
        }],
    }]


def test_assistant_text_alongside_tool_calls_is_kept():
    result = to_anthropic_messages([{
        "role": "assistant",
        "content": "Checking.",
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "compute_rsi", "arguments": "{}"},
        }],
    }])

    assert result[0]["content"][0] == {"type": "text", "text": "Checking."}
    assert result[0]["content"][1]["type"] == "tool_use"


def test_a_tool_result_becomes_a_user_turn():
    """OpenAI's role:"tool" has no Anthropic equivalent."""
    result = to_anthropic_messages([
        {"role": "tool", "tool_call_id": "call_1", "content": '{"rsi": 28.4}'},
    ])

    assert result == [{
        "role": "user",
        "content": [{
            "type": "tool_result", "tool_use_id": "call_1", "content": '{"rsi": 28.4}',
        }],
    }]


def test_parallel_tool_results_merge_into_one_user_turn():
    """Anthropic rejects the sequence if they arrive as separate turns."""
    result = to_anthropic_messages([
        {"role": "tool", "tool_call_id": "call_1", "content": "a"},
        {"role": "tool", "tool_call_id": "call_2", "content": "b"},
    ])

    assert len(result) == 1
    assert [b["tool_use_id"] for b in result[0]["content"]] == ["call_1", "call_2"]


def test_a_user_turn_after_tool_results_does_not_merge_into_them():
    result = to_anthropic_messages([
        {"role": "tool", "tool_call_id": "call_1", "content": "a"},
        {"role": "user", "content": "and now?"},
    ])

    assert len(result) == 2
    assert result[1] == {"role": "user", "content": "and now?"}


def test_a_full_tool_round_trip_survives_translation():
    """The shape an actual second loop iteration sends."""
    result = to_anthropic_messages([
        {"role": "user", "content": "is AAPL oversold?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "function": {"name": "compute_rsi",
                                          "arguments": '{"ticker": "AAPL"}'}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"current_rsi": 28.4}'},
        {"role": "assistant", "content": "RSI is 28.4."},
    ])

    assert [m["role"] for m in result] == ["user", "assistant", "user", "assistant"]
    assert result[1]["content"][0]["type"] == "tool_use"
    assert result[2]["content"][0]["type"] == "tool_result"


def test_already_parsed_arguments_are_accepted():
    """Our own replayed history stores arguments as dicts, not JSON strings."""
    result = to_anthropic_messages([{
        "role": "assistant",
        "tool_calls": [{"id": "c1", "function": {"name": "f",
                                                 "arguments": {"ticker": "AAPL"}}}],
    }])

    assert result[0]["content"][0]["input"] == {"ticker": "AAPL"}
