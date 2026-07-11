# OPENAI_MASTERPLAN — OpenAI Codex adapter (Phase 5 execution plan)

*Drafted 2026-07-10. This is the execution plan for the Codex backend that
MASTERPLAN.md sketches as Phase 5: classify survey text through a ChatGPT
subscription the same way cat-claws already does through a Claude
subscription. It is written to be executed by an agent (OpenAI Codex or
Claude) with no other context — every contract, path, and verified fact
needed is in this file. If anything here contradicts the code, trust the
code and update this file.*

> **Executor instructions.** Work the phases in order. You may fill in ONLY
> the `**Recorded finding:**` slots, the Gate verdict lines, and the §9
> checkboxes — the design decisions, contracts, extra names, and scope are
> settled; do not revise them. Phase A (the live spike) must be complete and
> Gate 1 passed before any Phase B code exists. Stop and report to the
> maintainer at any NO-GO, before any workaround that touches auth
> credentials (§8 risk 2), or if a change would violate §1. Bracket every
> work session with the sanity checks in `IMPLEMENTATION_GUIDE.md` §3b
> (pytest green incl. `TestPromptParity`, clean import, `git status`).

## 0. Goal

```python
catclaws.classify(rows, cats, agent="codex")                       # direct
catstack.classify(..., user_model="gpt-5.5",
                  model_source="codex-agent")                      # engine
```

Each row runs as ONE sealed call through the user's ChatGPT plan (no API
key), returning the standard wide 0/1 DataFrame. The "codex" adapter is a
second implementation of the existing `AgentAdapter` contract; everything
above the adapter (frozen prompt, JSON parsing, concurrency, backoff, output
schema) is already agent-agnostic and must not fork.

Repos (absolute paths; all three carry work in this plan):

- `/Users/chrissoria/Documents/Research/cat-agent` — package `cat-claws` 0.2.0 (import `catclaws`) — Phases A, B
- `/Users/chrissoria/Documents/Research/cat-stack` — package `cat-stack` (import `catstack`), the engine — Phase C
- `/Users/chrissoria/Documents/Research/cat-llm` — meta-package `cat-llm` (import `catllm`) — Phase D

## 1. Non-negotiables

1. **One row = one sealed call = one fresh context.** Never a persistent
   conversation across rows; never corpus-in-one-prompt. Throughput comes
   from concurrency, not context reuse.
2. **The prompt is frozen.** Row prompts come from
   `catstack.text_functions_ensemble.build_text_classification_prompt`,
   byte-identical to the API path (`tests/test_classify.py::TestPromptParity`
   enforces it). `classify._SYSTEM_PROMPT` is transport scaffolding, not part
   of the instrument. Nothing in this plan may alter prompt construction.
3. **The adapter contract** (`src/catclaws/_adapters/base.py`) is fixed:

   ```python
   class AgentAdapter:
       """One sealed agent call. Implementations are stateless."""
       name: str = "base"
       async def one_shot(self, prompt, system_prompt, model,
                          thinking_budget=0, images=None
                          ) -> tuple[str | None, str | None]:
   ```

   - Return `(text, error)`; **exactly one is None**.
   - Rate-limit failures return an error starting with
     `RATE_LIMIT_PREFIX = "rate-limited: "`, optionally ending with
     `(resets at epoch N)` — `parse_reset_epoch` matches
     `r"resets at epoch (\d+)"`. classify() keys backoff on this prefix.
   - **A successful answer always wins** over an informational limit event.
   - `thinking_budget` follows cat-stack semantics: 0 disables/minimizes
     reasoning; >0 grades into the provider's effort vocabulary.
   - SDK imports live INSIDE `one_shot` (lazy), so the eager class imports in
     `_adapters/__init__.py` never require either SDK at import time.
4. **Output schema is fixed**: DataFrame with `input_data`,
   `processing_status` ("success" / "error: ..."), `category_N` 0/1 columns
   (None on error rows). One bad row never aborts a batch.
5. **Sealed sessions.** The Claude adapter seals with `allowed_tools=[]`,
   `max_turns=1`, `setting_sources=[]`, custom `system_prompt`. The Codex
   adapter must achieve the equivalent (§3 P4 finds the exact knobs): no
   repo/AGENTS.md contamination, no tool wandering, custom instructions.
6. **Release discipline.** Accumulate CHANGELOG entries as you work.
   cat-claws: stage `0.3.0` (`src/catclaws/__about__.py` + CHANGELOG heading)
   only at the very end; PUBLISHING to PyPI is a separate maintainer-approved
   step. cat-stack and cat-llm: CHANGELOG `[Unreleased]` entries ONLY — no
   version bumps (they ship in their own release batches).
7. **Never touch** `cat-stack/src/catstack/collapse_themes.py` (maintainer's
   uncommitted WIP) or the raw study data. Synthetic rows only in benchmarks.
8. **Dependency discipline**: stdlib first; no new hard deps anywhere. For
   any SDK behavior claim: probe empirically before coding against it.
9. **Never hammer the subscription** to trigger rate limits, and keep spike
   turns few and tiny. Scratch dirs via `tempfile` only.

## 2. Dossier

### 2a. `openai-codex` Python SDK — public-docs facts (2026-07-10), NOT yet probed on this machine

