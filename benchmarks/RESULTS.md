# cat-agent classify() benchmark results

Environment (all runs below): macOS, `claude-agent-sdk` 0.2.110, Claude CLI
2.1.197, subscription auth (no API key). One row = one sealed, fresh-context
call; throughput comes from bounded concurrency (`max_workers`).

## Rate-limit detection — bug found and fixed 2026-07-03

An honest correction to an earlier version of this file: the Phase-2 rate-limit
path first *appeared* to be validated against a "genuine exhausted window," but
that was a **false positive in our own detection**, caught when the maintainer
pointed out his subscription was only 74% used and still working.

Dumping the raw SDK payload showed the truth — the call was **succeeding**:

```
status          = 'allowed'      # primary five_hour window is fine
overage_status  = 'rejected'     # overage BILLING is off...
overage_disabled_reason = 'org_level_disabled'   # ...at the org level (a config)
isUsingOverage  = False
AssistantMessage text = '{"1":1}'      # the request went through
ResultMessage subtype = 'success', is_error = False
```

`_rate_limit_detail` had treated *any* `overage_status=='rejected'` as a limit,
so it aborted every successful call and mislabeled it `rate-limited`, then
backed off. This would break cat-agent on **every call** for accounts with
org-disabled overage — common in institutional/university subscriptions, i.e.
exactly the target research audience.

**Fix:** detection now keys only on the primary `status=='rejected'` (the
window actually being exhausted), ignores `overage_status` (spillover billing
config, not a per-request block), and `_finalize` always returns a successful
answer over an informational limit event. Regression tests:
`test_org_disabled_overage_still_returns_answer`,
`test_overage_rejected_with_allowed_primary_is_not_a_limit`,
`test_finalize_answer_wins_over_limit_event`.

The backoff + reset-aware fail-fast **mechanism** is retained and still correct
for a *genuine* primary-window exhaustion (`status=='rejected'` with a reset
hours out) — it simply no longer false-fires on the overage config.

Live confirmation after the fix: a 3-row `classify()` on `claude-haiku-4-5`
(max_workers=3) returned **3/3 success** with the correct 0/1 matrix in 4.3s
(~1.4s/row), against the maintainer's live, actively-used subscription.

## Throughput

Full N=50 sweep across workers ∈ {1,4,8} is **not yet run** — deferred to avoid
consuming the maintainer's actively-used window; best run against a fresh
window. Reproduce with:

```bash
python benchmarks/bench_classify.py --n 50 --write-results
```

Reference from Phase 1 (and re-confirmed post-fix): ~1.4–1.5s/row effective on
`claude-sonnet-5`/`claude-haiku-4-5` at `max_workers=3`, vs ~33s/row through the
old sequential `claude -p` subprocess shim. Concurrency scaling was near-linear
in the Phase-0 spike (4 parallel one-shots in ~3.6s vs ~1.9s process overhead
each).

<!-- bench_classify.py --write-results APPENDS a timestamped run block below;
     it never overwrites the narrative above. -->
