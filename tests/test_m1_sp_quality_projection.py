from __future__ import annotations

import math
import unittest

from digital_pulse.m1_contracts import ParameterStatus, QualityLabel
from digital_pulse.m1_sp.parameters import (
    SP_PARAMETER_VERSION_P2B,
    SP_PROCESSING_VERSION_P2B,
    default_p2b_parameter_set,
)
from digital_pulse.m1_sp.processor import SPQualityProcessor
from digital_pulse.m1_sp.quality import sort_reason_codes

from _m1_sp_helpers import record_scenario


class M1SPQualityProjectionTests(unittest.TestCase):
    def test_formal_projection_contract(self):
        proc = SPQualityProcessor()
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=8.0, random_seed=1001)
        try:
            out = proc.process(session, samples)
            self.assertEqual(len(out.quality_results), 1)
            result = out.quality_results[0]
            result.validate()
            result.validate_schema()
            self.assertIsNone(result.score)
            self.assertIsNone(result.confidence)
            self.assertEqual(result.parameter_status, ParameterStatus.SYNTHETIC_ONLY)
            self.assertEqual(result.processing_version, SP_PROCESSING_VERSION_P2B)
            self.assertEqual(result.parameter_version, SP_PARAMETER_VERSION_P2B)
            self.assertEqual(result.label, QualityLabel.ACCEPTABLE)
            self.assertEqual(result.reason_codes, ())
            for key in result.metrics:
                self.assertIn(
                    key,
                    {
                        "valid_fraction",
                        "clipping_fraction",
                        "baseline_drift_raw",
                        "pulse_std_raw",
                        "beat_count",
                        "ppg_match_rate",
                    },
                )
                self.assertTrue(math.isfinite(float(result.metrics[key])))
            self.assertNotIn("beat_count", result.metrics)
            self.assertNotIn("ppg_match_rate", result.metrics)
            self.assertNotIn("motion_metric", result.metrics)
            self.assertNotIn("load_std_raw", result.metrics)
            self.assertTrue(result.window_id.startswith("window-"))
        finally:
            tmp.cleanup()

    def test_integrity_projection_window(self):
        proc = SPQualityProcessor()
        tmp, _path, session, samples = record_scenario("frame_loss", duration_s=8.0, random_seed=1001)
        try:
            out = proc.process(session, samples)
            result = out.quality_results[0]
            result.validate_schema()
            self.assertEqual(result.window_id, "integrity-0001")
            self.assertEqual(result.valid_duration_s, 0.0)
            self.assertEqual(result.label, QualityLabel.DATA_INTEGRITY_FAILURE)
            self.assertEqual(result.metrics, {})
        finally:
            tmp.cleanup()

    def test_reason_code_order_stable(self):
        ordered = sort_reason_codes(["manual_review_requested", "too_short", "crc_errors", "too_short"])
        self.assertEqual(ordered, ("too_short", "crc_errors", "manual_review_requested"))

    def test_p2b_parameter_status_and_versions(self):
        profile = default_p2b_parameter_set()
        self.assertEqual(profile.processing_version, SP_PROCESSING_VERSION_P2B)
        self.assertEqual(profile.parameter_version, SP_PARAMETER_VERSION_P2B)
        self.assertTrue(any(p.parameter_class.value == "simulation_only" for p in profile.parameters))
        self.assertTrue(
            all(
                p.value is None
                for p in profile.parameters
                if p.parameter_class.value == "pending_h1_calibration"
            )
        )
        self.assertFalse(any(p.parameter_class.value == "frozen_h1" for p in profile.parameters))


if __name__ == "__main__":
    unittest.main()
