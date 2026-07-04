"""Rate-limit detection tests for the Claude adapter.

Two layers:
  * pure helpers (`_rate_limit_detail`, `_looks_rate_limited_text`,
    `_api_status_is_rate_limit`, `_finalize`) — no SDK objects needed;
  * one end-to-end pass driving `ClaudeAdapter.one_shot` with a patched
    `claude_agent_sdk.query` that yields REAL SDK message objects, so the
    isinstance dispatch and the false-positive guard are exercised for real.

We cannot trigger a live 429 on demand (and must not hammer the subscription
to try), so detection is verified structurally against synthetic objects.
"""

import asyncio
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from catclaws._adapters.base import RATE_LIMIT_PREFIX, parse_reset_epoch
from catclaws._adapters.claude import (
    ClaudeAdapter,
    _api_status_is_rate_limit,
    _finalize,
    _looks_rate_limited_text,
    _rate_limit_detail,
)


def _info(status="rejected", overage_status=None, rate_limit_type="five_hour",
          resets_at=None):
    """A duck-typed RateLimitInfo (the helper reads attributes via getattr)."""
    return SimpleNamespace(
        status=status,
        overage_status=overage_status,
        rate_limit_type=rate_limit_type,
        resets_at=resets_at,
        overage_resets_at=None,
    )


class TestPureHelpers:
    @pytest.mark.parametrize("text,expected", [
        ("429 Too Many Requests", True),
        ("You have hit your usage limit for the day", True),
        ("rate limit exceeded", True),
        ("a normal validation error", False),
        ("", False),
        (None, False),
    ])
    def test_looks_rate_limited_text(self, text, expected):
        assert _looks_rate_limited_text(text) is expected

    @pytest.mark.parametrize("status,expected", [
        (429, True), (200, False), (500, False), (None, False),
    ])
    def test_api_status(self, status, expected):
        assert _api_status_is_rate_limit(status) is expected

    def test_rate_limit_detail_rejected(self):
        detail = _rate_limit_detail(_info(status="rejected",
                                          rate_limit_type="seven_day",
                                          resets_at=1751000000))
        assert detail == "seven_day limit reached (resets at epoch 1751000000)"

    def test_overage_rejected_with_allowed_primary_is_not_a_limit(self):
        # Regression (2026-07-03): org-disabled overage shows overage_status=
        # 'rejected' while the primary window is 'allowed' and calls succeed.
        # This must NOT be read as a limit, or every call fails on such accounts.
        assert _rate_limit_detail(
            _info(status="allowed", overage_status="rejected")
        ) is None

    @pytest.mark.parametrize("status", ["allowed", "allowed_warning"])
    def test_allowed_and_warning_are_not_limits(self, status):
        # The crux: a warning means the request STILL WENT THROUGH.
        assert _rate_limit_detail(_info(status=status)) is None

    def test_finalize_answer_wins_over_limit_event(self):
        # A successful answer alongside an informational limit event must be
        # returned, not discarded (the root cause of the overage false-positive).
        assert _finalize("the answer", None, "five_hour limit reached") == \
            ("the answer", None)

    def test_rate_limit_detail_none_info(self):
        assert _rate_limit_detail(None) is None

    def test_finalize_plain_text(self):
        assert _finalize("hello", None, None) == ("hello", None)

    def test_finalize_empty_text(self):
        text, err = _finalize("", None, None)
        assert text is None and "empty" in err

    def test_finalize_plain_error(self):
        assert _finalize("", "boom", None) == (None, "boom")

    def test_finalize_detail_wins(self):
        text, err = _finalize("", "boom", "five_hour limit reached")
        assert text is None and err == f"{RATE_LIMIT_PREFIX}five_hour limit reached"

    def test_finalize_text_detected_error(self):
        text, err = _finalize("", "HTTP 429 too many requests", None)
        assert err.startswith(RATE_LIMIT_PREFIX)

    def test_parse_reset_epoch(self):
        err = "rate-limited: five_hour limit reached (resets at epoch 1783129200)"
        assert parse_reset_epoch(err) == 1783129200
        assert parse_reset_epoch("rate-limited: synthetic cap") is None
        assert parse_reset_epoch(None) is None


