# Changelog

All notable changes to cat-agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Initial scaffold: MASTERPLAN.md (design + step tracker), package skeleton.
- classify() v0: one-row-sealed calls, frozen prompt, bounded concurrency,
  JSON re-asks, wide 0/1 DataFrame output.
- Phase 2 — rate-limit handling: the Claude adapter now detects subscription
  limits from the SDK's real signals (a `RateLimitEvent` with `status ==
  "rejected"`, or `ResultMessage.api_error_status == 429`; `allowed_warning`
  is correctly treated as non-blocking) and surfaces them with a
  `"rate-limited: "` error prefix, including the reset time when known. classify()
  gains `rate_limit_retries` (default 2): a throttled row backs off exponentially
  (30s, 60s…) and re-asks on a separate budget from `json_retries`, with other
  in-flight rows unaffected. Rate-limiting never raises — every row returns a status.
  Reset-aware: when the reported reset is farther out than the backoff budget can
  bridge (e.g. a `five_hour` cap hours away), the row fails fast with the resumable
  message instead of sleeping through futile retries — live-verified cutting a
  capped 2-row run from ~200s to ~4.5s. Near/unknown resets still back off.
- Phase 2 — `benchmarks/bench_classify.py`: synthetic-data throughput
  benchmark across max_workers settings; results in `benchmarks/RESULTS.md`.
