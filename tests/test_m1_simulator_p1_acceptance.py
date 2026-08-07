from __future__ import annotations

from pathlib import Path
import unittest

from digital_pulse.m1_simulator.acceptance import run_m1_p1_acceptance

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "m1_simulator" / "golden_summaries.json"


class M1SimulatorP1AcceptanceTests(unittest.TestCase):
    def test_formal_acceptance_against_golden(self):
        result = run_m1_p1_acceptance(golden_path=GOLDEN, d3_regression_passed=True)
        self.assertTrue(result.acceptance, result.failed_gates)
        self.assertEqual(result.failed_gates, ())
        self.assertEqual(result.total_cases, 18)
        self.assertEqual(result.single_attempt_cases, 16)
        self.assertEqual(result.multi_attempt_cases, 2)
        self.assertTrue(result.replay_verified)
        self.assertTrue(result.golden_summaries_verified)


if __name__ == "__main__":
    unittest.main()
