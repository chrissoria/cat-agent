"""AgentAdapter contract.

An adapter turns ONE prompt into ONE answer through an agent CLI, with
sealed-session semantics baked into the contract:

- fresh context per call (no conversation shared across rows),
- no tools, single turn, no user/project settings or memory files loaded,
- a custom system prompt replacing the agent's own scaffolding.

Everything above the adapter (prompt building, JSON parsing, the output
matrix, concurrency) is agent-agnostic. The Claude adapter is the first
implementation; an OpenAI Codex adapter (`codex exec`) is planned against
this same contract.
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


class AgentAdapter:
    """One sealed agent call. Implementations are stateless."""

    name: str = "base"

    async def one_shot(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str,
        thinking_budget: int = 0,
    ) -> tuple[str | None, str | None]:
        """Run one sealed call; return (text, error) — exactly one is None.

        thinking_budget follows cat-stack semantics: 0 disables reasoning,
        >0 grades into the provider's effort vocabulary.

        Rate-limit failures must return an error string prefixed with
        ``RATE_LIMIT_PREFIX`` so the caller can back off (see contract note
        above). All other failures return an ordinary error string.
        """
        raise NotImplementedError
