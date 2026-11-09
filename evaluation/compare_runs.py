"""Compare benchmark arms made of repeated live runs (evaluation/ACCURACY_PROTOCOL.md).

Each arm is a list of run folders (or results.json files) from the Nova runner
or evaluation/run_hard_benchmark.py. For every arm it reports per-run and
mean +/- SD accuracy, macro-F1, CONFLICT recall, auto-closes and unsafe
auto-closes (wrong SUPPORTED and wrong CONFLICT). For each pair of arms it
reports paired per-requirement flips, using each requirement's majority-correct
outcome across an arm's runs, with an exact two-sided sign test on the
discordant requirements. If the ground truth tags requirements, accuracy is
also broken down by tag.

    backend/venv/Scripts/python.exe -B evaluation/compare_runs.py \
        --arm baseline evaluation/results/runs/A evaluation/results/runs/B \
        --arm candidate evaluation/results/runs/C evaluation/results/runs/D \
        [--truth evaluation/hard_benchmark/ground_truth.json] [--out comparison.json]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

try:
    from evaluation.scoring import auto_close_safety, macro_f1 as _shared_macro_f1
except ImportError:  # run as a script from evaluation/
    from scoring import auto_close_safety, macro_f1 as _shared_macro_f1

CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN", "NOT_APPLICABLE"]


def _load(path: str) -> dict:
    target = Path(path)
    if target.is_dir():
        target = target / "results.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    data["_path"] = str(target)
    return data


def _macro_f1(pairs: list[tuple[str, str]]) -> float:
    return _shared_macro_f1(pairs, CLASSES)


def run_metrics(run: dict) -> dict:
    rows = run["requirements"]
    pairs = [(row["expected_status"], row["predicted_status"]) for row in rows]
    safety = auto_close_safety(
        (row["expected_status"], row["predicted_status"], row.get("predicted_review_state")) for row in rows
    )
    conflict = [row for row in rows if row["expected_status"] == "CONFLICT"]
    environment = (run.get("generation_config") or {}).get("environment") or {}
    return {
        "run_id": run.get("run_id"),
        "benchmark_id": run.get("benchmark_id"),
        "git_commit": (environment.get("git_commit") or "")[:10] or None,
        "git_dirty": environment.get("git_dirty"),
        "parser": environment.get("document_parser"),
        "runtime_s": run.get("runtime_seconds"),
        "n": len(rows),
        "correct": sum(gold == pred for gold, pred in pairs),
        "accuracy": round(100.0 * sum(gold == pred for gold, pred in pairs) / len(rows), 2) if rows else 0.0,
        "macro_f1": _macro_f1(pairs),
        "conflict_recall": f"{sum(r['predicted_status'] == 'CONFLICT' for r in conflict)}/{len(conflict)}",
        "auto_closed": safety["auto_closed"],
        "unsafe_supported": safety["unsafe_supported"],
        "unsafe_conflict": safety["unsafe_conflict"],
        "unsafe_auto_closes": safety["unsafe_auto_closes"],
        "not_extracted": sum(1 for r in rows if r["predicted_status"] == "NOT_EXTRACTED"),
    }


# EVA-08: settings that must match for two arms to be comparable. The code
# (git commit) is what an arm comparison is for, so it is not listed here.
SETTING_FIELDS = (
    "benchmark_version", "mode", "model", "atomic_model", "verification_model",
    "atomic_thinking_level", "verification_thinking_level", "verification_path",
)
GENERATION_FIELDS = ("provider", "temperature", "batch_size", "retrieval_backend")


def run_settings(run: dict) -> dict:
    generation = run.get("generation_config") or {}
    environment = generation.get("environment") or {}
    settings = {name: run.get(name) for name in SETTING_FIELDS}
    settings.update({name: generation.get(name) for name in GENERATION_FIELDS})
    settings["document_parser"] = environment.get("document_parser")
    return settings


def settings_mismatches(arms: dict[str, list[dict]], allowed: set[str]) -> list[str]:
    """Differences in recorded settings within an arm, or between arms outside ``allowed``."""
    problems = []
    reference = None
    for name, runs in arms.items():
        per_run = [run_settings(run) for run in runs]
        for index, current in enumerate(per_run[1:], start=2):
            diff = sorted(key for key in current if current[key] != per_run[0][key])
            if diff:
                problems.append(f"arm {name!r}: run {index} differs from run 1 in {', '.join(diff)}")
        if reference is None:
            reference = (name, per_run[0])
            continue
        diff = sorted(
            key for key in per_run[0]
            if per_run[0][key] != reference[1][key] and key not in allowed
        )
        if diff:
            details = "; ".join(f"{key}: {reference[1][key]!r} vs {per_run[0][key]!r}" for key in diff)
            problems.append(f"arms {reference[0]!r} and {name!r} differ in {details}")
    return problems


def _sign_test(fixed: int, broken: int) -> float:
    """Exact two-sided binomial test on discordant pairs (p = 0.5)."""
    n = fixed + broken
    if n == 0:
        return 1.0
    k = min(fixed, broken)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def _majority(runs: list[dict]) -> dict[str, dict]:
    outcome: dict[str, dict] = {}
    by_requirement = defaultdict(list)
    for run in runs:
        for row in run["requirements"]:
            by_requirement[row["requirement_id"]].append(row)
    for requirement_id, rows in by_requirement.items():
        votes = sum(row["predicted_status"] == row["expected_status"] for row in rows)
        outcome[requirement_id] = {
            "expected": rows[0]["expected_status"],
            "correct_runs": votes,
            "runs": len(rows),
            # None is a tie (even run count); use an odd number of runs per arm.
            "majority_correct": True if votes * 2 > len(rows) else (False if votes * 2 < len(rows) else None),
            "predictions": dict(Counter(row["predicted_status"] for row in rows)),
        }
    return outcome


def _tags(truth_path: str | None) -> dict[str, list[str]]:
    if not truth_path:
        return {}
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    tags = {}
    for requirement in truth.get("requirements", []):
        value = requirement.get("tags") or requirement.get("difficulty_tags") or []
        tags[requirement["requirement_id"]] = list(value) if isinstance(value, (list, tuple)) else [str(value)]
    return tags


def summarize_arm(name: str, runs: list[dict], tags: dict[str, list[str]]) -> dict:
    per_run = [run_metrics(run) for run in runs]
    accuracies = [item["accuracy"] for item in per_run]
    f1s = [item["macro_f1"] for item in per_run]
    summary = {
        "arm": name,
        "runs": per_run,
        "accuracy_mean": round(statistics.mean(accuracies), 2) if accuracies else None,
        "accuracy_sd": round(statistics.stdev(accuracies), 2) if len(accuracies) > 1 else None,
        "accuracy_range": [min(accuracies), max(accuracies)] if accuracies else None,
        "macro_f1_mean": round(statistics.mean(f1s), 2) if f1s else None,
        "unsafe_total": sum(item["unsafe_auto_closes"] for item in per_run),
        "auto_closed_mean": round(statistics.mean(item["auto_closed"] for item in per_run), 2) if per_run else None,
    }
    if tags:
        by_tag = defaultdict(lambda: [0, 0])
        for run in runs:
            for row in run["requirements"]:
                for tag in tags.get(row["requirement_id"], []) or ["untagged"]:
                    by_tag[tag][0] += row["predicted_status"] == row["expected_status"]
                    by_tag[tag][1] += 1
        summary["accuracy_by_tag"] = {
            tag: f"{good}/{total} ({100.0 * good / total:.0f}%)" for tag, (good, total) in sorted(by_tag.items())
        }
    by_class = defaultdict(lambda: [0, 0])
    for run in runs:
        for row in run["requirements"]:
            by_class[row["expected_status"]][0] += row["predicted_status"] == row["expected_status"]
            by_class[row["expected_status"]][1] += 1
    summary["recall_by_class"] = {label: f"{good}/{total}" for label, (good, total) in sorted(by_class.items())}
    return summary


def compare(reference: list[dict], candidate: list[dict]) -> dict:
    before, after = _majority(reference), _majority(candidate)
    fixed, broken = [], []
    for requirement_id in sorted(set(before) & set(after)):
        old, new = before[requirement_id], after[requirement_id]
        if old["majority_correct"] == new["majority_correct"]:
            continue
        line = (f"{requirement_id} (gold {new['expected']}): {old['correct_runs']}/{old['runs']} -> "
                f"{new['correct_runs']}/{new['runs']} correct; now {new['predictions']}")
        if new["majority_correct"] is True:
            fixed.append(line)
        elif old["majority_correct"] is True:
            broken.append(line)
    return {"fixed": len(fixed), "broken": len(broken), "net": len(fixed) - len(broken),
            "sign_test_p": _sign_test(len(fixed), len(broken)), "fixed_items": fixed, "broken_items": broken}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arm", nargs="+", action="append", metavar=("NAME", "RUN"), required=True,
                        help="arm name followed by its run folders or results.json files")
    parser.add_argument("--truth", help="ground_truth.json with per-requirement tags (optional)")
    parser.add_argument("--out", help="write the full comparison as JSON")
    parser.add_argument("--vary", action="append", default=[], metavar="SETTING",
                        help="a setting the arms are meant to differ in (e.g. verification_model); "
                             "any other difference makes the arms incomparable")
    args = parser.parse_args(argv)

    tags = _tags(args.truth)
    arms = {}
    for spec in args.arm:
        if len(spec) < 2:
            parser.error("--arm needs a name and at least one run")
        arms[spec[0]] = [_load(path) for path in spec[1:]]
    benchmark_ids = {run.get("benchmark_id") for runs in arms.values() for run in runs}
    if len(benchmark_ids) > 1:
        parser.error(f"runs come from different benchmarks: {sorted(map(str, benchmark_ids))}")
    mismatches = settings_mismatches(arms, set(args.vary))
    if mismatches:
        parser.error("incomparable arms (pass --vary SETTING for an intended difference):\n  "
                     + "\n  ".join(mismatches))

    report = {"benchmark_id": benchmark_ids.pop(), "arms": {}, "comparisons": {}}
    names = list(arms)
    for name in names:
        summary = summarize_arm(name, arms[name], tags)
        report["arms"][name] = summary
        sd = f" ± {summary['accuracy_sd']}" if summary["accuracy_sd"] is not None else ""
        print(f"[{name}] {len(arms[name])} runs: accuracy {summary['accuracy_mean']}%{sd} "
              f"(range {summary['accuracy_range']}), macro-F1 {summary['macro_f1_mean']}, "
              f"auto-closed {summary['auto_closed_mean']}/run, unsafe {summary['unsafe_total']}")
        for item in summary["runs"]:
            print(f"    {item['run_id']} {item['correct']}/{item['n']} ({item['accuracy']}%) F1 {item['macro_f1']} "
                  f"CONFLICT {item['conflict_recall']} auto {item['auto_closed']} unsafe "
                  f"{item['unsafe_supported']}+{item['unsafe_conflict']} commit {item['git_commit']}"
                  f"{' DIRTY' if item['git_dirty'] else ''} parser {item['parser']}")
        print(f"    recall by class: {summary['recall_by_class']}")
        if "accuracy_by_tag" in summary:
            print(f"    accuracy by tag: {summary['accuracy_by_tag']}")
    for reference in names:
        for candidate in names:
            if reference >= candidate and len(names) > 2:
                continue
            if reference == candidate or (len(names) == 2 and reference != names[0]):
                continue
            result = compare(arms[reference], arms[candidate])
            report["comparisons"][f"{reference} -> {candidate}"] = result
            print(f"\n{reference} -> {candidate}: fixed {result['fixed']}, broken {result['broken']}, "
                  f"net {result['net']:+d}, sign test p={result['sign_test_p']}")
            for line in result["fixed_items"]:
                print(f"    fixed : {line}")
            for line in result["broken_items"]:
                print(f"    broken: {line}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
