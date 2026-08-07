from __future__ import annotations

import json
from pathlib import Path
import unittest

from digital_pulse.m1_sp.parameters import (
    P2B_CHARACTERIZATION_SEEDS,
    SP_PARAMETER_VERSION_P2A,
    SP_PARAMETER_VERSION_P2B,
    SP_PROCESSING_VERSION_P2A,
    SP_PROCESSING_VERSION_P2B,
    default_p2a_parameter_set,
    default_p2b_parameter_set,
)
from digital_pulse.m1_sp.processor import SPQualityProcessor

from _m1_sp_helpers import record_scenario

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "m1_sp" / "p2b_characterization.json"
P2A_DIGEST = "f546f8910d45df71faaaf6569d861de39ae991e63beed175ff5b7c5ad0040f1c"

TARGET_CASES = {
    "normal_high_quality": "acceptable",
    "weak_signal": "weak_signal",
    "no_contact": "no_contact",
    "upper_saturation": "saturated",
    "lower_saturation": "saturated",
    "baseline_drift": "unstable_baseline",
    "motion_artifact": "motion_artifact",
    "unstable_load": "manual_review_required",
    "insufficient_duration": "insufficient_duration",
}


class M1SPP2BCharacterizationTests(unittest.TestCase):
    def test_p2a_digest_unchanged(self):
        self.assertEqual(default_p2a_parameter_set().configuration_digest, P2A_DIGEST)
        self.assertEqual(default_p2a_parameter_set().parameter_version, SP_PARAMETER_VERSION_P2A)
        self.assertEqual(default_p2a_parameter_set().processing_version, SP_PROCESSING_VERSION_P2A)

    def test_p2b_versions_differ_and_have_simulation_thresholds(self):
        p2a = default_p2a_parameter_set()
        p2b = default_p2b_parameter_set()
        self.assertEqual(p2b.parameter_version, SP_PARAMETER_VERSION_P2B)
        self.assertEqual(p2b.processing_version, SP_PROCESSING_VERSION_P2B)
        self.assertNotEqual(p2a.configuration_digest, p2b.configuration_digest)
        self.assertTrue(p2b.get("weak_signal_std_max_raw").value is not None)
        self.assertIsNone(p2b.get("pulse_amplitude_threshold").value)

    def test_multi_seed_stability(self):
        proc = SPQualityProcessor()
        for case, label in TARGET_CASES.items():
            for seed in P2B_CHARACTERIZATION_SEEDS:
                duration = 1.0 if case == "insufficient_duration" else 8.0
                tmp, _path, session, samples = record_scenario(case, duration_s=duration, random_seed=seed)
                try:
                    out = proc.process(session, samples)
                    self.assertEqual(out.processing_status, "quality_evaluated", (case, seed))
                    self.assertEqual(out.quality_results[0].label.value, label, (case, seed))
                finally:
                    tmp.cleanup()

    def test_fixture_exists_with_seed_set_and_thresholds(self):
        self.assertTrue(FIXTURE.exists(), "run scripts/characterize_m1_p2b_quality.py")
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(payload["seed_set"], list(P2B_CHARACTERIZATION_SEEDS))
        self.assertEqual(payload["parameter_version"], SP_PARAMETER_VERSION_P2B)
        self.assertIn("thresholds", payload)
        self.assertIn("metric_formula_versions", payload)
        names = {item["parameter"] for item in payload["thresholds"]}
        self.assertIn("weak_signal_std_max_raw", names)
        self.assertIn("motion_metric_max", names)


if __name__ == "__main__":
    unittest.main()
