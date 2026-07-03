"""Mocked tests for catagent.classify() — no live agent CLI needed."""

from unittest.mock import patch

import pytest

import catagent
from catagent._adapters.base import AgentAdapter


class FakeAdapter(AgentAdapter):
    """Deterministic adapter: keyword-matches the row text inside the prompt."""

    name = "fake"

    def __init__(self, replies=None, fail_first=0):
        self.replies = replies or {}
        self.fail_first = fail_first
        self.calls = 0

    async def one_shot(self, prompt, system_prompt, model, thinking_budget=0):
        self.calls += 1
        if self.calls <= self.fail_first:
            return None, "transient agent error"
        for needle, reply in self.replies.items():
            if needle in prompt:
                return reply, None
        return '{"1": 0, "2": 0}', None


def _run(adapter, rows=None, cats=None, **kw):
    with patch("catagent.classify.get_adapter", return_value=adapter):
        return catagent.classify(
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
            catagent.classify(input_data=[], categories=["A"])
        with pytest.raises(ValueError):
            catagent.classify(input_data=["x"], categories=[])


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
