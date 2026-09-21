#!/usr/bin/env python3
"""Re-score and aggregate run_evals.py result files into one table per model, and list every
failed run with what was missing, so a number in the manuscript can be traced to
the answer that produced it.

    uv run --group evals python tests/evals/summarize.py tests/evals/results/*.json

Runs are re-scored from their stored answers with the current scoring.py, so the
result files stay the raw record and a scoring fix never needs a re-run.
"""

import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parent))
from scoring import score  # noqa: E402

QUESTIONS = {q["id"]: q for q in json.loads((Path(__file__).parent / "questions.json").read_text())}


def main(paths):
    print(
        "| Model | Runs | Correct | Mean calls | Rejected | Paged when needed | "
        "Mean tool tokens | Mean prompt tokens | Mean s |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    failures = []
    for path in paths:
        d = json.load(open(path))
        runs = d["runs"]
        # Re-score from the stored answers so a scoring fix applies to old runs.
        for r in runs:
            r["missing"], r["forbidden"] = score(QUESTIONS[r["id"]], r["answer"])
            r["correct"] = not r["missing"] and not r["forbidden"]
        need = [r for r in runs if r["needs_paging"]]
        print(
            f"| {d['model']} | {len(runs)} | {sum(r['correct'] for r in runs)} "
            f"| {mean(r['tool_calls'] for r in runs):.2f} "
            f"| {sum(r['rejected_calls'] for r in runs)} "
            f"| {sum(r['paged'] for r in need)}/{len(need)} "
            f"| {mean(r['tool_result_tokens'] for r in runs):.0f} "
            f"| {mean(r['prompt_tokens_reported'] for r in runs):.0f} "
            f"| {mean(r['seconds'] for r in runs):.1f} |"
        )
        failures += [(d["model"], r) for r in runs if not r["correct"]]
    print()
    for model, r in failures:
        print(
            f"FAIL {model} {r['id']} rep {r['repeat']}: missing {r['missing']} "
            f"forbidden {r['forbidden']} calls {r['tool_calls']}\n  answer: "
            f"{r['answer'][:300]!r}"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
