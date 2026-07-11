from .base import AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter

# Adapter registry. Class imports are safe without either SDK installed:
# both modules import their SDK lazily inside one_shot().
ADAPTERS = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
}


def get_adapter(name: str) -> AgentAdapter:
    try:
        return ADAPTERS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown agent {name!r}. Available: {sorted(ADAPTERS)}"
        ) from None
