# cat-agent classify() benchmark results

Environment (all runs below): macOS, `claude-agent-sdk` 0.2.110, Claude CLI
2.1.197, subscription auth (no API key). One row = one sealed, fresh-context
call; throughput comes from bounded concurrency (`max_workers`).

## Rate-limit behavior — live-verified 2026-07-03

The Phase-2 rate-limit path was validated against a **genuine** exhausted
subscription window (the day's live testing had spent the five-hour cap), which
is a stronger check than a synthetic 429:

- **Detection works on the real SDK signal.** A raw adapter call returned in
  3.7s with `rate-limited: five_hour limit reached (resets at epoch
  1783129200)` — parsed from a `RateLimitEvent` whose `RateLimitInfo.status`
  was `"rejected"`, type `five_hour`, with the real reset timestamp.
- **Fail-fast on hard caps.** The window reset ~2.7h out — far beyond any
  backoff. A 2-row `classify()` returned in **4.5s** (both rows a graceful
  `error: rate-limited: … (resets at epoch …)`), `len(df) == inputs`, no
  exception. *Before* the reset-aware refinement the same call would have
  spent ~30s+60s of futile backoff per row (~200s) before failing — the
  refinement cut that ~44×.
- **Partial-results guarantee held live**: every row got a status; nothing
  was dropped or raised.

Design note learned here: the plan assumed "minutes-scale" windows, but the
real cap is `five_hour`. Backoff+retry only helps transient/near throttles, so
classify() now fails fast when the reported reset is beyond its backoff budget
(and still backs off for near/unknown resets). See CHANGELOG + IMPLEMENTATION_GUIDE §4.

## Throughput

Clean throughput sweep (N=50, `claude-haiku-4-5`, workers ∈ {1,4,8}) is
**pending the subscription window reset** (18:40 PDT 2026-07-03) — it can't run
truthfully while every call is rejected. Reproduce after reset with:

```bash
python benchmarks/bench_classify.py --n 50 --write-results
```

Reference point from Phase 1 (2026-07-03, before the cap): 3 rows on
`claude-sonnet-5` at `max_workers=3` completed in 4.4s wall — **~1.5s/row
effective**, vs ~33s/row through the old sequential `claude -p` subprocess
shim. Concurrency scaling was near-linear in the Phase-0 spike (4 parallel
one-shots in ~3.6s vs ~1.9s process overhead each).

<!-- bench_classify.py --write-results APPENDS a timestamped run block below;
     it never overwrites the narrative above. -->
