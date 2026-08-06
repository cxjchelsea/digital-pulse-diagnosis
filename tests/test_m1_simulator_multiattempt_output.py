from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from digital_pulse.m1_simulator import (
    M1SessionRecorder,
    ReplayDataSource,
    get_attempt_plan,
)


FIXED_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class M1SimulatorMultiAttemptOutputTests(unittest.TestCase):
    def test_retry_improves_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = get_attempt_plan("retry_improves", random_seed=1001, duration_s=0.3)
            result = M1SessionRecorder(software_commit_sha=FIXED_SHA).record_plan(plan, output_root=root)
            plan_dir = result.plan_path
            self.assertTrue((plan_dir / "plan.json").is_file())
            self.assertTrue((plan_dir / "expected.json").is_file())
            self.assertEqual(len(result.attempt_results), 2)
            self.assertEqual(plan.attempts[0].scenario_id, "weak_signal")
            self.assertEqual(plan.attempts[1].scenario_id, "normal_high_quality")
            ids = [item.session_id for item in result.attempt_results]
            self.assertEqual(len(set(ids)), 2)
            for item in result.attempt_results:
                self.assertTrue(item.session_path.name.startswith("attempt-"))
                samples = list(ReplayDataSource(item.session_path).samples())
                self.assertGreater(len(samples), 0)
                self.assertEqual(samples[0].session_id, item.session_id)
            expected = json.loads((plan_dir / "expected.json").read_text(encoding="utf-8"))
            self.assertEqual(expected["expected_quality_label"], "acceptable")
            self.assertEqual(expected["expected_int_action"], "accept")
            self.assertTrue(expected["analysis_allowed"])
            self.assertTrue(expected["not_algorithm_output"])

    def test_retry_still_fails_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = get_attempt_plan("retry_still_fails", random_seed=1001, duration_s=0.3)
            result = M1SessionRecorder(software_commit_sha=FIXED_SHA).record_plan(plan, output_root=root)
            self.assertEqual(len(result.attempt_results), 3)
            self.assertTrue(all(a.scenario_id == "weak_signal" for a in plan.attempts))
            attempts_root = result.plan_path / "attempts"
            self.assertEqual(len(list(attempts_root.iterdir())), 3)
            self.assertFalse(any(path.name.startswith("attempt-04") for path in attempts_root.iterdir()))
            ids = [item.session_id for item in result.attempt_results]
            self.assertEqual(len(set(ids)), 3)
            for item in result.attempt_results:
                list(ReplayDataSource(item.session_path).samples())
            expected = json.loads((result.plan_path / "expected.json").read_text(encoding="utf-8"))
            self.assertEqual(expected["expected_int_action"], "reposition")
            self.assertFalse(expected["analysis_allowed"])
            self.assertIn("RETRY_LIMIT_REACHED", expected["expected_reason_codes"])


if __name__ == "__main__":
    unittest.main()