Every row below must be confirmed or corrected by spike P0 before the
adapter is written. Training-data knowledge of this SDK is stale — it was
first published June 2026.

| Claim (source: developers.openai.com/codex/sdk, github openai/codex sdk/python, deepwiki) | Confidence |
|---|---|
| `pip install openai-codex` (beta; publishes runtime wheels; depends on `openai-codex-cli-bin` platform wheels that BUNDLE the codex binary — no separate CLI install needed) | high |
| Import name `openai_codex`: `from openai_codex import Codex, Sandbox` seen in official example | medium — P0 confirms (candidates: `openai_codex`, `codex_sdk`, `codex`) |
| Sync `Codex` and async `AsyncCodex` clients; both control a local `codex app-server` subprocess over JSON-RPC | high |
| `codex.thread_start(model=..., sandbox=Sandbox.read_only\|workspace_write\|full_access, base_instructions=..., approval_mode=...)`; a dict-options variant `start_thread({"working_directory": ..., "skip_git_repo_check": True})` also appears in docs — exact signature/kwargs unknown | medium — P0 records `inspect.signature` |
| `thread.run(prompt)` → `TurnResult` with `final_response`, collected items, token usage; per-turn options incl. `{"output_schema": <json-schema dict>}` | medium |
| `thread_resume`, `thread_fork` exist (NOT used here — sealed calls only) | medium |
| Client config (`CodexConfig`?) may accept `codex_bin`, `cwd`, `env` for the subprocess | low — P0 |
| Auth: reuses existing `codex login` ChatGPT credentials with zero config; helpers `login_api_key()`, `login_chatgpt()`, `login_chatgpt_device_code()` exist | high for reuse; P1 verifies billing mode |
| Python >= 3.10 (matches cat-claws' floor) | high |

### 2b. Models and plan limits (public, 2026-07-10)

- ChatGPT-authenticated Codex default model: **gpt-5.5** (fallback gpt-5.4);
  `gpt-5.4-mini` exists for lighter tasks; gpt-5.6 tiers went GA 2026-07-09.
  Older `*-codex` names (gpt-5.2, gpt-5.3-codex) are deprecated for
  ChatGPT-auth sessions. The adapter pins `default_model = "gpt-5.5"` and
  passes any user-supplied model string through verbatim.
- Plan limits: rolling **5-hour** windows plus **weekly** caps (Plus/Pro
  tiers); errors read like "usage limit reached" / "exceeded your …
  usage limit". `base.py`'s existing text markers ("usage limit",
  "rate limit", "quota exceeded", "too many requests", "429") already match
  these shapes. Reset-time machine-readability is unknown → P6.

### 2c. This machine (probed 2026-07-10)

- macOS; python = `/Users/chrissoria/anaconda3/bin/python3`. `catclaws` is
  installed editable from the cat-agent repo.
- **`catstack` imports as 2.0.1 from site-packages**, NOT the 2.3.0 repo.
  Fine for Phase A/B (the prompt builder + JSON helpers exist in 2.0.1 and
  the parity canary passes against it). Phase C live dispatch tests require
  `pip install -e ~/Documents/Research/cat-stack` FIRST — an env change the
  maintainer has pre-acknowledged; announce it when you do it.
- **`~/.codex/auth.json` exists** (codex is logged in): keys
  `['OPENAI_API_KEY', 'auth_mode', 'last_refresh', 'tokens']`. Read-only
  inspection allowed; never copy/move/edit it.
- **`~/.codex/config.toml` sets `model = "gpt-5.5"` and
  `model_reasoning_effort = "xhigh"`** plus a trusted-projects list. This is
  the #1 contamination trap: if per-thread options can't override user
  config, every classification silently runs at xhigh (slow, quota-burning,
  non-reproducible on other machines). Promoted to GO/NO-GO in P5.
- Homebrew codex CLI 0.144.1 at `/opt/homebrew/bin/codex`; the SDK bundles
  its own binary — P0 records which one the SDK actually runs.
- Claude precedent for acceptable overhead: ~1.9s process spawn per one-shot
  (MASTERPLAN Phase 0), throughput via `max_workers`.

## 3. Phase A — live spike (blocks everything else)

