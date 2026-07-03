"""Claude adapter — claude-agent-sdk implementation of AgentAdapter.

Requires Claude Code installed and logged in (`claude` on PATH). Calls run
through the user's Claude subscription, not an API key.
"""

from .base import AgentAdapter


class ClaudeAdapter(AgentAdapter):
    name = "claude"

    async def one_shot(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str,
        thinking_budget: int = 0,
    ) -> tuple[str | None, str | None]:
        try:
            from claude_agent_sdk import (
                query,
                ClaudeAgentOptions,
                AssistantMessage,
                ResultMessage,
                TextBlock,
                CLINotFoundError,
            )
            from claude_agent_sdk.types import (
                ThinkingConfigDisabled,
            )
        except ImportError as e:
            return None, (
                "claude-agent-sdk is not installed. Run: pip install cat-agent "
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
            from cat_stack._providers import _thinking_budget_to_effort
            opts_kwargs["effort"] = _thinking_budget_to_effort(thinking_budget)
        else:
            opts_kwargs["thinking"] = ThinkingConfigDisabled(type="disabled")

        try:
            text_parts = []
            result_error = None
            async for message in query(prompt=prompt, options=ClaudeAgentOptions(**opts_kwargs)):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    if getattr(message, "is_error", False):
                        result_error = getattr(message, "result", None) or "agent returned an error result"
            if result_error:
                return None, str(result_error)
            text = "".join(text_parts).strip()
            if not text:
                return None, "agent returned an empty response"
            return text, None
        except CLINotFoundError:
            return None, (
                "Claude CLI not found. Install it: https://code.claude.com/docs"
            )
        except Exception as e:
            # Thinking-config incompatibilities (e.g. models that reject an
            # explicit disable) fall back to the agent default rather than
            # failing the row.
            if "thinking" in str(e).lower() and "thinking" in opts_kwargs:
                opts_kwargs.pop("thinking", None)
                try:
                    text_parts = []
                    async for message in query(prompt=prompt, options=ClaudeAgentOptions(**opts_kwargs)):
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock):
                                    text_parts.append(block.text)
                    text = "".join(text_parts).strip()
                    if text:
                        return text, None
                except Exception as e2:
                    return None, f"claude adapter failed: {e2}"
            return None, f"claude adapter failed: {e}"
