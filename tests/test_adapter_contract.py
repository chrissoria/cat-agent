"""Cross-adapter contract tests — parameterized over every registered adapter.

One spec table per adapter; the assertions are identical for all of them.
If you find yourself copy-pasting an adapter-specific test file instead of
adding a spec row here, wrong direction (IMPLEMENTATION_GUIDE.md §7 sanity
gate 2).

These tests must pass in an environment with NEITHER agent SDK installed:
missing-SDK paths are exercised by blocking the module in sys.modules, which
behaves identically whether or not the real package is present.
"""

import asyncio
import sys
from unittest.mock import patch

import pytest

from catclaws._adapters import ADAPTERS, get_adapter
from catclaws._adapters.base import RATE_LIMIT_PREFIX
from catclaws._adapters.claude import ClaudeAdapter
from catclaws._adapters.codex import CodexAdapter

SPECS = {
    "claude": {
        "cls": ClaudeAdapter,
        "sdk_modules": ("claude_agent_sdk", "claude_agent_sdk.types"),
        "install_hint": 'pip install "cat-claws[claude]"',
        "default_model": "claude-sonnet-5",
        "supports_images": True,
    },
    "codex": {
        "cls": CodexAdapter,
        "sdk_modules": ("openai_codex",),
        "install_hint": 'pip install "cat-claws[codex]"',
        "default_model": "gpt-5.5",
        "supports_images": False,
    },
}


def test_spec_table_covers_registry():
    """A new adapter must add a spec row (this is the enforcement)."""
    assert set(SPECS) == set(ADAPTERS)


def test_unknown_agent_lists_available():
    with pytest.raises(ValueError) as ei:
        get_adapter("nope")
    msg = str(ei.value)
    assert "claude" in msg and "codex" in msg


@pytest.mark.parametrize("name", sorted(SPECS))
class TestAdapterContract:
    def test_registry_round_trip(self, name):
        adapter = get_adapter(name)
        assert isinstance(adapter, SPECS[name]["cls"])
        assert adapter.name == name

    def test_default_model_is_pinned(self, name):
        # Pinned string, not None/account-default: research reproducibility.
        assert SPECS[name]["cls"].default_model == SPECS[name]["default_model"]

    def test_missing_sdk_degrades_politely(self, name):
        """No SDK -> (None, install hint). Never a raised ImportError, never
        a rate-limited-prefixed error (that would trigger useless backoff)."""
        spec = SPECS[name]
        blocked = {m: None for m in spec["sdk_modules"]}
        adapter = spec["cls"]()
        with patch.dict(sys.modules, blocked):
            text, error = asyncio.run(
                adapter.one_shot(
                    "prompt", system_prompt="sys", model=spec["default_model"]
                )
            )
        assert text is None
        assert spec["install_hint"] in error
        assert not error.startswith(RATE_LIMIT_PREFIX)

    def test_images_policy(self, name):
        spec = SPECS[name]
        if spec["supports_images"]:
            pytest.skip("adapter supports images; exercised by live smokes")
        adapter = spec["cls"]()
        text, error = asyncio.run(
            adapter.one_shot(
                "prompt",
                system_prompt=None,
                model=spec["default_model"],
                images=[{"media_type": "image/png", "data": "AAAA"}],
            )
        )
        assert text is None
        assert "not yet supported" in error
        assert not error.startswith(RATE_LIMIT_PREFIX)
