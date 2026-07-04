# cat-claws

Agent-CLI backend for the [CatLLM ecosystem](https://github.com/chrissoria/cat-llm):
classify text through a **Claude subscription** (via the Claude Agent SDK)
instead of per-token API billing. An OpenAI Codex adapter is planned.

*(Distribution name `cat-claws`; imports as `catclaws`. Source repo:
[cat-agent](https://github.com/chrissoria/cat-agent).)*

**Status: alpha, under active development.** See `MASTERPLAN.md` for the
design and step tracker.

## Install

```bash
pip install cat-claws
```

## Design in one paragraph

One row = one sealed, fresh-context agent call (no tools, single turn, no
settings/CLAUDE.md loading), using cat-stack's validated classification
prompt byte-for-byte. The model answers in JSON; parsing and the wide 0/1
output matrix reuse cat-stack's existing machinery. Throughput comes from
concurrent one-shot calls, never from shared conversations or
corpus-in-one-prompt (which would contaminate rows and break research
validity).

## Quick start (Phase 1)

```python
import catclaws

df = catclaws.classify(
    input_data=["I moved for a new job", "Rent got too expensive"],
    categories=["Employment", "Cost of living", "Other"],
    user_model="claude-sonnet-5",   # any model your Claude login can use
    description="Why did you move?",
)
```

Requires [Claude Code](https://code.claude.com/docs) installed and logged in
(`claude` on PATH). No API key needed.
