from __future__ import annotations

import json
from pathlib import Path
import unittest

from digital_pulse.m1_sp.parameters import (
    P2A_CONFIGURATION_DIGEST,
    P2B_CONFIGURATION_DIGEST,
    P2C_CHARACTERIZATION_SEEDS,
    SP_PARAMETER_VERSION_P2A,
    SP_PARAMETER_VERSION_P2B,
    SP_PARAMETER_VERSION_P2C,
    SP_PROCESSING_VERSION_P2A,
    SP_PROCESSING_VERSION_P2B,
    SP_PROCESSING_VERSION_P2C,
    default_p2a_parameter_set,
    default_p2b_parameter_set,
    default_p2c_parameter_set,
)
from digital_pulse.m1_sp.processor import create_p2c_processor

from _m1_sp_helpers import record_scenario

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "m1_sp" / "p2c_characterization.json"

TARGET_LABELS = {
    "normal_high_quality": "acceptable",
    "weak_signal": "weak_signal",
    "motion_artifact": "motion_artifact",
    "baseline_drift": "unstable_baseline",
    "insufficient_duration": "insufficient_duration",
    "ppg_misalignment": "reference_mismatch",
}


class M1SPP2CCharacterizationTests(unittest.TestCase):
    def test_p2a_p2b_digests_unchanged(self):
        self.assertEqual(default_p2a_parameter_set().configuration_digest, P2A_CONFIGURATION_DIGEST)
        self.assertEqual(default_p2b_parameter_set().configuration_digest, P2B_CONFIGURATION_DIGEST)
        self.assertEqual(default_p2a_parameter_set().parameter_version, SP_PARAMETER_VERSION_P2A)
        self.assertEqual(default_p2a_parameter_set().processing_version, SP_PROCESSING_VERSION_P2A)
        self.assertEqual(default_p2b_parameter_set().parameter_version, SP_PARAMETER_VERSION_P2B)
        self.assertEqual(default_p2b_parameter_set().processing_version, SP_PROCESSING_VERSION_P2B)

    def test_p2c_versions_and_digest_distinct(self):
        p2b = default_p2b_parameter_set()
        p2c = default_p2c_parameter_set()
        self.assertEqual(p2c.parameter_version, SP_PARAMETER_VERSION_P2C)
        self.assertEqual(p2c.processing_version, SP_PROCESSING_VERSION_P2C)
        self.assertNotEqual(p2b.configuration_digest, p2c.configuration_digest)
        self.assertEqual(p2c.get("min_peak_prominence_raw").parameter_class.value, "simulation_only")
        self.assertIsNone(p2c.get("pulse_amplitude_threshold").value)

    def test_multi_seed_label_stability(self):
        proc = create_p2c_processor()
        for case, label in TARGET_LABELS.items():
            for seed in P2C_CHARACTERIZATION_SEEDS:
                duration = 1.0 if case == "insufficient_duration" else 8.0
                tmp, _path, session, samples = record_scenario(case, duration_s=duration, random_seed=seed)
                try:
                    out = proc.process(session, samples)
                    self.assertEqual(out.processing_status, "quality_evaluated", (case, seed))
                    self.assertEqual(out.quality_results[0].label.value, label, (case, seed))
                finally:
                    tmp.cleanup()

    def test_normal_match_rate_and_misalignment_separation(self):
        proc = create_p2c_processor()
        for seed in P2C_CHARACTERIZATION_SEEDS:
            tmp_n, _, s_n, samples_n = record_scenario("normal_high_quality", duration_s=8.0, random_seed=seed)
            tmp_m, _, s_m, samples_m = record_scenario("ppg_misalignment", duration_s=8.0, random_seed=seed)
            try:
                n = proc.process(s_n, samples_n).quality_results[0]
                m = proc.process(s_m, samples_m).quality_results[0]
                self.assertGreaterEqual(n.metrics["beat_count"], 4)
                self.assertGreaterEqual(n.metrics["ppg_match_rate"], 0.7)
                self.assertEqual(m.label.value, "reference_mismatch")
                self.assertLess(m.metrics["ppg_match_rate"], 0.7)
            finally:
                tmp_n.cleanup()
                tmp_m.cleanup()

    def test_fixture_exists(self):
        self.assertTrue(FIXTURE.exists(), "run scripts/characterize_m1_p2c_beats_reference.py")
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(payload["seed_set"], list(P2C_CHARACTERIZATION_SEEDS))
        self.assertEqual(payload["parameter_version"], SP_PARAMETER_VERSION_P2C)
        self.assertEqual(payload["configuration_digest"], default_p2c_parameter_set().configuration_digest)
        self.assertIn("filter_config", payload)
        self.assertIn("thresholds", payload)


if __name__ == "__main__":
    unittest.main()
