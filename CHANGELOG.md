# Changelog

All notable changes to cat-agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
