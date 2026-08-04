"""The claude adapter must not let an inherited ANTHROPIC_API_KEY silently
switch billing from the subscription to the API.

Live finding 2026-08-04: with ANTHROPIC_API_KEY set in the parent process
(loaded from a .env for other providers), every agent call warned
"ANTHROPIC_API_KEY or another auth source is set and takes precedence over
your claude.ai login" — i.e. the run was key-billed. The adapter now blanks
the variable in the child env unless CATCLAWS_USE_API_KEY=1.
"""
import asyncio
from unittest.mock import patch

from claude_agent_sdk import AssistantMessage, TextBlock

from catclaws._adapters.claude import ClaudeAdapter


def _run_one_shot(captured):
    async def fake_query(prompt=None, options=None):
        captured.append(options)
        yield AssistantMessage(
            content=[TextBlock(text="ok")], model="claude-sonnet-5"
        )

    with patch("claude_agent_sdk.query", fake_query):
        return asyncio.run(
            ClaudeAdapter().one_shot(
                prompt="p", system_prompt=None, model="claude-sonnet-5"
            )
        )


def test_env_blanks_api_key_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-parent-process-key")
    monkeypatch.delenv("CATCLAWS_USE_API_KEY", raising=False)
    captured = []
    text, err = _run_one_shot(captured)
    assert err is None and text == "ok"
    assert captured[0].env == {"ANTHROPIC_API_KEY": ""}


def test_opt_in_keeps_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-parent-process-key")
    monkeypatch.setenv("CATCLAWS_USE_API_KEY", "1")
    captured = []
    text, err = _run_one_shot(captured)
    assert err is None and text == "ok"
    assert not captured[0].env
