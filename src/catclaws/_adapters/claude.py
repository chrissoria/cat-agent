"""Claude adapter — claude-agent-sdk implementation of AgentAdapter.

Requires Claude Code installed and logged in (`claude` on PATH). Calls run
through the user's Claude subscription, not an API key.
"""

from .base import (  # noqa: F401 — helper re-exports keep older imports valid
    RATE_LIMIT_PREFIX,
    AgentAdapter,
    _RATE_LIMIT_TEXT_MARKERS,
    _finalize,
    _looks_rate_limited_text,
)

# --- rate-limit detection (pure helpers, unit-tested in tests/test_rate_limit.py) ---
#
# The SDK surfaces subscription limits three ways (verified on sdk 0.2.110):
#   1. a `RateLimitEvent` in the message stream, carrying a `RateLimitInfo`
#      whose `status`/`overage_status` is one of 'allowed'|'allowed_warning'|
#      'rejected' — only 'rejected' is an actual block;
#   2. a `ResultMessage.api_error_status` of 429 (too many requests);
#   3. as a last resort, rate-limit wording in an error/result string.
# All three funnel into the RATE_LIMIT_PREFIX error so classify() backs off.
# The agent-generic pieces (_looks_rate_limited_text, _finalize, the text
# markers) live in base.py and are re-imported above.


def _rate_limit_detail(info) -> str | None:
    """Human detail if the PRIMARY usage window is exhausted, else None.

    Only ``status == "rejected"`` blocks the current request. 'allowed' and
    'allowed_warning' both let it through (the latter just warns the cap is
    near). Crucially, ``overage_status`` is NOT a per-request block: a common
    steady state is ``overage_status='rejected'`` with
    ``overage_disabled_reason='org_level_disabled'`` — the org simply turned
    off spillover billing — while ``status='allowed'`` and the call succeeds.
    Treating overage rejection as a limit falsely fails every call on such
    accounts (verified live 2026-07-03), so it is deliberately ignored here.
    """
    if info is None:
        return None
    if getattr(info, "status", None) != "rejected":
        return None
    rtype = getattr(info, "rate_limit_type", None) or "usage"
    resets = getattr(info, "resets_at", None)
    detail = f"{rtype} limit reached"
    if resets:
        detail += f" (resets at epoch {resets})"
    return detail


def _api_status_is_rate_limit(status) -> bool:
    """HTTP 429 (too many requests) is the rate-limit status."""
    return status == 429


class ClaudeAdapter(AgentAdapter):
    name = "claude"
    default_model = "claude-sonnet-5"

    async def one_shot(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str,
        thinking_budget: int = 0,
        images: list | None = None,
    ) -> tuple[str | None, str | None]:
        try:
            from claude_agent_sdk import (
                query,
                ClaudeAgentOptions,
                AssistantMessage,
                RateLimitEvent,
                ResultMessage,
                TextBlock,
                CLINotFoundError,
            )
            from claude_agent_sdk.types import (
                ThinkingConfigDisabled,
            )
        except ImportError as e:
            return None, (
                'claude-agent-sdk is not installed. Run: pip install "cat-claws[claude]" '
                f"(original error: {e})"
            )

        # Sealed session: fresh context, no tools, one turn, no user/project
        # settings or CLAUDE.md files (running classify() from inside a repo
        # must not inject that repo's instructions into classifications).
        opts_kwargs = dict(
            model=model,
            allowed_tools=[],
            max_turns=1,
            setting_sources=[],
        )
        if system_prompt:
            opts_kwargs["system_prompt"] = system_prompt

        # Engine parity: the agent enables thinking by default (Phase-0
        # probe), but cat-stack's default is thinking_budget=0 -> off.
        # Positive budgets grade into the shared effort vocabulary.
        if thinking_budget and thinking_budget > 0:
            from catstack._providers import _thinking_budget_to_effort
            opts_kwargs["effort"] = _thinking_budget_to_effort(thinking_budget)
        else:
            opts_kwargs["thinking"] = ThinkingConfigDisabled(type="disabled")

        def _make_query_prompt():
            """Fresh query input per call (a generator can't be re-iterated on
            the thinking-fallback retry). Text -> the plain string; images -> a
            streaming-input message carrying base64 image content blocks (the
            shape verified against the SDK)."""
            if not images:
                return prompt

            async def _stream():
                content = [{"type": "text", "text": prompt}]
                for im in images:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": im.get("media_type", "image/png"),
                            "data": im["data"],
                        },
                    })
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": content},
                    "parent_tool_use_id": None,
                    "session_id": "catclaws",
                }

            return _stream()

        async def _run(options):
            """Consume one query stream -> (text, result_error, rate_limit_detail)."""
            text_parts = []
            result_error = None
            rate_limit_detail = None
            async for message in query(prompt=_make_query_prompt(), options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(message, RateLimitEvent):
                    detail = _rate_limit_detail(getattr(message, "rate_limit_info", None))
                    if detail:
                        rate_limit_detail = detail
                elif isinstance(message, ResultMessage):
                    if _api_status_is_rate_limit(getattr(message, "api_error_status", None)):
                        rate_limit_detail = rate_limit_detail or "HTTP 429 (too many requests)"
                    if getattr(message, "is_error", False):
                        errs = getattr(message, "errors", None) or []
                        parts = [
                            str(p)
                            for p in ([getattr(message, "result", None)] + list(errs))
                            if p
                        ]
                        result_error = " ".join(parts) or "agent returned an error result"
            return "".join(text_parts).strip(), result_error, rate_limit_detail

        try:
            return _finalize(*await _run(ClaudeAgentOptions(**opts_kwargs)))
        except CLINotFoundError:
            return None, (
                "Claude CLI not found. Install it: https://code.claude.com/docs"
            )
        except Exception as e:
            if _looks_rate_limited_text(e):
                return None, f"{RATE_LIMIT_PREFIX}{e}"
            # Thinking-config incompatibilities (e.g. models that reject an
            # explicit disable) fall back to the agent default rather than
            # failing the row.
            if "thinking" in str(e).lower() and "thinking" in opts_kwargs:
                opts_kwargs.pop("thinking", None)
                try:
                    return _finalize(*await _run(ClaudeAgentOptions(**opts_kwargs)))
                except Exception as e2:
                    if _looks_rate_limited_text(e2):
                        return None, f"{RATE_LIMIT_PREFIX}{e2}"
                    return None, f"claude adapter failed: {e2}"
            return None, f"claude adapter failed: {e}"
