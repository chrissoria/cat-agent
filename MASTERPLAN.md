# cat-agent — Claude Agent SDK backend for the CatLLM ecosystem

*Drafted 2026-07-03. Published on PyPI as `cat-claws` (import `catclaws`); the
GitHub source repo remains `cat-agent`.*

> **Continuing this project? Read `IMPLEMENTATION_GUIDE.md` next** — it holds
> the verified facts (don't re-derive them), the traps already hit, and
> step-by-step instructions with acceptance criteria for every remaining
> phase. This file is the why; that file is the how.

## Why this package exists

cat-stack already has a `claude-code` provider: a ~100-line subprocess shim
around `claude -p`. It works, but it is under-engineered for research use:

1. **Cost/access** — the real prize. Rows classified through the user's
   Claude subscription instead of per-token API billing. For researchers and
   students without API budgets, "install Claude Code, log in, classify your
   survey" is a different accessibility story. The shim technically does
   this; the SDK makes it robust enough to recommend.
2. **Throughput** — the shim is sequential with full CLI startup per row
   (~33s/row measured on claude-haiku-4-5, 2026-07-03). The SDK is
   async-native: concurrent one-shot queries are the honest performance fix.
3. **Reliability** — the shim scrapes stdout; the SDK yields typed message
   objects, so "the assistant's final text" is extracted reliably.
4. **Isolation** — `claude -p` loads project settings and CLAUDE.md by
   default: running classify() from inside a repo can silently inject that
   repo's instructions into every classification. The SDK exposes explicit
   controls (`setting_sources`, custom `system_prompt`, `allowed_tools`).

## Design constraints (non-negotiable)

- **One row = one call = one fresh context.** Never a persistent
  conversation across rows (cross-row contamination breaks research
  validity). Never corpus-in-one-prompt. Throughput comes from concurrency,
  not context reuse.
- **The frozen prompt.** Prompts come from cat-stack's validated
  `build_text_classification_prompt` — byte-identical to the API path. This
  package is a transport, not a new instrument.
- **Sealed sessions.** `allowed_tools=[]`, single turn, no settings/CLAUDE.md
  loading, custom system prompt only. Classification must not touch the
  filesystem or improvise.
- **Same output contract.** The model answers in JSON (prompt-requested, as
  today); parsing goes through cat-stack's `extract_json` +
  `validate_classification_json`; output is the standard wide 0/1 DataFrame
  (`input_data`, `processing_status`, `category_N` columns). Everything
  downstream (ensembles, R, Stata, desktop) must be able to adopt this
  backend without schema changes.
- **The subprocess shim stays** in cat-stack as the zero-dependency fallback.
  `model_source="claude-code"` keeps meaning shim; this package introduces
  `model_source="claude-agent"`.
- **Dependency discipline.** This package depends on `claude-agent-sdk` and
  `cat-stack`. cat-stack never depends on this package — it lazy-imports it
  behind the `claude-agent` model_source (the `[formatter]`-extra pattern),
  erroring with `pip install cat-stack[agent]` guidance when absent.

## Architecture

Multi-agent by design: Claude (via `claude-agent-sdk`) is the first adapter;
OpenAI Codex (via the official `openai-codex` SDK) is the second — built
2026-07-11, see `OPENAI_MASTERPLAN.md`. The seam between "the classification
pipeline" and "which agent answers one prompt" is an explicit adapter
interface from day one — everything above the adapter is agent-agnostic.

```
cat-stack classify(model_source="claude-agent" | "codex-agent" …)
   └─ lazy import catclaws  → backend satisfies the same (text, error)
                              contract complete() returns
catclaws
   ├─ _adapters/
   │    base.py     AgentAdapter: one_shot(prompt, system_prompt, model,
   │                opts) -> (text, error). Sealed-session semantics are part
   │                of the contract (no tools, fresh context, single turn).
   │    claude.py   claude-agent-sdk implementation (Phase 1)
   │    codex.py    openai-codex SDK implementation (Phase 5, 2026-07-11)
   ├─ _backend.py   agent-agnostic plumbing: adapter registry, dedicated
   │                event loop, semaphore-bounded concurrency, retries
   ├─ classify.py   standalone classify(agent="claude") (Phase 1: usable
   │                without engine integration; later delegated to from
   │                cat-stack)
   └─ __about__.py  version (single source of truth, hatch)
```

Adapter-contract notes for Codex (as built, 2026-07-11): the official
`openai-codex` Python SDK superseded the original `codex exec` subprocess
plan (same SDK-over-shim reasoning as Phase 0). Auth via ChatGPT subscription
login mirrors the Claude story. `Sandbox.read_only` + `ApprovalMode.deny_all`
+ empty-tempdir cwd + `ephemeral=True` are the sealed-session equivalent.
The extras split shipped in 0.3.0 (`cat-claws[claude]`, `cat-claws[codex]`)
so neither SDK is forced on users of the other.

## Known risks / open questions

- **Rate limits**: subscription plans have usage caps; large jobs may hit
  them. Degrade gracefully (clear error, partial results, resumability) —
  never promise API-like throughput.
- **Model parity**: CLI-served vs API-served output comparability is a
  methodology-disclosure question for papers. Measure, document, disclose
  (never silently swap).
- **SDK tempo**: the Agent SDK tracks Claude Code releases; re-audit on CLI
  major versions (same habit as the 2026-07-03 shim audit).
- **Warm-process reuse**: can fresh-context queries share a warm process?
  (Phase 0 answers; if not, concurrency alone is the plan.)

## Step tracker

### Phase 0 — empirical spike (kill-or-validate) — DONE 2026-07-03, sdk 0.2.110
- [x] Install `claude-agent-sdk`; introspect the real API surface
- [x] Timing: 5.6s wall for a trivial one-shot, only ~1.9s process overhead
      (the shim's 33s/row was mostly sequential design + inference, not startup)
- [x] Context isolation: PASS — two `query()` calls share nothing
- [x] Sealed-session options verified (`allowed_tools=[]`, `max_turns=1`,
      `setting_sources=[]`, custom `system_prompt`)
- [x] Model selection verified (`claude-sonnet-5` by name, no error)
- [x] Concurrency: 4 parallel one-shots in 3.6s total (near-linear speedup)
- [x] Structured output: `output_format={"type":"json","schema":...}` was
      silently IGNORED (markdown answer, no structured field) — Phase 1 uses
      prompt-JSON + extract_json; Phase 3 re-probes future SDK versions.
- [x] Finding: the agent enables THINKING by default (ThinkingBlock observed
      on haiku). Engine parity requires thinking disabled at
      thinking_budget=0 and graded `effort` above it — the adapter must set
      this explicitly.

### Phase 1 — classify() v0 (single function, parity with shim) — DONE 2026-07-03
- [x] Repo skeleton: pyproject (hatch), __about__, README, CHANGELOG
- [x] Adapter contract (`_adapters/base.py`) + Claude adapter — sealed
      one-row call, (text, error) contract, thinking-off-by-default parity
- [x] `classify.py` — rows → frozen prompts → one_shot → extract_json →
      wide 0/1 DataFrame with processing_status
- [x] JSON retry (`json_retries`, per-row isolation)
- [x] Mocked unit tests incl. frozen-prompt byte-parity test (5 passing)
- [x] Live smoke test: 3 rows, claude-sonnet-5, 4.4s total (1.5s/row
      effective at max_workers=3 vs ~33s/row through the shim), matrix correct
- [x] Commit + push — github.com/chrissoria/cat-agent (private for now;
      flip to public at first PyPI release)

*Note: Phase 2's core (semaphore-bounded concurrency) landed in Phase 1 via
`_backend.gather_bounded`; Phase 2 now means benchmarks at realistic N +
rate-limit handling.*

### Phase 2 — concurrency + rate-limit handling — DONE 2026-07-03
- [x] Semaphore-bounded async gather (max_workers semantics) — landed in Phase 1
      (`_backend.gather_bounded`); per-row isolation confirmed by a mocked test
      (a throttled row's backoff does not stall healthy rows)
- [x] Graceful rate-limit handling + partial results — adapter detects a genuine
      exhaustion (primary `RateLimitEvent.status=="rejected"` or
      `ResultMessage.api_error_status==429`; `allowed`/`allowed_warning` non-
      blocking) and surfaces `rate-limited: … (resets at epoch N)`; classify()
      backs off on a budget separate from json_retries, **fails fast when the
      reset is beyond the backoff budget** (five_hour caps), never raises.
- [x] FALSE-POSITIVE BUG found + fixed 2026-07-03: detection had treated a
      `rejected` *overage* bucket as a limit, but org-disabled overage
      (`overage_disabled_reason=="org_level_disabled"`) is a billing config, not
      a block — the call succeeds. This failed EVERY call on institutional
      accounts. Now keys only on primary `status`, and a successful answer wins
      over an informational limit event. Live-confirmed: 3-row classify 3/3
      success, correct matrix, 4.3s (~1.4s/row). 39 mocked tests green.
- [x] `benchmarks/bench_classify.py` (synthetic data) + `benchmarks/RESULTS.md`
- [x] Clean throughput sweep (N=50 haiku, workers∈{1,4,8}) — run 2026-07-04:
      workers=1: 145.1s / 0.34 rows/s; workers=4: 36.2s / 1.38 rows/s;
      workers=8: 20.5s / 2.44 rows/s; 0 errors. See
      `benchmarks/RESULTS.md`.

### Phase 3 — structured output (if Phase 0 says it's real)
- [ ] Schema-enforced JSON (native or in-process tool trick)
- [ ] Fall back to prompt-JSON when unsupported

### Phase 4 — engine + ecosystem integration

*Two-level integration (decided 2026-07-03): DISPATCH lives in cat-stack
(the engine is the only layer that sees `model_source`, and the domain
packages / R / Stata all call catstack directly — routing anywhere higher
would exclude them); DISTRIBUTION lives in cat-llm (the meta-package bundles
cat-agent for users, same as it bundles the domain packages cat-stack
doesn't depend on). cat-stack never hard-depends on cat-agent.*

- [ ] cat-stack: `model_source="claude-agent"` lazy-import branch + `[agent]` extra
- [ ] cat-llm (meta): add `cat-agent` to dependencies so `pip install cat-llm`
      includes the agent backend
- [ ] Ensemble support (claude-agent as one model in a panel)
- [ ] explore/extract/summarize passthroughs
- [ ] R/Stata/desktop: no changes needed by design — verify
- [ ] Docs + methodology disclosure notes; first PyPI release (flip repo public)

### Phase 5 — Codex adapter

*Execution plan authored 2026-07-10: see `OPENAI_MASTERPLAN.md` — a
self-contained handoff (SDK dossier, live-spike checklist with fill-in
slots, per-repo wiring specs, gates). Design update since this section was
sketched: OpenAI now ships an official `openai-codex` Python SDK (async
client over the codex app-server, bundled binary, reuses `codex login`);
the spike and adapter target that SDK instead of subprocess `codex exec` —
the same SDK-over-shim call Phase 0 made for Claude.*

- [x] Execution plan authored (`OPENAI_MASTERPLAN.md`, 2026-07-10)
- [x] Phase-0-style spike on the `openai-codex` SDK — DONE 2026-07-11, all 11
      probes PASS (isolation, subscription auth, per-call effort override;
      findings recorded in OPENAI_MASTERPLAN §3, script committed)
- [x] `_adapters/codex.py` implementing the same AgentAdapter contract
      (sealed read-only/deny-all/empty-cwd/ephemeral session; effort "none"
      at thinking_budget=0; typed usage-limit mapping)
- [x] `model_source="codex-agent"` in cat-stack (table-driven
      `_call_agent_backend`, all claude-agent sites mirrored); extras split
      shipped (`cat-claws[claude]` / `cat-claws[codex]`, 0.3.0)
- [x] Cross-agent parity run 2026-07-11: 24 synthetic rows, frozen prompt —
      96/96 cell agreement, kappa 1.000, 0 errors (claude-sonnet-5 vs
      gpt-5.5); benchmarks/RESULTS.md + README methodology note