# --- integration: real SDK objects through a patched query -------------------

sdk = pytest.importorskip("claude_agent_sdk")


def _mk(cls, **over):
    """Construct a dataclass, filling required fields with type-blank values.

    Robust to the SDK adding fields between versions — only the fields we care
    about are set via **over; everything else gets a harmless blank.
    """
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in over:
            kwargs[f.name] = over[f.name]
            continue
        if f.default is not dataclasses.MISSING or \
                f.default_factory is not dataclasses.MISSING:
            continue  # dataclass supplies it
        t = str(f.type)
        if "None" in t or "Optional" in t or "Any" in t:
            kwargs[f.name] = None
        elif "bool" in t:
            kwargs[f.name] = False
        elif "int" in t:
            kwargs[f.name] = 0
        elif "float" in t:
            kwargs[f.name] = 0.0
        elif "dict" in t:
            kwargs[f.name] = {}
        elif "list" in t:
            kwargs[f.name] = []
        else:
            kwargs[f.name] = ""
    return cls(**kwargs)


def _patched_query(messages):
    async def fake_query(prompt, options):
        for m in messages:
            yield m
    return fake_query


def _one_shot(messages, **kw):
    with patch("claude_agent_sdk.query", _patched_query(messages)):
        return asyncio.run(
            ClaudeAdapter().one_shot(
                prompt="classify this", system_prompt="engine",
                model="claude-haiku-4-5", **kw
            )
        )


class TestAdapterIntegration:
    def test_rejected_event_is_rate_limited(self):
        info = _mk(sdk.RateLimitInfo, status="rejected",
                   rate_limit_type="five_hour", resets_at=1751000000)
        event = _mk(sdk.RateLimitEvent, rate_limit_info=info)
        text, err = _one_shot([event])
        assert text is None
        assert err.startswith(RATE_LIMIT_PREFIX)
        assert "five_hour" in err

    def test_warning_event_still_returns_answer(self):
        """allowed_warning must NOT abort — the answer that follows wins."""
        info = _mk(sdk.RateLimitInfo, status="allowed_warning",
                   rate_limit_type="five_hour")
        event = _mk(sdk.RateLimitEvent, rate_limit_info=info)
        block = _mk(sdk.TextBlock, text='{"1": 1, "2": 0}')
        msg = _mk(sdk.AssistantMessage, content=[block])
        text, err = _one_shot([event, msg])
        assert err is None
        assert text == '{"1": 1, "2": 0}'

    def test_org_disabled_overage_still_returns_answer(self):
        """The real 2026-07-03 payload: primary window 'allowed', overage
        'rejected' (org_level_disabled), and a successful answer. Must return
        the answer — NOT mislabel it rate-limited."""
        info = _mk(sdk.RateLimitInfo, status="allowed",
                   overage_status="rejected",
                   overage_disabled_reason="org_level_disabled",
                   rate_limit_type="five_hour", resets_at=1783129200)
        event = _mk(sdk.RateLimitEvent, rate_limit_info=info)
        block = _mk(sdk.TextBlock, text='{"1": 1, "2": 0}')
        msg = _mk(sdk.AssistantMessage, content=[block])
        text, err = _one_shot([event, msg])
        assert err is None
        assert text == '{"1": 1, "2": 0}'

    def test_result_api_error_429_is_rate_limited(self):
        result = _mk(sdk.ResultMessage, is_error=True, api_error_status=429,
                     result="Too many requests")
        text, err = _one_shot([result])
        assert text is None
        assert err.startswith(RATE_LIMIT_PREFIX)

    def test_ordinary_result_error_is_not_rate_limited(self):
        result = _mk(sdk.ResultMessage, is_error=True, api_error_status=None,
                     result="some other failure")
        text, err = _one_shot([result])
        assert text is None
        assert not err.startswith(RATE_LIMIT_PREFIX)
        assert "some other failure" in err
