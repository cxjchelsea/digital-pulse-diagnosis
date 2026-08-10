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
            software_revision="c" * 40,
            source_root=ROOT / "src",
            workspace_clean=True,
        )
        self.assertTrue(result["formal_acceptance"], result["failed_gates"])
        self.assertEqual(result["failed_gates"], [])
        self.assertEqual(result["scenario_registry"]["total_case_count"], 18)
        self.assertTrue(result["replay"]["verified"])
        self.assertTrue(result["determinism"]["verified"])


if __name__ == "__main__":
    unittest.main()
