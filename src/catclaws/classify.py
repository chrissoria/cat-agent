"""catclaws.classify() — one-row-at-a-time classification through an agent CLI.

Design (see MASTERPLAN.md):
- One row = one sealed, fresh-context agent call. Throughput comes from
  bounded concurrency, never from shared conversations or corpus prompts.
- Prompts are cat-stack's validated classification prompt, byte-identical to
  the API path (`build_text_classification_prompt`).
- The model answers in JSON (prompt-requested); parsing reuses cat-stack's
  `extract_json` + `validate_classification_json`; output is the standard
  wide 0/1 DataFrame.
"""

import asyncio
import time

import pandas as pd

from ._adapters import get_adapter
from ._adapters.base import is_rate_limited, parse_reset_epoch
from ._backend import gather_bounded

# The system prompt is transport scaffolding (it replaces Claude Code's
# default agent persona), NOT part of the validated per-row instrument —
# the instrument travels entirely in the user prompt, as it does on the
# API path where no system message is sent for text classification.
_SYSTEM_PROMPT = (
    "You are a text classification engine. Follow the user's instructions "
    "exactly and reply with only what they ask for."
)

# First rate-limit backoff, in seconds (doubles each retry). Subscription
# usage windows are minutes-scale, so the wait starts coarse rather than at
# API-style sub-second values.
_RATE_LIMIT_BASE_DELAY = 30.0


def classify(
    input_data,
    categories,
    user_model: str | None = None,
    agent: str = "claude",
    description: str = "",
    multi_label: bool = True,
    thinking_budget: int = 0,
    max_workers: int = 4,
    json_retries: int = 2,
    rate_limit_retries: int = 2,
):
    """Classify text rows into 0/1 category indicators via an agent CLI.

    Runs on the agent's subscription login (no API key). Same prompt, same
    JSON contract, and same output schema as ``catstack.classify()``.

    Args:
        input_data: list of text rows (or pandas Series).
        categories: list of category names.
        user_model: model the agent should use. None (default) resolves to
            the agent's pinned default — "claude-sonnet-5" for claude,
            "gpt-5.5" for codex.
        agent: which agent answers — "claude" (Claude subscription via
            claude-agent-sdk) or "codex" (ChatGPT subscription via the
            openai-codex SDK).
        description: context about the data (survey question etc.) — feeds
            the same "Context:" line as the API path.
        multi_label: multiple categories per row (default) vs single best.
        thinking_budget: cat-stack semantics — 0 disables reasoning (default,
            engine parity), >0 grades into the agent's effort vocabulary.
        max_workers: concurrent sealed calls in flight.
        json_retries: re-asks per row when the reply isn't valid JSON.
        rate_limit_retries: on a rate-limited row, how many times to back off
            (exponential from 30s) and retry before giving up. Consumed before
            json_retries; set 0 to fail fast on limits. Other in-flight rows
            are unaffected while one row waits.

    Returns:
        pandas.DataFrame with input_data, processing_status, and one 0/1
        category_N column per category (same schema as catstack.classify()).
    """
    from catstack.text_functions_ensemble import build_text_classification_prompt
    from catstack import extract_json
    from catstack._utils import validate_classification_json

    rows = list(input_data)
    if not rows:
        raise ValueError("input_data is empty")
    categories = list(categories)
    if not categories:
        raise ValueError("categories is empty")

    adapter = get_adapter(agent)
    if user_model is None:
        user_model = adapter.default_model
    if user_model is None:  # defensive: every shipped adapter pins a default
        raise ValueError(
            f"pass user_model= (agent {agent!r} declares no default model)"
        )

    # Same prompt components as the engine builds them.
    categories_str = "\n".join(f"{i + 1}. {cat}" for i, cat in enumerate(categories))
    survey_question_context = f"Context: {description}." if description else ""

    def _row_prompt(text):
        messages = build_text_classification_prompt(
            response_text=text if text is not None else "",
            categories_str=categories_str,
            survey_question_context=survey_question_context,
            multi_label=multi_label,
        )
        return messages[-1]["content"]

    n_cats = len(categories)

    # Total seconds our backoff schedule (30s, doubling) can bridge. A limit
    # that resets beyond this can't be waited out here, so retrying is futile.
    _backoff_budget = sum(
        _RATE_LIMIT_BASE_DELAY * (2 ** k)
        for k in range(max(0, int(rate_limit_retries)))
    )

    def _success(values):
        return {
            "status": "success",
            "indicators": [
                1 if str(values.get(str(i + 1), "0")) == "1" else 0
                for i in range(n_cats)
            ],
        }

    async def _classify_row(text):
        """One row: sealed call -> parse -> validate.

        Two independent retry budgets. A rate-limited reply spends a
        `rate_limit_retries` slot: back off (exponential from 30s) and re-ask,
        without touching json_retries — re-asking a limit immediately would
        just hit it again. The `await asyncio.sleep` yields the event loop, so
        other in-flight rows keep going while this one waits. Any other
        malformed/failed reply spends a `json_retries` slot and re-asks now.
        """
        prompt = _row_prompt(text)
        last_error = "unknown error"
        rl_retries_left = max(0, int(rate_limit_retries))
        json_retries_left = max(0, int(json_retries))
        delay = _RATE_LIMIT_BASE_DELAY
        while True:
            reply, error = await adapter.one_shot(
                prompt,
                system_prompt=_SYSTEM_PROMPT,
                model=user_model,
                thinking_budget=thinking_budget,
            )
            if error and is_rate_limited(error):
                # A hard cap resetting beyond our backoff budget won't clear by
                # retrying — fail fast with the resumable message rather than
                # sleeping through futile re-asks (learned from a live
                # five_hour-window rejection). Unknown/near resets still back off.
                reset = parse_reset_epoch(error)
                futile = reset is not None and (reset - time.time()) > _backoff_budget
                if rl_retries_left > 0 and not futile:
                    rl_retries_left -= 1
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                # Backoff exhausted or futile — terminal for this row.
                return {"status": f"error: {error}", "indicators": [None] * n_cats}
            if error:
                last_error = error
            else:
                parsed = extract_json(reply)
                ok, values = (False, None)
                if parsed:
                    ok, values = validate_classification_json(parsed, n_cats)
                if ok:
                    return _success(values)
                last_error = f"invalid classification JSON in reply: {reply[:120]!r}"
            if json_retries_left > 0:
                json_retries_left -= 1
                continue
            return {"status": f"error: {last_error}", "indicators": [None] * n_cats}

    results = gather_bounded(
        [lambda t=t: _classify_row(t) for t in rows], max_workers=max_workers
    )

    out = {"input_data": rows, "processing_status": []}
    for i in range(n_cats):
        out[f"category_{i + 1}"] = []
    for res in results:
        if isinstance(res, Exception):
            res = {"status": f"error: {res}", "indicators": [None] * n_cats}
        out["processing_status"].append(
            "success" if res["status"] == "success" else res["status"]
        )
        for i in range(n_cats):
            out[f"category_{i + 1}"].append(res["indicators"][i])

    return pd.DataFrame(out)
