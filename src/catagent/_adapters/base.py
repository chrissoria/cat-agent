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
        """
        raise NotImplementedError
