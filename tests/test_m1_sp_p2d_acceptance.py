from __future__ import annotations

from pathlib import Path
import unittest

from digital_pulse.m1_p2_acceptance import run_m1_p2_acceptance


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "m1_sp" / "p2d_golden.json"


class M1SPP2DAcceptanceTests(unittest.TestCase):
    def test_formal_acceptance_matrix(self):
        result = run_m1_p2_acceptance(
            golden_path=GOLDEN,
            software_commit_sha="c" * 40,
            source_root=ROOT / "src",
            workspace_clean=True,
        )
        self.assertTrue(result["acceptance"], result["failed_gates"])
        self.assertEqual(result["failed_gates"], [])
        self.assertEqual(result["scenario_registry"]["total_case_count"], 18)
        self.assertTrue(result["replay"]["verified"])
        self.assertTrue(result["determinism"]["verified"])
        for key in (
            "quality_schema_valid",
            "oracle_isolation_verified",
            "direct_replay_equivalent",
            "deterministic_repeat_match",
            "golden_summaries_match",
            "software_sha_tracked",
            "engineering_unit_interface_valid",
            "d3_regression_passed",
            "m1_p1_regression_passed",
        ):
            self.assertIs(result[key], True, key)


if __name__ == "__main__":
    unittest.main()
