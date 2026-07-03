"""catagent.classify() — one-row-at-a-time classification through an agent CLI.

Design (see MASTERPLAN.md):
- One row = one sealed, fresh-context agent call. Throughput comes from
  bounded concurrency, never from shared conversations or corpus prompts.
- Prompts are cat-stack's validated classification prompt, byte-identical to
  the API path (`build_text_classification_prompt`).
- The model answers in JSON (prompt-requested); parsing reuses cat-stack's
  `extract_json` + `validate_classification_json`; output is the standard
  wide 0/1 DataFrame.
"""

import pandas as pd

from ._adapters import get_adapter
from ._backend import gather_bounded

# The system prompt is transport scaffolding (it replaces Claude Code's
# default agent persona), NOT part of the validated per-row instrument —
# the instrument travels entirely in the user prompt, as it does on the
# API path where no system message is sent for text classification.
_SYSTEM_PROMPT = (
    "You are a text classification engine. Follow the user's instructions "
    "exactly and reply with only what they ask for."
)


def classify(
    input_data,
    categories,
    user_model: str = "claude-sonnet-5",
    agent: str = "claude",
    description: str = "",
    multi_label: bool = True,
    thinking_budget: int = 0,
    max_workers: int = 4,
    json_retries: int = 2,
):
    """Classify text rows into 0/1 category indicators via an agent CLI.

    Runs on the agent's subscription login (no API key). Same prompt, same
    JSON contract, and same output schema as ``catstack.classify()``.

    Args:
        input_data: list of text rows (or pandas Series).
        categories: list of category names.
        user_model: model the agent should use (e.g. "claude-sonnet-5").
        agent: which agent CLI answers ("claude"; "codex" planned).
        description: context about the data (survey question etc.) — feeds
            the same "Context:" line as the API path.
        multi_label: multiple categories per row (default) vs single best.
        thinking_budget: cat-stack semantics — 0 disables reasoning (default,
            engine parity), >0 grades into the agent's effort vocabulary.
        max_workers: concurrent sealed calls in flight.
        json_retries: re-asks per row when the reply isn't valid JSON.

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

    async def _classify_row(text):
        """One row: sealed call -> parse -> validate, with JSON re-asks."""
        prompt = _row_prompt(text)
        last_error = "unknown error"
        for _attempt in range(1 + max(0, int(json_retries))):
            reply, error = await adapter.one_shot(
                prompt,
                system_prompt=_SYSTEM_PROMPT,
                model=user_model,
                thinking_budget=thinking_budget,
            )
            if error:
                last_error = error
                continue
            parsed = extract_json(reply)
            ok, values = (False, None)
            if parsed:
                ok, values = validate_classification_json(parsed, n_cats)
            if ok:
                return {
                    "status": "success",
                    "indicators": [
                        1 if str(values.get(str(i + 1), "0")) == "1" else 0
                        for i in range(n_cats)
                    ],
                }
            last_error = f"invalid classification JSON in reply: {reply[:120]!r}"
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
