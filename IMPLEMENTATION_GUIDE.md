# cat-agent — Implementation Guide (handoff document)

*Written 2026-07-03 for whoever (human or model) continues this project.
Read MASTERPLAN.md first for the why; this file is the how. If anything here
contradicts the code, trust the code and update this file.*

## 0. Ground rules — read before touching anything

1. **One row = one sealed, fresh-context agent call.** Never reuse a
   conversation across rows. Never put multiple rows in one prompt. This is
   a research-validity requirement the maintainer (Chris) set explicitly.
   If a change would violate it, stop and ask.
2. **The prompt is frozen.** Row prompts must be byte-identical to what
   `catstack.text_functions_ensemble.build_text_classification_prompt`
   produces. `tests/test_classify.py::TestPromptParity` enforces this — if
   that test fails, YOUR change is wrong, not the test. Never "improve" the
   prompt wording.
3. **Output schema is fixed**: DataFrame with `input_data`,
   `processing_status` ("success" or "error: ..."), and `category_N` 0/1
   columns (None on error rows). Same as `catstack.classify()`.
4. **One bad row never aborts a batch.** Errors are recorded per row.
5. **cat-stack never hard-depends on cat-agent.** Dispatch in cat-stack is a
   lazy import; distribution happens via the cat-llm meta-package.