**Script:** `/Users/chrissoria/Documents/Research/cat-agent/test_live_codex_spike.py`
(repo root, committed; author-env-only live script, same convention as the
ecosystem's other repo-root live tests). First step:
`pip install openai-codex` into the anaconda env and record the exact
version — it becomes the pyproject floor in §4.6.

Design rules: numbered probes, each independently try/excepted so one
failure never hides the rest; each prints `PROBE n: PASS/FAIL — <finding>`;
summary block at the end; timings via `time.perf_counter()`; scratch dirs
via `tempfile.mkdtemp()`; total spend ~15–25 tiny turns, nothing bigger.

After running, fill every slot below AND keep the raw script output (append
it to the bottom of this file or commit it as `spike_output.txt`).

- [x] **P0 — import surface.** Real import name; `__version__`; `dir()` of
  the client classes; `inspect.signature` of thread-start and run; whether
  options are kwargs, an options object, or dicts; where the bundled binary
  lives and its version (vs homebrew 0.144.1); which binary the app-server
  actually spawns. Every §2a row confirmed/corrected.
  **Recorded finding:** import name `openai_codex`, v0.1.0b3 (deps:
  `openai-codex-cli-bin` 0.137.0a4 → import `codex_cli_bin`, bundled binary
  codex-cli 0.137.0-alpha.4 at `site-packages/codex_cli_bin/bin/codex` —
  the SDK uses this, NOT the homebrew 0.144.1; plus `pydantic`). Sync
  `Codex` / async `AsyncCodex`, both context managers; `Codex(config:
  CodexConfig | None)`; `CodexConfig(codex_bin, launch_args_override,
  config_overrides: tuple[str, ...], cwd, env, ...)`. `thread_start(*,
  approval_mode=ApprovalMode.auto_review, base_instructions, config, cwd,
  developer_instructions, ephemeral, model, model_provider, personality,
  sandbox, service_tier, ...) -> Thread` — a plain method on the sync
  client but a COROUTINE on AsyncCodex (await it; `inspect.signature` hides
  the difference — caught live by the Gate-3 codex venv smoke). `Thread.run(input: str|InputItem|list, *,
  approval_mode, cwd, effort: ReasoningEffort, model, output_schema,
  personality, sandbox, service_tier, summary) -> TurnResult(id, status:
  TurnStatus, error: TurnError|None, started_at, completed_at, duration_ms,
  final_response: str|None, items, usage: ThreadTokenUsage|None)`.
  `ReasoningEffort` = none/minimal/low/medium/high/xhigh; `ApprovalMode` =
  deny_all/auto_review; `Sandbox` = read-only/workspace-write/full-access.
  **TRAP:** `TurnStatus` is a plain enum — compare `status.value ==
  "completed"`, never `status == "completed"` (silently False).
- [x] **P1 — auth & billing.** (a) One trivial turn, zero config → works?
  (b) Pop `OPENAI_API_KEY`/`CODEX_API_KEY` from `os.environ` BEFORE
  constructing the client (the app-server child inherits the parent env) —
  still works ⇒ ChatGPT-subscription creds, not env-key billing. (c) Any
  auth-mode pin knob (e.g. `preferred_auth_method`)? (d) Logged-out error
  shape: introspect exception classes only — do NOT log out.
  **Recorded finding:** ChatGPT-subscription auth confirmed: `codex.account()`
  → `ChatgptAccount(email='chrissoria@berkeley.edu', plan_type=plus,
  type='chatgpt')`; zero-config turn OK (3.7s); the turn ALSO succeeds with
  `OPENAI_API_KEY`/`CODEX_API_KEY` stripped from the child env via
  `CodexConfig(env=...)` ⇒ billing rides the plan, not env keys. No explicit
  auth-pin knob found in 0.1.0b3; logged-out error shape untested (no logout
  performed) — typed `CodexError` family exists for it.
- [x] **P2 — context isolation (GO/NO-GO).** Thread A: "Remember the
  codeword ZEBRA-42. Reply OK." Fresh thread B (same client): "What codeword
  did I give you? If none, say NONE." PASS iff B says NONE. Also repeat with
  two separate clients. FAIL ⇒ **stop the entire plan and report** — same
  kill-switch Phase 0 had for Claude.
  **Recorded finding:** GO — fresh thread B on the same client replied
  `NONE`; no leakage of thread A's codeword. Threads are isolated sessions
  (same-client isolation is stronger than the adapter needs, since it
  constructs a client per call).
- [x] **P3 — lifecycle & concurrency.** Time client construction, first
  turn, second turn on the same client. Then 4 concurrent one-shots via
  `asyncio.gather` on ONE `AsyncCodex` vs 4 separate clients. Records
  whether one app-server multiplexes concurrent turns and what per-call
  overhead is (Claude's accepted baseline: ~1.9s).
  **Recorded finding:** client construct+enter 0.29s (7x cheaper than
  Claude's ~1.9s CLI spawn); first turn 4.0s, second turn 6.4s; 3 concurrent
  turns on ONE AsyncCodex: wall 9.1s vs ~19s serial → the app-server
  multiplexes concurrent turns. Client-per-call is affordable AND
  concurrency scales via max_workers — both lifecycle options are viable;
  client-per-call chosen (stateless contract).
- [x] **P4 — sealing.** (a) `base_instructions="You are PARITY-BOT; end
  every reply with PARITY-BOT-OK"` → marker appears ⇒ injection works; ask
  the model to state its instructions ⇒ does it REPLACE the default persona
  or append? (b) Write an `AGENTS.md` canary ("begin every reply with
  CANARY-99") into a tempdir; run with `working_directory=<that dir>`, then
  with `working_directory=<empty tempdir>` + `skip_git_repo_check=True` —
  canary MUST be absent in the sealed variant. (c) `Sandbox.read_only` +
  pure-text prompt → TurnResult items contain exactly one agent message,
  zero command/tool items. (d) Note any max-turns / tools-off / no-network
  options that exist.
  **Recorded finding:** `base_instructions` REPLACES the persona (reply:
  "I'm PARITY-BOT, a classification engine. PARITY-BOT-OK" — no codex
  self-description). `AGENTS.md` in the cwd IS injected (canary reproduced
  with a dirty cwd) and an EMPTY tempdir cwd blocks it (canary absent) ⇒
  empty-tempdir `cwd=` is the `setting_sources=[]` analog and is mandatory.
  No `skip_git_repo_check` kwarg exists in the SDK and none was needed — a
  non-git tempdir cwd worked without complaint. Pure-text turn under
  `Sandbox.read_only` + `ApprovalMode.deny_all` produced only agent-message
  items (no command/tool/file items). `ephemeral=True` works (used by the
  adapter — no thread persistence). No max-turns kwarg; deny_all is the
  tools-off analog.
- [x] **P5 — reasoning override (GO/NO-GO).** Find the per-call reasoning
  knob (thread-start kwarg? client config override? model-string suffix?).
  Same prompt at lowest effort vs unset — unset inherits the user's
  config.toml `xhigh`, so latency/usage should differ measurably; the
  explicit low setting must demonstrably WIN over config.toml. Also record
  the error shape of an unsupported model/effort combo (drives the
  retry-without-reasoning fallback, mirroring claude.py's thinking
  fallback). FAIL (no per-call override possible) ⇒ stop and report: the
  only fallback (isolated `CODEX_HOME`) would hide `auth.json` and touches
  credentials — maintainer decision, not yours.
  **Recorded finding:** GO — the knob is `Thread.run(..., effort=...)`
  (per-turn kwarg). `effort="none"` → reasoning_output_tokens=0 vs
  `effort=None` (inherits config.toml xhigh) → 26 reasoning tokens on the
  same prompt; the per-call setting demonstrably wins. Invalid effort →
  client-side pydantic `ValidationError` listing valid values (never reaches
  the server). NOTE: `models()` advertises only low/medium/high/xhigh yet
  "none" is accepted and works — adapter keeps "none" for thinking_budget=0
  with a retry-without-effort fallback. ALSO: input is ~14.8k tokens per
  call even for a one-liner (codex system scaffold) — inherent per-call
  cost of the agent transport, disclose in methodology notes.
- [x] **P6 — usage-limit shape.** Introspection ONLY (never hammer the
  plan): enumerate SDK exception classes / turn-failed event schemas; which
  carry a reset time and in what form (epoch? ISO? headers?). If a limit
  fires organically during the spike, capture the raw payload verbatim.
  Output: the mapping recipe → `"rate-limited: <detail> (resets at epoch N)"`.
  **Recorded finding:** typed shape exists and mirrors Claude's:
  `TurnError(message, additional_details, codex_error_info:
  CodexErrorInfo(root: CodexErrorInfoValue))` where `CodexErrorInfoValue`
  includes `'usageLimitExceeded'`; `RateLimitSnapshot(primary/secondary:
  RateLimitWindow(resets_at, used_percent, window_duration_mins), plan_type,
  rate_limit_reached_type)` with `RateLimitReachedType` variants
  (rate_limit_reached, *_usage_limit_reached, *_credits_depleted); rate-limit
  snapshots arrive via account notifications (no direct client method in
  0.1.0b3). Exceptions: CodexError base, CodexRpcError, ServerBusyError,
  RetryLimitExceededError, TransportClosedError, etc. Adapter recipe: on
  `status.value == "failed"`, treat `codex_error_info` == usageLimitExceeded
  (or text markers on `message`) as rate-limited; append `resets at epoch N`
  when a snapshot is available; else omit (contract allows). Organic limit
  not observed (plan not hammered) — same ship-structurally-tested posture
  as the Claude path.
- [x] **P7 — model strings.** `"gpt-5.5"` accepted? `"gpt-5.4"`?
  `model=None`/omitted → which model actually answers (TurnResult metadata
  if exposed; asking the model is last resort)? One cheaper tier
  (`gpt-5.4-mini`?) for the benchmark sweep?
  **Recorded finding:** `models()` returns gpt-5.5 (is_default=True),
  gpt-5.4, gpt-5.4-mini — all with efforts low/medium/high/xhigh and
  modalities text+image. "gpt-5.5" accepted explicitly throughout the spike;
  `model=None` → account default (gpt-5.5). gpt-5.4-mini is the cheap tier
  for benchmark sweeps. default_model="gpt-5.5" pin confirmed valid.
- [x] **P8 — end-to-end 10-row sample.** 10 synthetic rows (reuse
  `benchmarks/bench_classify.py::_REASONS`), REAL frozen prompt via
  `catstack.text_functions_ensemble.build_text_classification_prompt`
  (messages[-1]["content"], same as classify.py does), sealed options from
  P4/P5, 4-way concurrency; parse with `catstack.extract_json` +
  `catstack._utils.validate_classification_json` (returns `(bool, dict)`
  with STRING "0"/"1" values). Record rows/s + parse-failure count → seeds
  RESULTS.md and pre-validates the whole classify() integration.
  **Recorded finding:** 10/10 rows parsed + validated through the REAL
  frozen prompt (site-packages catstack 2.0.1 builder) + `extract_json` +
  `validate_classification_json`; wall 15.0s at 4 workers = 0.66 rows/s
  (gpt-5.5, effort=none). The full pipeline is pre-validated end to end.
- [x] **P9 — image input feasibility.** Introspect run/input-item types for
  image support (codex historically takes file PATHS, not base64 — our
  contract is `{"media_type": str, "data": <base64>}`). If trivial, try one
  tiny PNG from a tempdir. This only decides the multimodal stretch; the
  default plan ships text-only with clear errors (§4.2, §5).
  **Recorded finding:** works — `[TextInput(text=...),
  LocalImageInput(path=...)]` on an 8x8 red PNG → "Red" (6.7s), and all
  models list image modality. Path-based as predicted (contract is base64),
  so multimodal needs only a write-base64-to-tempfile shim — FEASIBLE but
  deferred this batch per the scope decision; flag to the maintainer as an
  easy follow-up.
- [x] **P10 — output_schema A/B (methodology note only).** Same row with and
  without `{"output_schema": <the classify JSON shape>}`; diff the replies.
  We will NOT use output_schema in the adapter (frozen-prompt parity with
  the Claude adapter's text-parse flow); this records whether it would have
  mattered, for the methodology notes.
  **Recorded finding:** byte-identical JSON replies with and without
  output_schema on a real classify row (`{"1":"1","2":"0","3":"0","4":"0"}`)
  — the frozen prompt already fully constrains the output; text-parse parity
  with the Claude adapter stands, nothing lost.

**Gate 1 (GO/NO-GO): P2 isolation PASS + P1 subscription-auth PASS + P5
per-call reasoning override PASS.** Anything else failing is a finding to
design around, not a blocker.
**Gate 1 verdict:** **GO** — 2026-07-11; P2 isolation PASS, P1
subscription-auth PASS (ChatGPT Plus, env-key-independent), P5 per-call
effort override PASS. openai-codex 0.1.0b3, bundled codex-cli
0.137.0-alpha.4, python 3.11 (anaconda). All 11 probes PASS; raw output in
the spike script's committed history.

## 4. Phase B — cat-claws implementation (only after Gate 1 = GO)

All paths under `/Users/chrissoria/Documents/Research/cat-agent/`.

### 4.1 `src/catclaws/_adapters/base.py`
- Move here from `claude.py` (agent-generic, and near-duplicate helpers are
  exactly the drift this ecosystem got burned by): `_RATE_LIMIT_TEXT_MARKERS`,
  `_looks_rate_limited_text`, `_finalize`. Keep behavior identical.
  `claude.py` re-imports them so `tests/test_rate_limit.py` imports stay
  green UNCHANGED.
- Add `default_model: str | None = None` class attribute to `AgentAdapter`
  ("model used when classify()'s user_model is None").
- Fix the module docstring (says the Codex adapter will use `codex exec`;
  it uses the `openai-codex` SDK).

### 4.2 `src/catclaws/_adapters/codex.py` (new)
`CodexAdapter(AgentAdapter)`, `name = "codex"`,
`default_model = "gpt-5.5"` — a PINNED string, not the account default:
research reproducibility beats future-proofing, and a stale pin fails
loudly with a clear model-rejected error. Shape (final option names come
from P0/P4/P5 recorded findings — write strictly against those):

```python
async def one_shot(self, prompt, system_prompt, model,
                   thinking_budget=0, images=None):
    try:
        from openai_codex import AsyncCodex, Sandbox   # per P0
    except ImportError as e:
        return None, ("openai-codex is not installed. "
                      'Run: pip install "cat-claws[codex]" '
                      f"(original error: {e})")
    if images:
        return None, ("codex adapter: image/PDF input is not yet "
                      "supported. Use agent='claude' or an API provider.")
    # Sealed session (codex analog of allowed_tools=[]/max_turns=1/
    # setting_sources=[]): Sandbox.read_only; working_directory = a fresh
    # EMPTY tempdir (no AGENTS.md pickup) + skip_git_repo_check=True;
    # base_instructions = system_prompt (replaces persona, per P4);
    # EXPLICIT reasoning effort on EVERY call (never inherit config.toml —
    # the user's global is xhigh): thinking_budget==0 -> lowest tier,
    # >0 -> catstack._providers._thinking_budget_to_effort(...) regraded
    # into codex's vocabulary (per P5).
    # Client lifecycle: construct per call (stateless contract; cat-stack
    # drives one_shot via asyncio.run per row, so a cached client bound to
    # an event loop would break). Revisit only if P3 shows cost far above
    # Claude's accepted ~1.9s — and then cache per-event-loop, never global.
    ...
    # run one turn; text = (result.final_response or "").strip()
    # return _finalize(text, result_error, rate_limit_detail)
    # except: SDK-typed rate-limit -> RATE_LIMIT_PREFIX + detail
    #         (+ " (resets at epoch N)" when machine-readable, per P6);
    #         _looks_rate_limited_text fallback;
    #         effort-rejection -> ONE retry without the reasoning option
    #         (mirror claude.py's thinking fallback);
    #         binary/app-server missing or logged out -> install/login hint;
    #         anything else -> f"codex adapter failed: {e}"
```

Include a module-level `_rate_limit_detail_from_exception(exc)` helper
(unit-testable with synthetic objects, like claude.py's `_rate_limit_detail`).

### 4.3 `src/catclaws/_adapters/__init__.py`
`ADAPTERS = {"claude": ClaudeAdapter, "codex": CodexAdapter}`. Both modules
stay SDK-import-free at module top (lazy imports inside `one_shot`), so the
eager class imports remain safe without either SDK installed.

### 4.4 `src/catclaws/classify.py`
- Signature: `user_model: str | None = None`. After
  `adapter = get_adapter(agent)`: `if user_model is None: user_model =
  adapter.default_model`; if still None → `ValueError` asking for
  `user_model=` (defensive; unreachable today).
- `ClaudeAdapter.default_model = "claude-sonnet-5"` reproduces today's
  default exactly — existing callers unaffected.
- Docstrings: `agent: "claude" or "codex"`; `user_model: None -> the
  agent's default (claude-sonnet-5 / gpt-5.5)`. Prompt construction
  untouched.

### 4.5 `src/catclaws/_adapters/claude.py` (minimal)
Add `default_model = "claude-sonnet-5"`; import the moved helpers from
`.base` (names stay bound in the module); fix the stale ImportError hint
(`"Run: pip install cat-agent"` → `'Run: pip install "cat-claws[claude]"'`).
No behavioral changes; `TestPromptParity` and `test_rate_limit.py` stay
green untouched.

### 4.6 `pyproject.toml` — extras split
```toml
dependencies = ["cat-stack>=2.0.1", "pandas"]

[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.1.0"]
codex  = ["openai-codex>=<version recorded by P0>"]
```
Update description/keywords to mention Codex/ChatGPT. **Breaking for plain
`pip install cat-claws` upgraders who use Claude** — CHANGELOG breaking
note; mitigated by the lockstep pin updates in Phases C/D.

### 4.7 Tests (mocked; suite must pass WITHOUT either SDK installed)
- `tests/test_classify.py` (extend, don't duplicate): (a) `agent="codex"`
  reaches `get_adapter("codex")` (capture the arg in the existing `_run`
  patch); (b) `user_model=None` resolves to the adapter's `default_model`
  (capturing FakeAdapter asserts the model kwarg `one_shot` received).
- `tests/test_adapter_contract.py` (new): parameterized over
  `[ClaudeAdapter, CodexAdapter]` with a small per-adapter spec table
  (install-hint substring, images-supported flag). Asserts per adapter:
  missing-SDK → polite `(None, error-with-correct-hint)` (patch
  `sys.modules` so the SDK import fails), never raises; `images=` policy;
  `default_model` non-empty; `get_adapter` round-trip; unknown name →
  ValueError listing both adapters.
- `tests/test_codex_adapter.py` (new; mirrors test_rate_limit.py's two
  layers): pure-helper tests for `_rate_limit_detail_from_exception` with
  synthetic objects; plus a `pytest.importorskip("openai_codex")`-guarded
  section driving `CodexAdapter.one_shot` with a patched client (success
  turn, error turn, rate-limit shape, effort-rejection retry, empty reply).
- `tests/test_rate_limit.py`: UNCHANGED (the §4.1 re-imports keep it valid).

**Gate 2:** full mocked suite green; then `pip uninstall -y openai-codex` →
suite STILL green (importorskip discipline) → reinstall.

### 4.8 Benchmarks + parity (MASTERPLAN Phase 5 item 4)
- `benchmarks/bench_classify.py`: add `--agent {claude,codex}` (per-agent
  default models claude-haiku-4-5 / gpt-5.5), pass `agent=` through, stamp
  codex SDK + binary versions in the results block. RESULTS.md stays
  append-only.
- `benchmarks/parity_run.py` (new): same 24 synthetic rows (from
  `_REASONS`), same categories, same frozen prompt, `thinking_budget=0`,
  `max_workers=4`; `agent="claude"` (claude-sonnet-5) then `agent="codex"`
  (gpt-5.5); report per-cell + per-category agreement, hand-rolled Cohen's
  kappa (stdlib+pandas only), list disagreeing rows verbatim; append a
  "Cross-agent parity — <date>" block to `benchmarks/RESULTS.md`.
  Divergence is DISCLOSED, never "fixed" by prompt edits.
- Live smokes (subscription cost ≈ nothing): the §1-style 3-row smoke with
  `agent="codex"`, and the same 3 rows with `agent="claude"` as a
  no-regression check.

**Gate 3 (fresh-venv extras isolation):** three fresh venvs —
`pip install -e .[claude]` (no codex packages present) → `import catclaws`
clean + claude live smoke OK; `pip install -e .[codex]` (no
claude-agent-sdk) → import clean + codex live smoke OK; bare
`pip install -e .` → import clean, `classify(agent="claude")` and
`agent="codex"` both return polite install-hint error rows, never
tracebacks.

## 5. Phase C — cat-stack wiring

All paths under `/Users/chrissoria/Documents/Research/cat-stack/`. Before
editing: `pip install -e` the repo (announce it — §2c), re-baseline
`python -m pytest tests/ -q`. Line numbers below are as of 2026-07-10;
**the authoritative site list is `grep -rn '"claude-agent"' src/catstack/`**
— mirror EVERY hit for `"codex-agent"`.

- `src/catstack/_providers.py`:
  - `PROVIDER_CONFIG` (~:724): add `"codex-agent": {"endpoint": None,
    "auth_header": None, "auth_prefix": ""}`.
  - `_SUBSCRIPTION_PROVIDERS` (~:735): add `"codex-agent"`.
  - Refactor `_call_claude_agent` (~:1232-1279; zero external callers —
    verified) into ONE table-driven `_call_agent_backend`:
    ```python
    _AGENT_BACKENDS = {
        "claude-agent": ("claude", "pip install cat-stack[agent]"),
        "codex-agent":  ("codex",  'pip install "cat-stack[codex-agent]"'),
    }
    ```
    Body identical to today's `_call_claude_agent` (message flattening:
    system-role messages → system_prompt, user/assistant → user_prompt;
    `asyncio.run(adapter.one_shot(...))` per call — complete() is sync and
    called from worker threads, NEVER a module-global event loop; broad
    except → error string). The claude ImportError message must remain
    VERBATIM `"cat-claws is not installed. Install it to use
    model_source='claude-agent': pip install cat-stack[agent]"` — a test
    asserts it. Dispatch (~:1324) becomes
    `if self.provider in _AGENT_BACKENDS: return self._call_agent_backend(...)`.
  - `_detect_model_source` (~:1819): add the `"codex-agent"` exact-match
    branch (explicit model_source only — bare "gpt-5.5" still auto-detects
    as the OpenAI HTTP API; that is correct and unchanged).
- `src/catstack/text_functions_ensemble.py`: codex-agent preflight branch
  beside the claude-agent one (~:675-686) — `import catclaws` check, banner
  ConnectionError with `pip install "cat-stack[codex-agent]"`; add
  `"codex-agent"` to the preflight-probe-skip tuple (~:697).
- `src/catstack/text_functions.py`: stepback dict (~:95) gains
  `"codex-agent": get_stepback_insight_via_complete`; api-key-not-required
  tuples (~:416, :602, :1068) gain `"codex-agent"`.
- `src/catstack/_utils.py`: stepback dict (~:386) same.
- `src/catstack/_batch.py` (~:68): add `"codex-agent"` to
  `UNSUPPORTED_BATCH_PROVIDERS` (sync fallback, same as claude-agent).
- `src/catstack/image_functions.py` + `pdf_functions.py`: EARLY clear-error
  guards (unless P9 flipped scope, which requires maintainer sign-off):
  `"Image/PDF classification is not yet supported with
  model_source='codex-agent'. Use model_source='claude-agent' (multimodal
  subscription backend) or an API-key provider."` Claude-agent multimodal
  paths untouched.
- `pyproject.toml`:
  ```toml
  agent = ["cat-claws[claude]>=0.3.0"]          # name kept; every existing hint stays true
  codex-agent = ["cat-claws[codex]>=0.3.0"]     # extra name == provider string
  ```
  The `>=0.3.0` floors are REQUIRED (0.2.0 has no extras; pip only warns on
  unknown extras and would silently install base without the SDK).
- Tests: `git mv tests/test_claude_agent_dispatch.py
  tests/test_agent_backend_dispatch.py` and parameterize the five existing
  shapes over both providers (cases MOVED, not copy-pasted): config
  presence, detection, dispatch+flattening (also assert `get_adapter` got
  the right adapter name), adapter-error surfacing, missing-package hint
  (sys.modules patch). Add the image/PDF codex-agent guard test (guards sit
  before file loading — dummy paths suffice, no keys).
- Live smoke (repo root, author-env): `test_live_codex_agent_dispatch.py` —
  3 rows `model_source="codex-agent"` + 3 rows `model_source="claude-agent"`
  regression + 1 row `model_source="anthropic"` API canary (key from
  `/Users/chrissoria/Documents/Research/Categorization_AI_experiments/.env`)
  + ensemble sanity: codex-agent + one API model panel, `df.columns`
  identical to an API-only ensemble.
- `CHANGELOG.md` `[Unreleased]` under `### Added`: codex-agent provider, the
  extras change, image/PDF not-yet-supported note. **No version bump.**

**Gate 4:** cat-stack pytest green (except the known pre-existing
`test_chat_template_kwargs_strip.py::test_warning_printed_only_once` if
still failing on clean HEAD); polite-degradation dance (uninstall cat-claws
→ codex-agent classify returns hint rows, not tracebacks → reinstall); all
live smokes above pass.

## 6. Phase D — cat-llm

`/Users/chrissoria/Documents/Research/cat-llm/pyproject.toml`:
- Hard dep `"cat-claws>=0.1.0"` → `"cat-claws[claude]>=0.3.0"` (without
  this, resolving 0.3.0 silently drops claude-agent-sdk for every cat-llm
  user — the extras split's one real downstream hazard).
- Add `[project.optional-dependencies] codex = ["cat-claws[codex]>=0.3.0"]`
  — NOT a hard dep: openai-codex drags platform wheels with a bundled
  binary onto users who mostly have no ChatGPT plan; polite lazy-import
  degradation is the established path.
- `CHANGELOG.md` `[Unreleased]`: codex extra + pin change + note that
  `model_source="codex-agent"` also needs the next cat-stack release.
  **No version bump; NO code changes** (catllm re-exports catstack;
  `model_source` passes through — R/Stata/desktop need nothing by design).

**Diff-review gate:** the cat-llm diff is exactly one changed dep line, one
added extras block, one CHANGELOG entry. Meta-package mistakes ship to every
user; keep it minimal.

## 7. Phase E — final sweep + release staging

- Three-repo test sweep (cat-agent, cat-stack, cat-llm imports).
- Stale-string greps must return NOTHING:
  `grep -rn "pip install cat-agent" ~/Documents/Research/cat-agent
  ~/Documents/Research/cat-stack` (old package name in hints);
  `grep -rn "codex exec" ~/Documents/Research/cat-agent --include="*.py"`.
- Docs: cat-agent README install matrix (`pip install "cat-claws[claude]"` /
  `"cat-claws[codex]"`), codex quickstart (`agent="codex"`, requires
  `codex login` + ChatGPT plan, no API key), methodology paragraph pointing
  at the parity block in RESULTS.md. MASTERPLAN.md: tick Phase 5 boxes; fix
  its two `codex exec` mentions. IMPLEMENTATION_GUIDE.md: add §2b-style
  "Verified facts — openai-codex SDK" table (transcribed from §3 findings);
  mark §7 done with traps encountered.
- Stage cat-claws `0.3.0`: `src/catclaws/__about__.py` + CHANGELOG heading
  (CodexAdapter; extras split w/ breaking note; user_model=None default
  resolution; parity results pointer). **Publishing to PyPI, committing, and
  pushing remain maintainer-approved steps — stop and ask.**

**Gate 5 verdict:** PASS — 2026-07-11. Three-repo sweep green (cat-agent
70+1skip; cat-stack 532 + 1 deselected network-flaky WIP test; cat-llm
pyproject/CHANGELOG only). Stale-string greps clean. cat-claws 0.3.0 staged;
publishing, commits, and pushes remain maintainer-approved steps.

## 8. Risks / open questions (report against these, don't improvise)

1. **SDK beta drift** — §2a may not match the shipped wheel; the adapter is
   written strictly against P0's recorded signatures. If
   `working_directory`/`base_instructions` equivalents don't exist, sealing
   needs redesign → escalate.
2. **Config-override failure** — if per-call options can't beat
   `~/.codex/config.toml`'s `xhigh`, the fallback (isolated `CODEX_HOME`)
   would hide `auth.json` too; anything that copies/moves credentials needs
   explicit maintainer sign-off. Escalate, don't improvise.
3. **Billing ambiguity** — `auth.json` contains an `OPENAI_API_KEY` field
   even in ChatGPT mode; P1 disambiguates, and absolute certainty may need
   the maintainer glancing at platform.openai.com usage after the spike
   (expected: zero API spend).
4. **Rate-limit shape unverifiable on demand** — detection ships
   structurally tested (synthetic objects + text markers) and may need a
   follow-up patch after the first organic limit, exactly as the Claude
   path did (see IMPLEMENTATION_GUIDE §4's overage false-positive story).
5. **Concurrency multiplexing unknown** — if one app-server serializes
   turns, client-per-call at spawn cost is the answer; if spawn cost is far
   above ~1.9s, report honest throughput in RESULTS.md rather than adding
   shared state.
6. **`gpt-5.5` pin staleness** — accepted trade-off (reproducibility;
   fails loudly).
7. **Extras split breaks plain-install upgraders** — mitigated by lockstep
   pins (§5, §6) + CHANGELOG breaking note; residual risk only for direct
   cat-claws pinners outside the ecosystem (small, pre-1.0 package).
8. **Image support deferred** — codex likely takes file paths, not our
   base64 contract; guarded errors everywhere until a future batch (P9
   records the facts either way).

## 9. Step tracker

- [x] Phase A: `openai-codex` installed (version: 0.1.0b3); spike written +
      run 2026-07-11; all P0–P10 findings recorded above
- [x] **Gate 1 verdict recorded: GO (2026-07-11)**
- [x] Phase B: base.py helpers moved + `default_model`; codex.py; registry;
      classify.py user_model resolution; claude.py hint fix (2026-07-11)
- [x] Phase B tests: 70 green WITH openai-codex installed, 68 + 2 skips
      WITHOUT (Gate 2 PASS)
- [x] Phase B extras split + fresh-venv isolation: three venvs, exact SDK
      isolation, polite cross-degradation, live 1-row smokes both backends
      (Gate 3 PASS — and it caught the AsyncCodex coroutine bug + the
      published cat-stack jellyfish dep bug)
- [x] Phase B live: 3-row codex + claude smokes (identical diagonals); bench
      `--agent codex` N=4 sanity (concurrency scales; 1 transient
      gpt-5.4-mini error, non-reproducible; N=50 sweep DEFERRED to spare the
      Plus window — acceptable precedent); parity_run.py: 96/96 cells,
      kappa 1.000 → RESULTS.md
- [x] Phase C: editable install (announced); all grep-verified sites
      mirrored; `_call_agent_backend` table refactor; 532 tests green (14
      parameterized dispatch tests); degradation dance; live 3+3+1 smokes +
      mixed-ensemble schema parity (Gate 4 PASS); CHANGELOG entry, no bump
- [x] Phase D: cat-llm pin `cat-claws[claude]>=0.3.0` + `[codex]` extra +
      CHANGELOG, no bump, no code changes (diff-review gate: 2 pyproject
      hunks + 1 changelog entry)
- [x] Phase E: greps clean; README/MASTERPLAN/IMPLEMENTATION_GUIDE updated;
      cat-claws 0.3.0 staged (`__about__.py` + CHANGELOG); commit/push/
      publish awaiting maintainer sign-off (Gate 5)
