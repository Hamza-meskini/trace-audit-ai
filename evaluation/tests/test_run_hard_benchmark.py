"""The corpus wrapper must use the target corpus's validator, not Nova's.

Runs in a subprocess because the wrapper rebinds module globals of the Nova
runners for the whole interpreter.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "evaluation" / "run_hard_benchmark.py"

STUB_VALIDATOR = textwrap.dedent(
    """
    from pathlib import Path

    def validate(root=None):
        return {"valid": True, "errors": [], "warnings": [], "counts": {"corpus": Path(root).name, "stage": "all"},
                "distributions": {}}

    def validate_requirements(root=None):
        return {"valid": True, "errors": [], "warnings": [], "counts": {"corpus": Path(root).name, "stage": "requirements"},
                "distributions": {}}
    """
)


class HardBenchmarkWrapperTests(unittest.TestCase):
    def _run(self, benchmark: Path, *args: str):
        environment = {**os.environ, "PYTHONUTF8": "1", "DATABRICKS_MLFLOW_TRACING_ENABLED": "false",
                       "DATABASE_BACKEND": "sqlite"}
        environment.pop("TRACEAUDIT_DOCUMENT_PARSER", None)
        return subprocess.run(
            [sys.executable, "-B", str(WRAPPER), "--benchmark-dir", str(benchmark), *args],
            cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=environment, timeout=300,
        )

    def _corpus(self, tmp: str, benchmark_id: str = "traceaudit-stub-corpus-v1") -> Path:
        root = Path(tmp) / "stub_corpus"
        (root / "documents").mkdir(parents=True)
        (root / "ground_truth.json").write_text(json.dumps({"benchmark_id": benchmark_id}), encoding="utf-8")
        (root / "validate_hard_benchmark.py").write_text(STUB_VALIDATOR, encoding="utf-8")
        return root

    def test_validate_only_uses_the_target_corpus_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = self._run(self._corpus(tmp), "--validate-only", "--results-dir", str(Path(tmp) / "out"))
        self.assertEqual(completed.returncode, 0, completed.stdout[-3000:] + completed.stderr[-3000:])
        payload = json.loads(completed.stdout[completed.stdout.index("{"):])
        self.assertEqual(payload["counts"], {"corpus": "stub_corpus", "stage": "all"})

    def test_live_run_requires_an_explicit_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = self._run(self._corpus(tmp), "--mode", "end-to-end", "--results-dir", str(Path(tmp) / "out"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("TRACEAUDIT_DOCUMENT_PARSER", completed.stdout + completed.stderr)

    def test_refuses_the_nova_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = self._run(self._corpus(tmp, "traceaudit-nova-end-to-end-v1"), "--validate-only")
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
