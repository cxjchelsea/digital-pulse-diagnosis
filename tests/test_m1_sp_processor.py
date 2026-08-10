from __future__ import annotations

import unittest

from digital_pulse.m1_sp import (
    P2C_CONFIGURATION_DIGEST,
    SP_PROCESSING_VERSION_P2D,
    EngineeringUnitConversionStatus,
    RawIdentityConverter,
    SyntheticCalibrationAdapter,
    SPProcessingProvenance,
    SPProcessingResult,
    SPProcessor,
)

from _m1_sp_helpers import FIXED_SHA, record_scenario


class SPProcessorTests(unittest.TestCase):
    def test_default_facade_returns_formal_p2c_result(self):
        tmp, _, session, samples = record_scenario(
            "normal_high_quality", duration_s=8.0, random_seed=1001
        )
        try:
            result = SPProcessor().process(
                session, samples, provenance=SPProcessingProvenance(FIXED_SHA)
            )
            self.assertIsInstance(result, SPProcessingResult)
            self.assertEqual(result.processing_status, "quality_evaluated")
            self.assertTrue(result.quality_results)
            self.assertEqual(result.parameter_digest, P2C_CONFIGURATION_DIGEST)
            self.assertEqual(result.software_commit_sha, FIXED_SHA)
            self.assertEqual(result.processing_version, SP_PROCESSING_VERSION_P2D)
            self.assertEqual(len(result.result_sha256), 64)
            second = SPProcessor().process(
                session, samples, provenance=SPProcessingProvenance("d" * 40)
            )
            self.assertEqual(result.parameter_digest, second.parameter_digest)
            self.assertEqual(result.quality_results, second.quality_results)
            self.assertEqual(result.result_sha256, second.result_sha256)
        finally:
            tmp.cleanup()

    def test_safety_block_has_no_quality_results(self):
        tmp, _, session, samples = record_scenario("abort", duration_s=8.0, random_seed=1001)
        try:
            result = SPProcessor().process(
                session, samples, provenance=SPProcessingProvenance(FIXED_SHA)
            )
            self.assertEqual(result.processing_status, "blocked_before_quality")
            self.assertEqual(result.quality_results, ())
        finally:
            tmp.cleanup()

    def test_provenance_and_engineering_unit_truthfulness(self):
        with self.assertRaises(ValueError):
            SPProcessingProvenance("short-sha")
        with self.assertRaises(ValueError):
            SPProcessingProvenance("A" * 40)
        conversion = SPProcessor().engineering_unit_conversion
        self.assertTrue(conversion.raw_identity)
        self.assertFalse(conversion.engineering_units_applied)
        self.assertTrue(conversion.real_calibration_pending)
        self.assertEqual(conversion.parameter_status.value, "pending_h1_calibration")
        self.assertEqual(
            conversion.conversion_status,
            EngineeringUnitConversionStatus.PENDING_H1_CALIBRATION,
        )
        pending = RawIdentityConverter().describe_load(12.5)
        self.assertEqual(pending.raw_value, 12.5)
        self.assertIsNone(pending.engineering_value)
        self.assertIsNone(pending.unit)
        synthetic = SyntheticCalibrationAdapter().describe_load(12.5)
        self.assertEqual(
            synthetic.conversion_status,
            EngineeringUnitConversionStatus.SYNTHETIC_ENGINEERING,
        )
        self.assertEqual(synthetic.unit, "synthetic_count")


if __name__ == "__main__":
    unittest.main()