6. Workflow conventions (from the maintainer's standing preferences):
   accumulate changes in CHANGELOG.md `[Unreleased]`; do NOT bump the
   version per change — one bump per release batch. Every substantive fix
   gets a live smoke test against the real agent, not just mocks. Prefer
   stdlib over new dependencies. For provider/SDK behavior claims: probe
   empirically before coding against them.

## 1. What exists and works (verified live 2026-07-03)

- Environment: macOS, anaconda python (`/Users/chrissoria/anaconda3/bin/python3`).
  `catagent` and `catstack` are installed editable in it. Claude Code CLI
  2.1.197 is installed and logged in (subscription auth — no API key needed
  for agent calls). `claude-agent-sdk` 0.2.110 is installed.
- `catagent.classify()` works end to end: 3 rows on `claude-sonnet-5`,
  `max_workers=3`, 4.4s total, correct matrix.
- Mocked tests: `cd ~/Documents/Research/cat-agent && python -m pytest tests/ -q`
  → 5 passing. Run them after every change.
- Live smoke (costs ~nothing on subscription, takes ~10s):

  ```bash
  python3 -c "
  import catagent
  df = catagent.classify(
      input_data=['I moved for a new job', 'Rent got too expensive', 'Closer to my parents'],
      categories=['Employment', 'Cost of living', 'Family', 'Other'],
      user_model='claude-sonnet-5', description='Why did you move?', max_workers=3)
  print(df.to_string()); assert (df.processing_status == 'success').all()"
  ```

## 2. Verified facts — do NOT re-derive, do NOT trust your training data

These were established by live probes on 2026-07-03 (sdk 0.2.110, CLI
2.1.197). Training-data knowledge of this SDK is likely stale.

| Fact | Consequence |
|---|---|
| Two `query()` one-shots share NO context | fresh-context-per-row design is sound |
| Process overhead ≈ 1.9s per one-shot; 4-way concurrency near-linear | throughput = concurrency; don't chase warm-process reuse |
| `ClaudeAgentOptions(output_format={"type":"json","schema":...})` is **silently ignored** — answer arrives as markdown text, no structured field | Phase 1 parses prompt-JSON via `catstack.extract_json`; re-probe `output_format` on every SDK upgrade before building Phase 3 |
| The agent **thinks by default** (ThinkingBlock observed on haiku) | adapter passes `thinking=ThinkingConfigDisabled(type="disabled")` at `thinking_budget=0` (engine parity) and `effort=<graded>` above 0 |
| `ThinkingConfigDisabled` is a TypedDict | `ThinkingConfigDisabled(type="disabled")` constructs a plain dict — fine |
| `catstack._utils.validate_classification_json(json_str, n)` returns a `(bool, dict)` TUPLE and the dict values are STRINGS "1"/"0" | unpack both; compare `str(v) == "1"` |
| `catstack.extract_json(reply)` returns a JSON *string* (handles fences/preambles) | feed its output to validate, don't json.loads the raw reply |
| `system_prompt` option REPLACES Claude Code's default agent persona | our `_SYSTEM_PROMPT` in classify.py is transport scaffolding, not part of the instrument |
| `setting_sources=[]` prevents CLAUDE.md/user-settings injection | never remove it — without it, running from inside a repo contaminates classifications |

## 3. Code map

```
src/catagent/
  __about__.py        version 0.0.1 — single source of truth (hatch reads it)
  __init__.py         exports classify
  _adapters/base.py   AgentAdapter.one_shot(prompt, system_prompt, model,
                      thinking_budget) -> (text|None, error|None)
  _adapters/claude.py ClaudeAdapter — sealed ClaudeAgentOptions; thinking
                      parity; CLINotFoundError -> friendly install message;
                      falls back to agent-default thinking if the explicit
                      disable errors
  _adapters/__init__.py  ADAPTERS registry + get_adapter(name)
  _backend.py         gather_bounded(coro_fns, max_workers) — sync->async
                      seam via asyncio.run; captures per-task exceptions
  classify.py         classify(input_data, categories, user_model, agent,
                      description, multi_label, thinking_budget,
                      max_workers, json_retries) -> DataFrame
tests/test_classify.py  FakeAdapter pattern for mocked tests — copy it for
                        new tests; TestPromptParity is the canary
```

Repo: github.com/chrissoria/cat-agent (PRIVATE until first PyPI release).
Commit style: imperative subject, body explains why, footer:
`Co-Authored-By: Claude ...` (see git log for examples). Push after commit.

## 3b. Standing sanity checks — bracket EVERY work session with these

Run before starting (baseline must be green — if it isn't, fix that first,
don't build on a broken base) and after every substantive change:

```bash
cd ~/Documents/Research/cat-agent
python -m pytest tests/ -q                  # ALL green, incl. TestPromptParity
python -c "import catagent; print(catagent.__version__)"   # imports clean
git status --short                          # only files YOU meant to touch
```

And once per session, the 3-row live smoke from §1 (~10s, subscription-only
cost). PASS = 3× "success" + correct 1s on the diagonal.

**Direction check (ask before each step, honestly):** does what I'm about to
do (a) keep one-row-sealed-calls, (b) leave the prompt byte-identical,
(c) keep the output schema unchanged, (d) avoid new hard dependencies? If
any answer is no → stop and surface it to the maintainer instead of coding.

## 4. Phase 2 — benchmarks + rate-limit handling (next up)

Goal: know how this behaves at realistic N and fail gracefully at limits.

1. **Benchmark script** (`benchmarks/bench_classify.py`, committed): classify
   N=50 short rows (generate synthetic one-liners; do NOT use real study
   data) on `claude-haiku-4-5` at max_workers ∈ {1, 4, 8}. Record wall time,
   rows/s, error count. Write results into the script's docstring or a
   `benchmarks/RESULTS.md` with date + CLI/SDK versions.

   ✓ **Sanity before the 50-row run:** run the script with N=4 first. PASS:
   4/4 success, wall time ≈ 5–15s at workers=4, and per-worker scaling
   visible (workers=1 clearly slower than workers=4). FAIL (e.g. workers=4
   not faster, or errors): stop — something regressed in `_backend.py`
   concurrency; do not burn a 50-row run to find out.

   ✓ **Sanity after:** rows/s at workers=8 should be ≥ workers=4's. If it's
   *worse*, you're likely hitting throttling — that's a finding, record it,
   don't "fix" the code.

2. **Rate-limit surfacing.** During Phase-0 probes a `RateLimitEvent`
   message type was observed in the stream (untriggered). In
   `_adapters/claude.py`, detect rate-limit conditions (probe: what does the
   SDK emit when throttled? check message types + ResultMessage fields) and
   return a distinguishable error string prefix, e.g.
   `"rate-limited: ..."` so classify() can react.

   ✓ **Sanity:** you cannot reliably trigger a real rate limit on demand —
   so the check is structural: unit-test the detection function against a
   synthetic RateLimitEvent/ResultMessage object. If you find yourself
   hammering the live API trying to trigger a real 429, stop — that's the
   wrong direction (and abuses the subscription).

3. **Backoff in classify()**: on a rate-limited row, sleep (exponential,
   start 30s — subscription windows are minutes-scale, not seconds) and
   retry up to 2 times BEFORE consuming json_retries. Keep per-row
   isolation: other in-flight rows continue.

   ✓ **Sanity:** mocked test with a fake clock / patched `asyncio.sleep` —
   the test suite must still finish in seconds. If tests now take minutes,
   you forgot to patch the sleep. Also: non-rate-limited rows in the same
   batch must complete WITHOUT waiting on the throttled row (assert via
   call-order in the fake adapter).

4. **Partial-results guarantee test**: mocked test where the adapter
   rate-limits every call — classify() must return a full DataFrame with
   `error: rate-limited...` statuses, never raise.

   ✓ **Sanity:** `len(df) == len(input_data)` exactly, and
   `TestPromptParity` still green (backoff logic must not have touched
   prompt construction).

5. Acceptance: mocked tests green; benchmark table committed; a live 50-row
   haiku run completes with 0 errors (or documented rate-limit behavior).

   ✓ **Phase-2 exit sanity:** re-run the §1 3-row sonnet-5 smoke one last
   time; diff `benchmarks/RESULTS.md` numbers against the Phase-1 baseline
   (1.5s/row effective at workers=3). Materially slower → find out why
   before checking the phase off.

## 5. Phase 3 — structured output (blocked until SDK supports it)

Re-probe on each SDK upgrade (takes 1 minute):

```bash
python3 /path/to/probe: send output_format={"type":"json","schema":{...}} and
inspect messages — see scratchpad/probe_agent_sdk.py pattern in git history
of this guide, or rewrite: if ResultMessage gains a structured field or the
text becomes bare JSON, it works.
```

If supported: add `output_format` to ClaudeAdapter behind a feature check,
keep extract_json as fallback. If not: leave Phase 3 alone.

✓ **Sanity (gate for even starting Phase 3):** the probe must show a
*machine-parseable* result — bare JSON text or a populated structured field.
"Markdown that mentions the right numbers" (what 0.2.110 produces) is a
FAIL; do not write any Phase-3 code against it. If implemented: run the same
3-row live smoke twice, once with structured output and once with the
extract_json fallback forced — both matrices must be identical.

## 6. Phase 4 — cat-stack + cat-llm integration

Dispatch lives in cat-stack; distribution in cat-llm (decision recorded in
MASTERPLAN). Mirror the existing `claude-code` branches exactly — anchors in
`cat-stack/src/catstack/_providers.py` (line numbers as of 2.0.1+):

- `PROVIDER_CONFIG["claude-code"]` (~line 719): add a sibling
  `"claude-agent"` entry (`endpoint: None`).
- `detect_provider` (~line 1742): add `model_source == "claude-agent"`.
- `complete()` dispatch (~line 1249): before payload build, add:
  ```python
  if self.provider == "claude-agent":
      try:
          from catagent._adapters import get_adapter
          from catagent._backend import gather_bounded
      except ImportError:
          return None, ("cat-agent is not installed. "
                        "Run: pip install cat-stack[agent]")
      ...  # build system/user text from messages (see _call_claude_cli
           # for the message-flattening pattern), run one sealed call
  ```
  NOTE: complete() is sync and called from worker threads; call the adapter
  via `asyncio.run` per call (gather_bounded pattern) — do NOT create a
  module-global event loop.
- `text_functions_ensemble.py` ~line 653: the `claude-code` validation
  branch (CLI availability check, api_key not required) — add
  `claude-agent` alongside it, checking catagent importability instead.
- pyproject: `[project.optional-dependencies] agent = ["cat-agent>=0.1.0"]`.
- cat-llm meta pyproject: add `cat-agent>=0.1.0` to `dependencies`.
- Tests: mocked test in cat-stack (`tests/test_claude_agent_dispatch.py`)
  patching catagent; live test: `catstack.classify(model_source="claude-agent")`
  3 rows. Ensemble test: one API model + claude-agent in a panel.
- Ecosystem rules: cat-stack release = CHANGELOG entry + version bump at
  batch end (see cat-stack/CLAUDE.md); cat-agent needs a PyPI release FIRST
  (flip repo public, `python -m build`, twine with PYPI_API_TOKEN from
  cat-stack/.env, TWINE_USERNAME=__token__).

✓ **Per-step sanity for Phase 4 (cat-stack is production code used by 6+
downstream packages — check after EVERY edit there, not at the end):**

1. After each cat-stack edit:
   `cd ~/Documents/Research/cat-stack && python -m pytest tests/ -q` —
   expected: everything green except the known pre-existing failure in
   `test_chat_template_kwargs_strip.py::test_warning_printed_only_once`
   (untracked WIP test, fails on clean HEAD too — NOT yours to fix).
   Any OTHER failure = your change broke the engine; revert and rethink.
2. The no-install path must degrade politely BEFORE testing the happy path:
   temporarily `pip uninstall -y cat-agent`, run
   `catstack.classify(model_source="claude-agent", ...)` → must return
   error rows mentioning `pip install cat-stack[agent]`, never a raw
   ImportError traceback. Reinstall (`pip install -e ~/Documents/Research/cat-agent`)
   and confirm the same call succeeds.
3. Regression canary: run one classify on a NORMAL provider
   (`model_source="anthropic"`, sonnet-5, creativity=0.3, 1 row) after the
   dispatch edits — the claude-agent branch must not have disturbed API
   routing.
4. Ensemble sanity: panel of claude-agent + one API model must produce
   consensus columns and per-model columns with no schema drift
   (compare `df.columns` against an API-only ensemble run).
5. cat-llm meta edit: `pip download cat-llm --no-deps -d /tmp/x` is NOT the
   check — the check is reading the diff: exactly one line added to
   `dependencies` in cat-llm/pyproject.toml. Meta-package mistakes ship to
   every user; keep the diff minimal and reviewed.

## 7. Phase 5 — Codex adapter (later)

Spike first, code second (`codex exec` non-interactive mode: auth story,
model flag, JSON/event output, sandbox flags, startup cost, context
isolation — same probe checklist as Phase 0). Implement
`_adapters/codex.py` against `AgentAdapter`; register in `ADAPTERS`;
`model_source="codex-agent"` in cat-stack; split extras
(`cat-agent[claude]` / `cat-agent[codex]`) so neither SDK is forced on
users of the other. Cross-agent parity run for methodology disclosure.

✓ **Sanity gates:** (1) Do not write `codex.py` until the spike proves
context isolation between `codex exec` calls — that probe result decides
whether the design is even possible, same as Phase 0 did for Claude.
(2) The Codex adapter must pass the SAME mocked test suite: parameterize
`tests/test_classify.py` over adapters rather than duplicating tests — if
you're copy-pasting the test file, wrong direction. (3) After the extras
split, `pip install cat-agent[claude]` in a fresh venv must work WITHOUT
any codex packages present (and vice versa) — import-time cross-adapter
leakage means `_adapters/__init__.py` needs lazier imports.

## 8. Traps encountered (so you don't repeat them)

- zsh: `echo ===` and unquoted `=` in commands expand weirdly; quote them.
- The experiments `.env` values are quoted; `export $(grep ...)` leaks the
  quotes. Use python-dotenv to read keys when shelling out.
- pandas/bottleneck UserWarning noise on every python start — filter with
  `grep -v pandas`, it is not an error.
- Live API keys live in
  `/Users/chrissoria/Documents/Research/Categorization_AI_experiments/.env`
  (ANTHROPIC_API_KEY, GOOGLE_API_KEY, ...). Agent calls need NO key.
- Do not edit `cat-stack/src/catstack/collapse_themes.py` — it carries the
  maintainer's uncommitted WIP. If you must build cat-stack dists, stash it
  first and pop after (see cat-stack releases in git history).
