from .base import AgentAdapter
from .claude import ClaudeAdapter

# Adapter registry — Codex joins here in a later phase.
ADAPTERS = {
    "claude": ClaudeAdapter,
}


def get_adapter(name: str) -> AgentAdapter:
    try:
        return ADAPTERS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown agent {name!r}. Available: {sorted(ADAPTERS)}"
        ) from None
