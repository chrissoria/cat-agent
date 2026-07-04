"""Mocked tests for catclaws.classify() — no live agent CLI needed."""

import asyncio
from unittest.mock import patch

import pytest

import catclaws
from catclaws._adapters.base import AgentAdapter

# Captured before any test patches asyncio.sleep, so the recorder can still
# yield to the event loop with the genuine (zero-delay) sleep.
_REAL_SLEEP = asyncio.sleep


class _SleepRecorder:
    """Stand-in for asyncio.sleep: records durations, never actually waits."""

    def __init__(self):
        self.calls = []

    async def __call__(self, delay):
        self.calls.append(delay)
        await _REAL_SLEEP(0)  # yield control so other rows interleave


class FakeAdapter(AgentAdapter):
    """Deterministic adapter: keyword-matches the row text inside the prompt."""

    name = "fake"

    def __init__(self, replies=None, fail_first=0, rate_limit_first=0,
                 rate_limit_all=False, rate_limit_reset=None):
        self.replies = replies or {}
        self.fail_first = fail_first
        self.rate_limit_first = rate_limit_first
        self.rate_limit_all = rate_limit_all
        self.rate_limit_reset = rate_limit_reset
        self.calls = 0

    def _rl_error(self):
        if self.rate_limit_reset is not None:
            return ("rate-limited: five_hour limit reached "
                    f"(resets at epoch {self.rate_limit_reset})")
        return "rate-limited: synthetic cap"

    async def one_shot(self, prompt, system_prompt, model, thinking_budget=0):
        self.calls += 1
        if self.rate_limit_all or self.calls <= self.rate_limit_first:
            return None, self._rl_error()
        if self.calls <= self.fail_first:
            return None, "transient agent error"
        for needle, reply in self.replies.items():
            if needle in prompt:
                return reply, None
        return '{"1": 0, "2": 0}', None


def _run(adapter, rows=None, cats=None, **kw):
    with patch("catclaws.classify.get_adapter", return_value=adapter):
        return catclaws.classify(
            input_data=rows or ["job move", "family move"],
            categories=cats or ["Employment", "Family"],
            **kw,
        )


class TestMatrix:
    def test_wide_matrix_schema_and_values(self):
        adapter = FakeAdapter(replies={
            "job move": '{"1": 1, "2": 0}',
            "family move": 'Sure! Here you go: {"1": 0, "2": 1}',  # preamble tolerated
        })
        df = _run(adapter)
        assert list(df.columns) == ["input_data", "processing_status", "category_1", "category_2"]
        assert df["processing_status"].tolist() == ["success", "success"]
        assert df["category_1"].tolist() == [1, 0]
        assert df["category_2"].tolist() == [0, 1]

    def test_json_retry_recovers(self):
        adapter = FakeAdapter(replies={"job move": '{"1": 1, "2": 0}'}, fail_first=1)
        df = _run(adapter, rows=["job move"], json_retries=2)
        assert df["processing_status"].tolist() == ["success"]

    def test_bad_row_isolated(self):
        adapter = FakeAdapter(replies={
            "job move": '{"1": 1, "2": 0}',
            "family move": "I refuse to answer in JSON",
        })
        df = _run(adapter, json_retries=0)
        assert df["processing_status"][0] == "success"
        assert df["processing_status"][1].startswith("error:")
        assert df["category_1"].tolist()[0] == 1
        assert df["category_1"].isna()[1]

    def test_empty_inputs_raise(self):
        with pytest.raises(ValueError):
            catclaws.classify(input_data=[], categories=["A"])
        with pytest.raises(ValueError):
            catclaws.classify(input_data=["x"], categories=[])


class TestPromptParity:
    def test_prompt_is_the_frozen_instrument(self):
        """The prompt sent to the agent must be byte-identical to what the
        engine's build_text_classification_prompt produces."""
        captured = {}

        class CapturingAdapter(AgentAdapter):
            async def one_shot(self, prompt, system_prompt, model, thinking_budget=0):
                captured["prompt"] = prompt
                return '{"1": 1, "2": 0}', None

        _run(CapturingAdapter(), rows=["job move"], description="Why did you move?")

        from catstack.text_functions_ensemble import build_text_classification_prompt
        expected = build_text_classification_prompt(
            response_text="job move",
            categories_str="1. Employment\n2. Family",
            survey_question_context="Context: Why did you move?.",
            multi_label=True,
        )[-1]["content"]
        assert captured["prompt"] == expected


