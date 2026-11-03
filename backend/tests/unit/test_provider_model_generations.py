"""Request shape per Claude generation: what each model accepts.

Opus 4.7+, the 5.x line and Fable reject `temperature` with a 400, the 5.5
generation also rejects forced tool_choice, and all of them think on every
call. These pin which models get which arguments, with no network.
"""

from __future__ import annotations

import pytest

from app.models.provider import (
    THINKING_HEADROOM_TOKENS,
    AnthropicProvider,
    is_legacy_claude,
    to_anthropic_tool_choice,
)


@pytest.mark.parametrize("model", [
    "claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-sonnet-4-6",
    "claude-opus-4-6", "claude-opus-4-1", "claude-sonnet-4-20250514",
    "claude-3-5-sonnet-20241022",
])
def test_older_models_are_legacy(model):
    assert is_legacy_claude(model)


@pytest.mark.parametrize("model", [
    "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-opus-5-5",
    "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1", "claude-opus-6",
])
def test_newer_and_unknown_models_are_not(model):
    assert not is_legacy_claude(model)


def _provider(effort=""):
    p = AnthropicProvider.__new__(AnthropicProvider)
    p._effort = effort
    return p


def test_legacy_request_keeps_temperature_and_cap():
    kw = _provider("high")._request("claude-haiku-4-5", "s", [], 0.1, 512)
    assert kw["temperature"] == 0.1
    assert kw["max_tokens"] == 512
    assert "output_config" not in kw


def test_thinking_model_drops_temperature_and_gets_headroom():
    kw = _provider()._request("claude-opus-5-5", "s", [], 0.1, 2048)
    assert "temperature" not in kw
    assert kw["max_tokens"] == 2048 + THINKING_HEADROOM_TOKENS
    assert "output_config" not in kw


def test_effort_is_sent_only_when_configured():
    kw = _provider("low")._request("claude-opus-5-5", "s", [], 0.1, 4096)
    assert kw["output_config"] == {"effort": "low"}


def test_required_tool_choice_degrades_to_auto_where_forcing_is_rejected():
    assert to_anthropic_tool_choice("required", "claude-opus-5-5") == {"type": "auto"}
    assert to_anthropic_tool_choice("required", "claude-haiku-4-5") == {"type": "any"}
    assert to_anthropic_tool_choice("required") == {"type": "any"}


# ── JSON mode ───────────────────────────────────────────────────────────────

import json  # noqa: E402

from app.models.provider import extract_json_text  # noqa: E402


@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    '  ```json\n{"a": 1}\n```  \n',
    'Here is the analysis:\n\n{"a": 1}',
    'Here is the analysis:\n```json\n{"a": 1}\n```\nLet me know.',
])
def test_json_replies_are_normalised_to_the_object(raw):
    assert json.loads(extract_json_text(raw)) == {"a": 1}


def test_nested_braces_and_fences_inside_strings_survive():
    raw = '```json\n{"note": "use {x} here", "inner": {"b": [1, 2]}}\n```'
    assert json.loads(extract_json_text(raw)) == {"note": "use {x} here", "inner": {"b": [1, 2]}}


def test_text_with_no_object_is_left_for_the_caller_to_reject():
    assert extract_json_text("I can't help with that.") == "I can't help with that."
    assert extract_json_text("") == ""
