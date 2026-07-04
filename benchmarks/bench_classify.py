"""Throughput benchmark for catclaws.classify().

Live, subscription-backed. Classifies N SYNTHETIC one-liner rows (never real
study data) on a haiku-class model at several max_workers settings and reports
wall time, rows/s, and error count per setting.

Usage:
    # Sanity first — 4 rows, prove concurrency scales before spending a 50-row run:
    python benchmarks/bench_classify.py --n 4
    # Full run, writing benchmarks/RESULTS.md:
    python benchmarks/bench_classify.py --n 50 --write-results

The N=4 gate (from IMPLEMENTATION_GUIDE.md §4): expect 4/4 success, and
workers=4 clearly faster than workers=1. If not, stop — something regressed in
_backend.py concurrency; don't burn a 50-row run to find out.
"""

import argparse
import subprocess
import time
from datetime import datetime, timezone

import catclaws

CATEGORIES = ["Employment", "Cost of living", "Family", "Other"]

# Synthetic survey-style responses to "Why did you move?" — short, realistic,
# and deterministic so runs are comparable. NOT from any real dataset.
_REASONS = [
    "I moved for a new job",
    "Rent got too expensive here",
    "Wanted to be closer to my parents",
    "My company relocated my position",
    "Looking for a lower cost of living",
    "To take care of my aging mother",
    "Better job opportunities in the city",
    "Housing prices forced us out",
    "Family reasons, my kids live there",
    "Just needed a change of scenery",
]


def make_rows(n):
    return [_REASONS[i % len(_REASONS)] for i in range(n)]


def _versions():
    try:
        sdk = __import__("claude_agent_sdk").__version__
    except Exception:
        sdk = "?"
    try:
        cli = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except Exception:
        cli = "?"
    return sdk, cli


def run(n, workers_list, model, description="Why did you move?"):
    rows = make_rows(n)
    results = []
    for w in workers_list:
        t0 = time.perf_counter()
        df = catclaws.classify(
            rows, CATEGORIES, user_model=model,
            description=description, max_workers=w,
        )
        dt = time.perf_counter() - t0
        errors = int((df["processing_status"] != "success").sum())
        rps = n / dt if dt else float("inf")
        results.append({"workers": w, "wall_s": dt, "rows_per_s": rps, "errors": errors})
        print(f"  workers={w:>2}   {dt:7.1f}s   {rps:5.2f} rows/s   errors={errors}")
    return results


def write_results(path, n, model, results):
    """Append a timestamped run block to RESULTS.md (never overwrite — the
    file also holds a hand-written narrative worth keeping)."""
    sdk, cli = _versions()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "",
        f"## Throughput run — {stamp}",
        "",
        f"- Model: `{model}`  |  rows: {n}  |  synthetic 'reason for moving' data",
        f"- claude-agent-sdk: {sdk}  |  Claude CLI: {cli}",
        "",
        "| max_workers | wall time | rows/s | errors |",
        "|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['workers']} | {r['wall_s']:.1f}s | {r['rows_per_s']:.2f} | {r['errors']} |"
        )
    lines.append("")
    with open(path, "a") as f:  # append: preserve prior runs + the narrative
        f.write("\n".join(lines))
    print(f"\nappended run to {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--workers", default="1,4,8",
                    help="comma-separated max_workers values")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--write-results", action="store_true",
                    help="write benchmarks/RESULTS.md")
    args = ap.parse_args()
    workers_list = [int(x) for x in args.workers.split(",") if x.strip()]

    print(f"Benchmarking classify(): n={args.n}, model={args.model}, "
          f"workers={workers_list}")
    results = run(args.n, workers_list, args.model)
    if args.write_results:
        import os
        out = os.path.join(os.path.dirname(__file__), "RESULTS.md")
        write_results(out, args.n, args.model, results)


if __name__ == "__main__":
    main()