class TestRateLimitBackoff:
    def test_backoff_then_success(self):
        """A row rate-limited twice, then answered, recovers after exponential
        backoff (30s, 60s) — and rate-limit retries do NOT consume json_retries."""
        adapter = FakeAdapter(
            replies={"job move": '{"1": 1, "2": 0}'}, rate_limit_first=2
        )
        rec = _SleepRecorder()
        with patch("asyncio.sleep", rec):
            df = _run(adapter, rows=["job move"], json_retries=0,
                      rate_limit_retries=2)
        assert df["processing_status"].tolist() == ["success"]
        assert df["category_1"].tolist() == [1]
        assert rec.calls == [30.0, 60.0]  # exponential from 30s

    def test_rate_limit_budget_separate_from_json_retries(self):
        """After exhausting rate-limit retries on a limit, the row still gets
        its ordinary (non-rate-limit) attempt — the two budgets are independent."""
        # rate-limited twice, then a malformed (non-JSON) reply.
        adapter = FakeAdapter(
            replies={"job move": "not json at all"}, rate_limit_first=2
        )
        rec = _SleepRecorder()
        with patch("asyncio.sleep", rec):
            df = _run(adapter, rows=["job move"], json_retries=0,
                      rate_limit_retries=2)
        status = df["processing_status"][0]
        assert status.startswith("error: invalid classification JSON")  # not rate-limited
        assert rec.calls == [30.0, 60.0]

    def test_rate_limit_retries_zero_fails_fast(self):
        adapter = FakeAdapter(rate_limit_all=True)
        rec = _SleepRecorder()
        with patch("asyncio.sleep", rec):
            df = _run(adapter, rows=["x"], rate_limit_retries=0)
        assert df["processing_status"][0].startswith("error: rate-limited")
        assert rec.calls == []  # never waited

    def test_hard_cap_far_reset_fails_fast_without_backoff(self):
        """A limit resetting hours out can't be waited out — fail fast with the
        resumable message instead of burning ~90s of futile backoff."""
        import time
        future = int(time.time()) + 5 * 3600  # 5h away, past any backoff budget
        adapter = FakeAdapter(rate_limit_all=True, rate_limit_reset=future)
        rec = _SleepRecorder()
        with patch("asyncio.sleep", rec):
            df = _run(adapter, rows=["x"], rate_limit_retries=2)
        status = df["processing_status"][0]
        assert status.startswith("error: rate-limited")
        assert "resets at epoch" in status  # resumability info preserved
        assert rec.calls == []  # crucially: did NOT sleep on a futile retry

    def test_near_reset_still_backs_off(self):
        """A reset within the backoff budget is worth waiting out — retry."""
        import time
        near = int(time.time()) + 20  # inside the 30s+60s budget
        adapter = FakeAdapter(
            replies={"job move": '{"1": 1, "2": 0}'},
            rate_limit_first=1, rate_limit_reset=near,
        )
        rec = _SleepRecorder()
        with patch("asyncio.sleep", rec):
            df = _run(adapter, rows=["job move"], rate_limit_retries=2)
        assert df["processing_status"].tolist() == ["success"]
        assert rec.calls == [30.0]  # backed off once, then succeeded

    def test_partial_results_when_every_call_rate_limited(self):
        """Guide item 4: adapter rate-limits every call -> full DataFrame of
        error rows, never raises."""
        adapter = FakeAdapter(rate_limit_all=True)
        rows = ["a", "b", "c"]
        rec = _SleepRecorder()
        with patch("asyncio.sleep", rec):
            df = _run(adapter, rows=rows, cats=["X", "Y"], rate_limit_retries=2)
        assert len(df) == len(rows)  # nothing dropped
        assert all(s.startswith("error: rate-limited") for s in df["processing_status"])
        assert df["category_1"].isna().all() and df["category_2"].isna().all()
        # 2 backoff waits per row, none longer than the batch itself.
        assert rec.calls.count(30.0) == 3 and rec.calls.count(60.0) == 3

    def test_throttled_row_does_not_block_healthy_rows(self):
        """Guide item 3 sanity: a rate-limited row's backoff must not stall an
        independent healthy row (per-row isolation via the event loop)."""
        events = []

        class _IsolationAdapter(AgentAdapter):
            name = "iso"

            async def one_shot(self, prompt, system_prompt, model, thinking_budget=0):
                if "fast" in prompt:
                    events.append("fast-done")
                    return '{"1": 1, "2": 0}', None
                events.append("slow-rl")
                return None, "rate-limited: synthetic cap"

        rec = _SleepRecorder()

        def recording_sleep_factory():
            async def _sleep(delay):
                events.append(("sleep", delay))
                await _REAL_SLEEP(0)
            return _sleep

        with patch("asyncio.sleep", recording_sleep_factory()):
            df = _run(_IsolationAdapter(), rows=["fast", "slow"],
                      max_workers=2, rate_limit_retries=2)

        # Healthy row succeeded; throttled row exhausted backoff and errored.
        statuses = dict(zip(df["input_data"], df["processing_status"]))
        assert statuses["fast"] == "success"
        assert statuses["slow"].startswith("error: rate-limited")
        # Isolation proof: the healthy row finished BEFORE the throttled row's
        # first backoff wait even began — it did not queue behind it.
        assert events.index("fast-done") < events.index(("sleep", 30.0))
        # Only the throttled row ever slept.
        assert sum(1 for e in events if isinstance(e, tuple)) == 2
