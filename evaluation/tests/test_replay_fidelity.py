"""Fidelity gate for the offline verification replay (evaluation/ACCURACY_PROTOCOL.md).

Unmodified code must reproduce every saved verdict of the five replayable Nova
runs without touching the network. The saved runs live in the git-ignored
evaluation/results/runs folder, so the test skips when they are absent.

The replay runs in a subprocess because it replaces httpx and socket entry
points with tripwires for the whole interpreter.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "evaluation" / "replay_verification.py"
RUNS_DIR = REPO / "evaluation" / "results" / "runs"
FIXTURES = (
    "20260915T140120.543292Z",
    "20260915T222056.820191Z",
    "20260916T133251.054900Z",
    "20260922T115355.064325Z",
    "20260922T123239.778672Z",
)


FIXTURES_DIR = REPO / "evaluation" / "fixtures" / "replay"


def _run_available(run_id: str) -> bool:
    return (RUNS_DIR / run_id / "results.json").exists() or (FIXTURES_DIR / f"{run_id}.results.json.gz").exists()


@unittest.skipIf(
    os.environ.get("TRACEAUDIT_REPLAY_IN_SEPARATE_JOB") == "1",
    "CI runs the replay gate as its own (non-blocking) job",
)
@unittest.skipUnless(
    all(_run_available(run_id) for run_id in FIXTURES),
    "saved Nova runs are not present (neither evaluation/results nor evaluation/fixtures/replay)",
)
class ReplayFidelityTests(unittest.TestCase):
    def _replay(self, *args: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "replay.json"
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--quiet", "--label", "fidelity", "--out", str(out), *args],
                cwd=tmp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout[-4000:] + completed.stderr[-4000:])
            return json.loads(out.read_text(encoding="utf-8"))

    def test_derived_status_simulation_stays_offline_and_is_labelled(self):
        report = self._replay("--simulate", "derived-status")
        self.assertTrue(report["integrity_ok"], report["integrity"])
        self.assertEqual(report["integrity"]["network_attempts"], 0)
        self.assertEqual(report["simulate"], "derived-status")
        self.assertGreater(report["simulated_label_changes"], 0)

    def test_current_code_reproduces_every_expected_verdict_offline(self):
        """Every row matches its saved verdict, or the ledger entry that explains the change.

        evaluation/replay_expectations.json lists each intended change with its
        reason and commit. A change missing from the ledger, a ledger entry the
        code no longer produces (for example after reverting a fix) and an entry
        whose reason is still TODO all fail.
        """
        report = self._replay()
        self.assertIsNone(report["simulate"])
        self.assertEqual(report["simulated_label_changes"], 0)
        self.assertTrue(report["integrity_ok"], report["integrity"])
        self.assertEqual(report["integrity"]["network_attempts"], 0)
        self.assertEqual(report["integrity"]["unexpected_calls"], 0)
        summary = report["summary"]
        expectations = report["expectations"]
        self.assertEqual(summary["instances"], 240)
        self.assertTrue(expectations["ok"], json.dumps(
            {k: expectations[k] for k in ("unexplained", "stale", "todo_reasons")}, indent=1))
        self.assertEqual(expectations["matches_expected"], 240)
        self.assertEqual(summary["unsafe_false_auto_closes"], 0)
        self.assertEqual(summary["wrong_conflict_auto_closes"], 0)


if __name__ == "__main__":
    unittest.main()
