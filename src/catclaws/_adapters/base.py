"""AgentAdapter contract.

An adapter turns ONE prompt into ONE answer through an agent CLI, with
sealed-session semantics baked into the contract:

- fresh context per call (no conversation shared across rows),
- no tools, single turn, no user/project settings or memory files loaded,
- a custom system prompt replacing the agent's own scaffolding.

Everything above the adapter (prompt building, JSON parsing, the output
matrix, concurrency) is agent-agnostic. The Claude adapter (claude-agent-sdk)
and the Codex adapter (openai-codex SDK) both implement this contract.
"""

import re

# Part of the adapter contract: when a call fails specifically because the
# subscription hit a usage/rate limit, the returned error string starts with
# this prefix. classify() keys on it to apply backoff (a plain error does
# not retry with backoff). Every adapter must use this prefix for rate-limit
# failures so the backoff logic stays agent-agnostic.
RATE_LIMIT_PREFIX = "rate-limited: "

# When an adapter knows when the limit resets, it appends this suffix so the
# caller can tell a transient throttle (retry) from a hard cap hours away
# (futile to retry — fail fast with a resumable message). Optional: adapters
# without a reset time simply omit it, and the caller falls back to backoff.
_RESET_EPOCH_RE = re.compile(r"resets at epoch (\d+)")


def is_rate_limited(error: str | None) -> bool:
    """True if an adapter error string signals a subscription rate limit."""
    return bool(error) and error.startswith(RATE_LIMIT_PREFIX)


def parse_reset_epoch(error: str | None) -> int | None:
    """Unix epoch when the limit resets, if the adapter included one.

    Matches the ``(resets at epoch N)`` suffix the Claude adapter emits from
    the SDK's ``RateLimitInfo.resets_at``. Returns None when absent (unknown
    reset -> caller should fall back to bounded backoff)."""
    if not error:
        return None
    m = _RESET_EPOCH_RE.search(error)
    return int(m.group(1)) if m else None


# --- agent-generic result/error helpers (used by every adapter) -------------
#
# These lived in claude.py first; they contain nothing Claude-specific and
# duplicating them per adapter is exactly the near-twin drift this ecosystem
# got burned by. claude.py and codex.py import them from here.

_RATE_LIMIT_TEXT_MARKERS = (
    "rate limit",
    "rate-limit",
    "rate_limit",
    "usage limit",
    "too many requests",
    "quota exceeded",
    "429",
)


def _looks_rate_limited_text(text) -> bool:
    """Text fallback: does an error/result string read like a rate limit?"""
    if not text:
        return False
    low = str(text).lower()
    return any(marker in low for marker in _RATE_LIMIT_TEXT_MARKERS)


def _finalize(text, result_error, rate_limit_detail):
    """Turn collected call state into the (text, error) contract result.

    A real answer always wins: limit signals can arrive on SUCCESSFUL calls
    too (they report current utilization), so a present answer with no error
    means the request went through — never discard it for an informational
    limit event. Only when there's no answer do limits (retryable via backoff)
    win over ordinary errors.
    """
    if text and not result_error:
        return text, None
    if rate_limit_detail:
        return None, f"{RATE_LIMIT_PREFIX}{rate_limit_detail}"
    if result_error:
        if _looks_rate_limited_text(result_error):
            return None, f"{RATE_LIMIT_PREFIX}{result_error}"
        return None, str(result_error)
    if text:
        return text, None
    return None, "agent returned an empty response"


class AgentAdapter:
    """One sealed agent call. Implementations are stateless."""

    name: str = "base"
    # Model used when classify()'s user_model is None. A pinned string per
    # adapter (research reproducibility over account-default drift).
    default_model: str | None = None

    async def one_shot(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str,
        thinking_budget: int = 0,
        images: list | None = None,
    ) -> tuple[str | None, str | None]:
        """Run one sealed call; return (text, error) — exactly one is None.

        thinking_budget follows cat-stack semantics: 0 disables reasoning,
        >0 grades into the provider's effort vocabulary.

        images (optional): a list of ``{"media_type": str, "data": <base64>}``
        for multimodal (image/PDF-page) classification. When given, the adapter
        sends the images alongside the text prompt; when None, it is a plain
        text call.

        Rate-limit failures must return an error string prefixed with
        ``RATE_LIMIT_PREFIX`` so the caller can back off (see contract note
        above). All other failures return an ordinary error string.
        """
        raise NotImplementedError
