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


# Per-agent benchmark defaults: the cheap tier of each subscription.
_DEFAULT_BENCH_MODEL = {"claude": "claude-haiku-4-5", "codex": "gpt-5.4-mini"}


def _versions(agent="claude"):
    if agent == "codex":
        try:
            sdk = "openai-codex " + __import__("openai_codex").__version__
        except Exception:
            sdk = "?"
        try:
            import os
            import codex_cli_bin
            bin_path = os.path.join(
                os.path.dirname(codex_cli_bin.__file__), "bin", "codex"
            )
            cli = subprocess.run(
                [bin_path, "--version"], capture_output=True, text=True, timeout=15
            ).stdout.strip() + " (bundled)"
        except Exception:
            cli = "?"
        return sdk, cli
    try:
        sdk = "claude-agent-sdk " + __import__("claude_agent_sdk").__version__
    except Exception:
        sdk = "?"
    try:
        cli = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except Exception:
        cli = "?"
    return sdk, cli


def run(n, workers_list, model, agent="claude", description="Why did you move?"):
    rows = make_rows(n)
    results = []
    for w in workers_list:
        t0 = time.perf_counter()
        df = catclaws.classify(
            rows, CATEGORIES, user_model=model, agent=agent,
            description=description, max_workers=w,
        )
        dt = time.perf_counter() - t0
        errors = int((df["processing_status"] != "success").sum())
        rps = n / dt if dt else float("inf")
        results.append({"workers": w, "wall_s": dt, "rows_per_s": rps, "errors": errors})
        print(f"  workers={w:>2}   {dt:7.1f}s   {rps:5.2f} rows/s   errors={errors}")
    return results


def write_results(path, n, model, results, agent="claude"):
    """Append a timestamped run block to RESULTS.md (never overwrite — the
    file also holds a hand-written narrative worth keeping)."""
    sdk, cli = _versions(agent)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "",
        f"## Throughput run — {stamp}",
        "",
        f"- Agent: `{agent}`  |  Model: `{model}`  |  rows: {n}  |  synthetic 'reason for moving' data",
        f"- SDK: {sdk}  |  CLI: {cli}",
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
    ap.add_argument("--agent", default="claude", choices=["claude", "codex"])
    ap.add_argument("--model", default=None,
                    help="default: the agent's cheap tier "
                         f"({_DEFAULT_BENCH_MODEL})")
    ap.add_argument("--write-results", action="store_true",
                    help="write benchmarks/RESULTS.md")
    args = ap.parse_args()
    workers_list = [int(x) for x in args.workers.split(",") if x.strip()]
    model = args.model or _DEFAULT_BENCH_MODEL[args.agent]

    print(f"Benchmarking classify(): n={args.n}, agent={args.agent}, "
          f"model={model}, workers={workers_list}")
    results = run(args.n, workers_list, model, agent=args.agent)
    if args.write_results:
        import os
        out = os.path.join(os.path.dirname(__file__), "RESULTS.md")
        write_results(out, args.n, model, results, agent=args.agent)


if __name__ == "__main__":
    main()
