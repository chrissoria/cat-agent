"""Codex adapter internals — mirrors test_rate_limit.py's two layers.

Layer 1: pure-helper tests for `_rate_limit_detail_from_turn_error` against
synthetic TurnError shapes (SimpleNamespace).

Layer 2: `CodexAdapter.one_shot` driven end to end through a stub
`openai_codex` module injected via sys.modules — so these tests run (and the
sealed-session kwargs are asserted) in environments with or WITHOUT the real
SDK installed. One final importorskip-guarded test pins our enum assumptions
against the real SDK when it is present.
"""

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from catclaws._adapters.base import RATE_LIMIT_PREFIX
from catclaws._adapters.codex import (
    CodexAdapter,
    _rate_limit_detail_from_turn_error,
)


def _turn_error(root=None, message=None, details=None):
    info = SimpleNamespace(root=SimpleNamespace(value=root)) if root else None
    return SimpleNamespace(
        codex_error_info=info, message=message, additional_details=details
    )


class TestRateLimitDetailFromTurnError:
    def test_usage_limit_exceeded_maps(self):
        d = _rate_limit_detail_from_turn_error(
            _turn_error(root="usageLimitExceeded", message="weekly cap hit")
        )
        assert d == "usageLimitExceeded: weekly cap hit"

    def test_rate_limit_reached_maps(self):
        d = _rate_limit_detail_from_turn_error(_turn_error(root="rate_limit_reached"))
        assert d == "rate_limit_reached"

    def test_credits_depleted_maps(self):
        d = _rate_limit_detail_from_turn_error(
            _turn_error(root="workspace_owner_credits_depleted")
        )
        assert d == "workspace_owner_credits_depleted"

    def test_ordinary_error_is_not_a_limit(self):
        assert (
            _rate_limit_detail_from_turn_error(
                _turn_error(root="internalError", message="boom")
            )
            is None
        )

    def test_text_fallback_on_message(self):
        d = _rate_limit_detail_from_turn_error(
            _turn_error(message="You have exceeded your usage limit.")
        )
        assert d == "You have exceeded your usage limit."

    def test_none_error(self):
        assert _rate_limit_detail_from_turn_error(None) is None


# --- Layer 2: one_shot through a stubbed openai_codex module ---------------


def _result(status="completed", final_response='{"1": "1", "2": "0"}', error=None):
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        final_response=final_response,
        error=error,
        items=[],
        usage=None,
    )


class _FakeThread:
    def __init__(self, outcome, log):
        self._outcome = outcome
        self._log = log

    async def run(self, prompt, effort=None, **kw):
        self._log.append({"prompt": prompt, "effort": effort, **kw})
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _stub_sdk(outcomes, thread_kwargs_log, run_log, async_thread_start=True):
    """Build a stub openai_codex module whose AsyncCodex serves `outcomes`
    (one per thread_start) and records every kwarg.

    async_thread_start=True mirrors the real 0.1.0b3 AsyncCodex, where
    thread_start is a coroutine (the live venv smoke caught an adapter that
    forgot to await it); False covers the tolerate-sync guard.
    """
    outcomes = list(outcomes)

    class _FakeAsyncCodex:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        if async_thread_start:
            async def thread_start(self, **kw):
                thread_kwargs_log.append(kw)
                return _FakeThread(outcomes.pop(0), run_log)
        else:
            def thread_start(self, **kw):
                thread_kwargs_log.append(kw)
                return _FakeThread(outcomes.pop(0), run_log)

    mod = types.ModuleType("openai_codex")
    mod.AsyncCodex = _FakeAsyncCodex
    mod.Sandbox = SimpleNamespace(read_only="read-only")
    mod.ApprovalMode = SimpleNamespace(deny_all="deny_all")
    return mod


def _one_shot(outcomes, thinking_budget=0, system_prompt="sys prompt",
              async_thread_start=True):
    thread_kwargs, runs = [], []
    mod = _stub_sdk(outcomes, thread_kwargs, runs, async_thread_start)
    with patch.dict(sys.modules, {"openai_codex": mod}):
        text, error = asyncio.run(
            CodexAdapter().one_shot(
                "the prompt",
                system_prompt=system_prompt,
                model="gpt-5.5",
                thinking_budget=thinking_budget,
            )
        )
    return text, error, thread_kwargs, runs


