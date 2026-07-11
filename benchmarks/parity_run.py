"""Cross-agent parity run — Claude vs Codex on the same frozen prompt.

MASTERPLAN Phase 5 item 4 / methodology disclosure: the two subscription
backends answer the SAME synthetic rows through the SAME validated prompt at
thinking_budget=0. This script reports per-cell and per-category agreement
plus Cohen's kappa, lists every disagreeing row verbatim, and appends a
"Cross-agent parity" block to benchmarks/RESULTS.md.

Divergence is DISCLOSED, never "fixed" by prompt edits (the prompt is a
frozen instrument). Synthetic data only — never real study rows.

Usage:
    python benchmarks/parity_run.py                 # print only
    python benchmarks/parity_run.py --write-results # also append RESULTS.md
"""

import argparse
import os
import time
from datetime import datetime, timezone

import catclaws
from bench_classify import _REASONS, CATEGORIES, _versions

AGENT_MODELS = {"claude": "claude-sonnet-5", "codex": "gpt-5.5"}
N_ROWS = 24


def _rows():
    return [_REASONS[i % len(_REASONS)] for i in range(N_ROWS)]


def _cohen_kappa(a, b):
    """Cohen's kappa for two equal-length binary (0/1) vectors — stdlib only."""
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n
    pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if pe == 1.0:
        return 1.0  # both raters constant and identical
    return (po - pe) / (1 - pe)


def run_agent(agent, rows):
    model = AGENT_MODELS[agent]
    print(f"\n[{agent}] classifying {len(rows)} rows on {model} ...")
    t0 = time.perf_counter()
    df = catclaws.classify(
        rows,
        CATEGORIES,
        user_model=model,
        agent=agent,
        description="Why did you move?",
        thinking_budget=0,
        max_workers=4,
    )
    dt = time.perf_counter() - t0
    errors = int((df["processing_status"] != "success").sum())
    print(f"[{agent}] {dt:.1f}s, errors={errors}")
    return df, dt, errors


def compare(rows, df_a, df_b, name_a, name_b):
    cat_cols = [f"category_{i + 1}" for i in range(len(CATEGORIES))]
    ok = (df_a["processing_status"] == "success") & (
        df_b["processing_status"] == "success"
    )
    per_cat = {}
    all_a, all_b = [], []
    for col, cat in zip(cat_cols, CATEGORIES):
        a = [int(v) for v in df_a.loc[ok, col]]
        b = [int(v) for v in df_b.loc[ok, col]]
        agree = sum(1 for x, y in zip(a, b) if x == y)
        per_cat[cat] = {
            "agreement": agree / len(a) if a else float("nan"),
            "kappa": _cohen_kappa(a, b),
        }
        all_a += a
        all_b += b
    overall = {
        "cells": len(all_a),
        "agreement": (
            sum(1 for x, y in zip(all_a, all_b) if x == y) / len(all_a)
            if all_a
            else float("nan")
        ),
        "kappa": _cohen_kappa(all_a, all_b),
        "rows_compared": int(ok.sum()),
    }
    disagreements = []
    for idx in df_a.index[ok]:
        diff_cats = [
            cat
            for col, cat in zip(cat_cols, CATEGORIES)
            if int(df_a.loc[idx, col]) != int(df_b.loc[idx, col])
        ]
        if diff_cats:
            disagreements.append(
                {
                    "row": rows[idx],
                    "cats": diff_cats,
                    name_a: [int(df_a.loc[idx, c]) for c in cat_cols],
                    name_b: [int(df_b.loc[idx, c]) for c in cat_cols],
                }
            )
    return overall, per_cat, disagreements


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-results", action="store_true")
    args = ap.parse_args()

    rows = _rows()
    df_claude, dt_c, err_c = run_agent("claude", rows)
    df_codex, dt_x, err_x = run_agent("codex", rows)

    overall, per_cat, disagreements = compare(
        rows, df_claude, df_codex, "claude", "codex"
    )

    print(
        f"\n=== parity: {overall['rows_compared']}/{len(rows)} rows compared, "
        f"{overall['cells']} cells ==="
    )
    print(
        f"overall agreement {overall['agreement']:.1%}, "
        f"kappa {overall['kappa']:.3f}"
    )
    for cat, m in per_cat.items():
        print(f"  {cat:<16} agreement {m['agreement']:.1%}  kappa {m['kappa']:.3f}")
    if disagreements:
        print(f"\n{len(disagreements)} disagreeing rows:")
        for d in disagreements:
            print(f"  {d['row']!r}: {d['cats']} claude={d['claude']} codex={d['codex']}")
    else:
        print("\nno disagreeing rows")

    if args.write_results:
        sdk_c, cli_c = _versions("claude")
        sdk_x, cli_x = _versions("codex")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "",
            f"## Cross-agent parity — {stamp}",
            "",
            f"- {N_ROWS} synthetic rows, frozen prompt, thinking_budget=0, max_workers=4",
            f"- claude: `{AGENT_MODELS['claude']}` ({sdk_c}; {cli_c}) — {dt_c:.1f}s, {err_c} errors",
            f"- codex: `{AGENT_MODELS['codex']}` ({sdk_x}; {cli_x}) — {dt_x:.1f}s, {err_x} errors",
            "",
            f"- Overall: {overall['agreement']:.1%} cell agreement, kappa {overall['kappa']:.3f} "
            f"({overall['rows_compared']} rows, {overall['cells']} cells)",
            "",
            "| category | agreement | kappa |",
            "|---|---:|---:|",
        ]
        for cat, m in per_cat.items():
            lines.append(f"| {cat} | {m['agreement']:.1%} | {m['kappa']:.3f} |")
        if disagreements:
            lines += ["", "Disagreeing rows (claude vs codex, per-category 0/1):", ""]
            for d in disagreements:
                lines.append(
                    f"- {d['row']!r} — {', '.join(d['cats'])}: "
                    f"claude={d['claude']} codex={d['codex']}"
                )
        else:
            lines += ["", "No disagreeing rows.", ""]
        lines.append("")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RESULTS.md")
        with open(out, "a") as f:
            f.write("\n".join(lines))
        print(f"\nappended parity block to {out}")


if __name__ == "__main__":
    main()
