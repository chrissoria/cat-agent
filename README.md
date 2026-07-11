# cat-claws

Agent-SDK backends for the [CatLLM ecosystem](https://github.com/chrissoria/cat-llm):
classify text through a **Claude subscription** (Claude Agent SDK) or a
**ChatGPT subscription** (openai-codex SDK) instead of per-token API billing.

*(Distribution name `cat-claws`; imports as `catclaws`. Source repo:
[cat-agent](https://github.com/chrissoria/cat-agent).)*

**Status: alpha, under active development.** See `MASTERPLAN.md` for the
design and step tracker, and `OPENAI_MASTERPLAN.md` for the Codex backend's
execution record.

## Install

Each backend's SDK is an extra — install the one(s) you use:

```bash
pip install "cat-claws[claude]"   # Claude backend (claude-agent-sdk)
pip install "cat-claws[codex]"    # Codex backend (openai-codex, bundles the codex binary)
pip install "cat-claws[claude,codex]"  # both
```

A bare `pip install cat-claws` installs neither SDK; calls then return a
clear per-row install hint instead of classifying.

## Design in one paragraph

One row = one sealed, fresh-context agent call (no tools, single turn, no
settings/AGENTS.md/CLAUDE.md loading), using cat-stack's validated
classification prompt byte-for-byte. The model answers in JSON; parsing and
the wide 0/1 output matrix reuse cat-stack's existing machinery. Throughput
comes from concurrent one-shot calls, never from shared conversations or
corpus-in-one-prompt (which would contaminate rows and break research
validity).

## Quick start

```python
import catclaws

# Claude subscription (requires Claude Code installed and logged in):
df = catclaws.classify(
    input_data=["I moved for a new job", "Rent got too expensive"],
    categories=["Employment", "Cost of living", "Other"],
    description="Why did you move?",
    agent="claude",                 # default; user_model=None -> claude-sonnet-5
)

# ChatGPT subscription (requires `codex login`; the SDK bundles the binary):
df = catclaws.classify(
    input_data=["I moved for a new job", "Rent got too expensive"],
    categories=["Employment", "Cost of living", "Other"],
    description="Why did you move?",
    agent="codex",                  # user_model=None -> gpt-5.5
)
```

No API key needed for either backend. Engine users reach the same adapters
via `catstack.classify(..., model_source="claude-agent")` /
`model_source="codex-agent"`.

Notes for `agent="codex"`: reasoning is explicitly set per call
(`thinking_budget=0` → effort "none"), so your `~/.codex/config.toml`
defaults are never silently inherited; image/PDF input is not yet supported
on the codex backend (use `agent="claude"` or an API provider).

## Methodology note

The two backends answer the same frozen prompt. On the 24-row synthetic
parity run (2026-07-11, `benchmarks/parity_run.py`) claude-sonnet-5 and
gpt-5.5 agreed on 96/96 cells (Cohen's kappa 1.000, 0 errors). Synthetic
one-liners are easy; expect some divergence on real survey text — measure
and disclose per study (`benchmarks/RESULTS.md` holds the running record),
and never tune the prompt to force agreement.