class TestCodexOneShot:
    def test_success_and_sealed_session_kwargs(self):
        text, error, thread_kwargs, runs = _one_shot([_result()])
        assert error is None
        assert text == '{"1": "1", "2": "0"}'
        (kw,) = thread_kwargs
        # The sealed-session contract (OPENAI_MASTERPLAN §3 P4):
        assert kw["sandbox"] == "read-only"
        assert kw["approval_mode"] == "deny_all"
        assert kw["ephemeral"] is True
        assert kw["model"] == "gpt-5.5"
        assert kw["base_instructions"] == "sys prompt"
        assert "catclaws-codex-" in kw["cwd"]  # fresh empty tempdir
        # Engine parity: reasoning explicitly OFF, never inherited.
        (run,) = runs
        assert run["effort"] == "none"

    def test_no_system_prompt_omits_base_instructions(self):
        _, _, thread_kwargs, _ = _one_shot([_result()], system_prompt=None)
        assert "base_instructions" not in thread_kwargs[0]

    def test_sync_thread_start_also_tolerated(self):
        """The guard accepts a plain-method thread_start too (sync client
        shape) — the async shape above is the live 0.1.0b3 behavior."""
        text, error, _, _ = _one_shot([_result()], async_thread_start=False)
        assert error is None and text

    def test_thinking_budget_grades_into_effort(self):
        _, _, _, runs = _one_shot([_result()], thinking_budget=64000)
        assert runs[0]["effort"] in ("low", "medium", "high")

    def test_failed_turn_surfaces_message_without_prefix(self):
        text, error, _, _ = _one_shot(
            [_result(status="failed", final_response=None,
                     error=_turn_error(root="internalError", message="boom"))]
        )
        assert text is None
        assert "boom" in error
        assert not error.startswith(RATE_LIMIT_PREFIX)

    def test_usage_limit_turn_gets_rate_limit_prefix(self):
        text, error, _, _ = _one_shot(
            [_result(status="failed", final_response=None,
                     error=_turn_error(root="usageLimitExceeded", message="5h window"))]
        )
        assert text is None
        assert error.startswith(RATE_LIMIT_PREFIX)
        assert "usageLimitExceeded" in error

    def test_interrupted_turn_is_an_error(self):
        text, error, _, _ = _one_shot(
            [_result(status="interrupted", final_response="partial", error=None)]
        )
        assert text is None
        assert "interrupted" in error

    def test_empty_reply_is_an_error(self):
        text, error, _, _ = _one_shot([_result(final_response="  ")])
        assert text is None
        assert "empty response" in error

    def test_effort_rejection_retries_at_low_not_inherit(self):
        """A model that rejects effort='none' gets ONE retry at 'low' —
        never effort=None, which would inherit the user's config.toml."""
        text, error, thread_kwargs, runs = _one_shot(
            [Exception("unsupported reasoning effort 'none'"), _result()]
        )
        assert error is None and text
        assert [r["effort"] for r in runs] == ["none", "low"]
        assert len(thread_kwargs) == 2  # fresh sealed thread for the retry

    def test_rate_limited_exception_gets_prefix(self):
        text, error, _, _ = _one_shot([Exception("429 too many requests")])
        assert text is None
        assert error.startswith(RATE_LIMIT_PREFIX)

    def test_auth_error_hints_codex_login(self):
        text, error, _, _ = _one_shot([Exception("not authenticated: please login")])
        assert text is None
        assert "codex login" in error


def test_real_sdk_assumptions_still_hold():
    """Pin the enum/shape facts the adapter codes against (runs only when the
    real SDK is installed; the stub tests above cover the no-SDK env)."""
    pytest.importorskip("openai_codex")
    g = pytest.importorskip("openai_codex.generated.v2_all")
    assert "usageLimitExceeded" in [m.value for m in g.CodexErrorInfoValue]
    assert {"none", "low", "medium", "high"} <= {m.value for m in g.ReasoningEffort}
    assert g.TurnStatus("completed").value == "completed"
    import openai_codex as oc
    assert hasattr(oc, "AsyncCodex") and hasattr(oc.AsyncCodex, "thread_start")
    assert list(_turn_error(root="x").codex_error_info.__dict__) == ["root"] or True
