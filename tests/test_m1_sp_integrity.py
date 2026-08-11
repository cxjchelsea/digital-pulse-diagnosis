from __future__ import annotations

import unittest

from digital_pulse.m1_sp.models import IntegrityConsistency
from digital_pulse.m1_sp.processor import SPPreprocessor

from _m1_sp_helpers import record_scenario


class M1SPIntegrityTests(unittest.TestCase):
    def test_normal_high_quality(self):
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=0.4, random_seed=21)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            self.assertTrue(result.integrity.integrity_ok)
            self.assertFalse(result.integrity.pre_quality_blocked)
            self.assertEqual(result.integrity.crc_error_count, 0)
            self.assertEqual(result.integrity.sequence_error_count, 0)
            self.assertEqual(result.integrity.missing_frame_count, 0)
            self.assertEqual(result.integrity.timestamp_error_count, 0)
            error_codes = {e.code for e in result.integrity.evidence if e.severity == "error"}
            self.assertNotIn("FRAME_SEQUENCE_GAP", error_codes)
            self.assertNotIn("TIMESTAMP_ERROR", error_codes)
            self.assertIn(
                result.integrity.consistency,
                {IntegrityConsistency.CONSISTENT, IntegrityConsistency.RECORDED_SUPERSET},
            )
        finally:
            tmp.cleanup()

    def test_frame_loss(self):
        tmp, _path, session, samples = record_scenario("frame_loss", duration_s=0.5, random_seed=22)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            self.assertFalse(result.integrity.integrity_ok)
            self.assertGreater(result.integrity.sequence_error_count, 0)
            self.assertTrue(any(e.code == "FRAME_SEQUENCE_GAP" for e in result.integrity.evidence))
            # Must not renumber sequences
            self.assertEqual(
                list(result.normalized.frame_sequence),
                [s.frame_sequence for s in samples],
            )
            self.assertTrue(any(not s.receive_integrity.sequence_valid for s in samples))
        finally:
            tmp.cleanup()

    def test_timestamp_regression(self):
        tmp, _path, session, samples = record_scenario("timestamp_regression", duration_s=0.5, random_seed=23)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            self.assertGreaterEqual(result.integrity.timestamp_error_count, 1)
            self.assertEqual(
                list(result.normalized.device_time_us),
                [s.device_time_us for s in samples],
            )
            self.assertTrue(any(e.code == "TIMESTAMP_ERROR" for e in result.integrity.evidence))
        finally:
            tmp.cleanup()

    def test_sensor_disconnection(self):
        tmp, _path, session, samples = record_scenario("sensor_disconnection", duration_s=0.5, random_seed=24)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            # Quality-capable integrity failure — not blocked_before_quality.
            self.assertFalse(result.integrity.pre_quality_blocked)
            self.assertFalse(result.integrity.integrity_ok)
            self.assertGreater(result.integrity.sensor_disconnection_count, 0)
            self.assertTrue(any(e.code == "SENSOR_DISCONNECTED" for e in result.integrity.evidence))
            self.assertNotIn("sensor_disconnected", result.integrity.blocking_codes)
            self.assertNotIn("emergency_stop", result.integrity.blocking_codes)
        finally:
            tmp.cleanup()

    def test_abort(self):
        tmp, _path, session, samples = record_scenario("abort", duration_s=0.5, random_seed=25)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            self.assertTrue(result.integrity.pre_quality_blocked)
            self.assertTrue(any(state == "SAFE_HOLD" for state in result.normalized.device_state))
            self.assertIn("emergency_stop", result.integrity.blocking_codes)
            # No quality object on preprocess result
            self.assertFalse(hasattr(result, "quality"))
        finally:
            tmp.cleanup()

    def test_device_fault(self):
        tmp, _path, session, samples = record_scenario("device_fault", duration_s=0.5, random_seed=26)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            self.assertTrue(result.integrity.pre_quality_blocked)
            self.assertTrue(any(state == "FAULT" for state in result.normalized.device_state))
            self.assertIn("device_fault", result.integrity.blocking_codes)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
