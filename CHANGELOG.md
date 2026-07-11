# Changelog

All notable changes to cat-claws will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-11

- **Codex adapter** (`agent="codex"`): classify through a ChatGPT
  subscription via the official `openai-codex` SDK (bundles the codex
  binary; reuses `codex login` — verified to bill the plan even with
  `OPENAI_API_KEY` in the environment). Sealed session = `Sandbox.read_only`
  + `ApprovalMode.deny_all` + a fresh empty tempdir `cwd` (blocks AGENTS.md
  injection — verified with a live canary) + `ephemeral=True` +
  `base_instructions` (replaces the persona). Reasoning is set explicitly on
  EVERY call: `thinking_budget=0` → effort `"none"` (0 reasoning tokens,
  live-verified to override a `config.toml` global of `xhigh`), positive
  budgets grade through the shared low/medium/high ladder; a model that
  rejects `"none"` retries once at `"low"`, never `effort=None` (which would
  inherit user config). Rate limits map typed
  (`TurnError.codex_error_info` → `usageLimitExceeded` et al.) plus the
  shared text-marker fallback into the `"rate-limited: "` contract. Image
  input returns a clear not-yet-supported error (the SDK takes file paths;
  our contract is base64 — recorded as a follow-up in OPENAI_MASTERPLAN §3
  P9). Full spike record: `OPENAI_MASTERPLAN.md`, `test_live_codex_spike.py`.
- **BREAKING — extras split**: `claude-agent-sdk` moved from base
  dependencies to the `[claude]` extra; new `[codex]` extra carries
  `openai-codex`. Plain `pip install cat-claws` now installs neither SDK and
  degrades to per-row install hints. Install `"cat-claws[claude]"` (what
  cat-stack's `[agent]` extra and cat-llm's pin now do) and/or
  `"cat-claws[codex]"`. Verified in three fresh venvs: exact SDK isolation,
  polite cross-degradation, live 1-row smokes on both backends.
- **`classify(user_model=None)`** now resolves to the chosen agent's pinned
  `default_model` (`claude-sonnet-5` / `gpt-5.5`) instead of a hard-coded
  Claude default that was wrong for `agent="codex"`. Explicit models pass
  through unchanged; an adapter without a default raises a clear ValueError.
- Agent-generic helpers (`_finalize`, `_looks_rate_limited_text`, text
  markers) moved from `claude.py` to `_adapters/base.py` (re-imported in
  claude.py, so existing imports keep working); `AgentAdapter` gains the
  `default_model` attribute. Fixed a stale install hint that still said
  `pip install cat-agent`.
- Tests: cross-adapter contract suite parameterized over both adapters
  (`tests/test_adapter_contract.py`), codex internals with a stubbed SDK
  that runs with or without `openai-codex` installed
  (`tests/test_codex_adapter.py` — includes a regression for AsyncCodex's
  coroutine `thread_start`, caught live), agent-selection tests in
  `test_classify.py`. 70 mocked tests green with the SDK installed, 68 (+2
  skips) without it.
- Benchmarks: `bench_classify.py --agent {claude,codex}` (per-agent cheap
  tiers); new `benchmarks/parity_run.py` — 24 synthetic rows, both agents,
  frozen prompt: **96/96 cell agreement, kappa 1.000, 0 errors**
  (claude-sonnet-5 vs gpt-5.5, 2026-07-11; appended to RESULTS.md).

## [0.2.0] - 2026-07-04

- Multimodal `one_shot(images=...)`: image input via the Agent SDK's streaming-
  input dict (base64 image content blocks), verified live against the model.
  Additive — the text-only path is unchanged; the AgentAdapter base contract
  carries the `images` param. Enables cat-stack image + PDF classification on the
  Claude subscription (no API key).

## [0.1.0] - 2026-07-03

- Initial scaffold: MASTERPLAN.md (design + step tracker), package skeleton.
- classify() v0: one-row-sealed calls, frozen prompt, bounded concurrency,
  JSON re-asks, wide 0/1 DataFrame output.
- Phase 2 — rate-limit handling: the Claude adapter detects a genuinely
  exhausted usage window (a `RateLimitEvent` whose primary `status ==
  "rejected"`, or `ResultMessage.api_error_status == 429`; `allowed`/
  `allowed_warning` are non-blocking) and surfaces it with a `"rate-limited: "`
  error prefix, including the reset time when known. It deliberately IGNORES
  `overage_status` — a `rejected` overage bucket with
  `overage_disabled_reason == "org_level_disabled"` just means the org turned
  off spillover billing while the request still succeeds; treating it as a
  limit falsely failed every call on such accounts (common in institutional
  subscriptions). A successful answer always wins over an informational limit
  event. classify() gains `rate_limit_retries` (default 2): on a genuine limit a
  row backs off exponentially (30s, 60s…) on a budget separate from
  `json_retries`, other in-flight rows unaffected, and never raises. Reset-aware:
  when the reset is farther out than the backoff budget can bridge (e.g. a
  `five_hour` cap hours away) the row fails fast with the resumable message
  instead of sleeping through futile retries; near/unknown resets still back off.
- Phase 2 — `benchmarks/bench_classify.py`: synthetic-data throughput
  benchmark across max_workers settings; results in `benchmarks/RESULTS.md`.
