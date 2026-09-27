"""The Claude adapter reports WHY a call failed, not the SDK's generic raise.

When the agent call fails (observed live: the CLI is not logged in), the
Agent SDK yields an error AssistantMessage ("Not logged in · Please run
/login", error="authentication_failed") and an is_error ResultMessage, then
raises a generic `Exception("Claude Code returned an error result:
success")`. The adapter used to lose the real reason and return only the
generic text. These tests drive `ClaudeAdapter.one_shot` with a patched
`claude_agent_sdk.query` yielding real SDK message objects.
"""

import asyncio
from unittest.mock import patch

import pytest

from catclaws._adapters.claude import ClaudeAdapter
from catclaws._auth import sign_in_help

sdk = pytest.importorskip("claude_agent_sdk")
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock  # noqa: E402

from test_rate_limit import _mk  # noqa: E402  (shared SDK-object builder)

NOT_LOGGED_IN = "Not logged in · Please run /login"


def _query(messages, raise_after=None):
    async def fake_query(prompt, options):
        for m in messages:
            yield m
        if raise_after:
            raise Exception(raise_after)
    return fake_query


def _one_shot(messages, raise_after=None):
    with patch("claude_agent_sdk.query", _query(messages, raise_after)):
        return asyncio.run(ClaudeAdapter().one_shot(
            prompt="classify this", system_prompt="engine", model="claude-sonnet-5"))


def _auth_failure_messages():
    return [
        _mk(AssistantMessage, content=[TextBlock(text=NOT_LOGGED_IN)],
            model="<synthetic>", error="authentication_failed"),
        _mk(ResultMessage, subtype="success", is_error=True, result=NOT_LOGGED_IN),
    ]


def test_real_reason_survives_the_generic_sdk_raise():
    text, err = _one_shot(_auth_failure_messages(),
                          raise_after="Claude Code returned an error result: success")
    assert text is None
    assert NOT_LOGGED_IN in err
    assert "error result: success" not in err


def test_auth_failure_gets_login_hint():
    _, err = _one_shot(_auth_failure_messages(),
                       raise_after="Claude Code returned an error result: success")
    assert err.endswith(sign_in_help("claude"))


def test_errored_assistant_text_is_never_returned_as_an_answer():
    # Same failure without the trailing raise: the synthetic "Not logged in"
    # text must not come back as a successful answer.
    text, err = _one_shot(_auth_failure_messages())
    assert text is None
    assert NOT_LOGGED_IN in err


def test_unrelated_exception_keeps_old_behavior():
    # No error in the stream: an exception still reports as an adapter failure.
    text, err = _one_shot([], raise_after="boom")
    assert text is None and err == "claude adapter failed: boom"
