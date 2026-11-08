"""Run another corpus in the Nova schema (by default evaluation/hard_benchmark)
through the unchanged Nova end-to-end runner.

The Nova runner and the extraction runner find their corpus through module
globals that they read at call time (BENCHMARK, DOCS, RESULTS, validate). This
wrapper points those globals at another benchmark folder and then calls the
Nova runner's own main(), so every flag works the same way:

    PYTHONUTF8=1 TRACEAUDIT_DOCUMENT_PARSER=databricks \
    backend/venv/Scripts/python.exe -B evaluation/run_hard_benchmark.py \
        --mode end-to-end --batch-size 4 --retrieval-backend local

Two traps it avoids:
- The corpus validator is loaded by file path under a unique module name.
  The Nova runner imports Nova's validator by bare module name, so importing
  another validator the same way would silently reuse Nova's.
- Results go to evaluation/results/<benchmark_id>/ (runs in its runs/
  subfolder), so Nova's latest result files are never overwritten.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARK = REPO / "evaluation" / "hard_benchmark"


def _load_validator(path: Path):
    spec = importlib.util.spec_from_file_location(f"_corpus_validator_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load validator {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("validate", "validate_requirements"):
        if not callable(getattr(module, name, None)):
            raise SystemExit(f"{path} must define {name}(root=None)")
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--benchmark-dir", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--validator", default=None,
                        help="validator file (default: <benchmark-dir>/validate_hard_benchmark.py)")
    parser.add_argument("--results-dir", default=None,
                        help="default: evaluation/results/<benchmark_id>")
    own, rest = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    benchmark = Path(own.benchmark_dir).resolve()
    truth_path = benchmark / "ground_truth.json"
    if not truth_path.exists():
        raise SystemExit(f"No ground_truth.json in {benchmark}")
    benchmark_id = json.loads(truth_path.read_text(encoding="utf-8"))["benchmark_id"]
    if not benchmark_id or benchmark_id.startswith("traceaudit-nova"):
        raise SystemExit("Use evaluation/run_nova_end_to_end_benchmark.py for Nova itself")
    validating_only = "--validate-only" in rest
    if not validating_only and not os.environ.get("TRACEAUDIT_DOCUMENT_PARSER"):
        # backend/.env selects databricks-auto, which silently falls back to a
        # local parser on any Databricks error, so one run could mix parsers.
        raise SystemExit("Set TRACEAUDIT_DOCUMENT_PARSER explicitly (for example 'databricks') for benchmark runs")

    validator = _load_validator(Path(own.validator) if own.validator else benchmark / "validate_hard_benchmark.py")
    slug = re.sub(r"[^a-z0-9]+", "-", benchmark_id.lower()).strip("-")
    results = Path(own.results_dir).resolve() if own.results_dir else REPO / "evaluation" / "results" / slug
    results.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(REPO))
    import evaluation.run_extraction_benchmark as extraction_runner
    import evaluation.run_nova_end_to_end_benchmark as end_to_end_runner

    for module in (extraction_runner, end_to_end_runner):
        module.BENCHMARK = benchmark
        module.DOCS = benchmark / "documents"
        module.RESULTS = results
    end_to_end_runner.validate = lambda: validator.validate(benchmark)
    extraction_runner.validate = lambda: validator.validate_requirements(benchmark)

    print(f">> Corpus: {benchmark_id} ({benchmark}) | results: {results}")
    sys.argv = [sys.argv[0], *rest]
    return end_to_end_runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
