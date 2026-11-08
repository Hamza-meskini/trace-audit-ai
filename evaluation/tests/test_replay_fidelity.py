"""Fidelity gate for the offline verification replay (evaluation/ACCURACY_PROTOCOL.md).

Unmodified code must reproduce every saved verdict of the five replayable Nova
runs without touching the network. The saved runs live in the git-ignored
evaluation/results/runs folder, so the test skips when they are absent.

The replay runs in a subprocess because it replaces httpx and socket entry
points with tripwires for the whole interpreter.
"""

import json
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


@unittest.skipUnless(
    all((RUNS_DIR / run_id / "results.json").exists() for run_id in FIXTURES),
    "saved Nova runs are not present (evaluation/results is git-ignored)",
)
class ReplayFidelityTests(unittest.TestCase):
    def test_current_code_reproduces_every_saved_verdict_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "replay.json"
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--quiet", "--label", "fidelity", "--out", str(out)],
                cwd=tmp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout[-4000:] + completed.stderr[-4000:])
            report = json.loads(out.read_text(encoding="utf-8"))

        self.assertTrue(report["integrity_ok"], report["integrity"])
        self.assertEqual(report["integrity"]["network_attempts"], 0)
        self.assertEqual(report["integrity"]["unexpected_calls"], 0)
        summary = report["summary"]
        self.assertEqual(summary["instances"], 240)
        self.assertEqual(summary["status_matches_saved"], 240)
        self.assertEqual(summary["review_matches_saved"], 240)
        self.assertEqual(summary["unsafe_false_auto_closes"], 0)


if __name__ == "__main__":
    unittest.main()
