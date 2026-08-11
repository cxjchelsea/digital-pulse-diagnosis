from __future__ import annotations

import unittest

from digital_pulse.m1_sp.parameters import SP_PARAMETER_VERSION, SP_PROCESSING_VERSION
from digital_pulse.m1_sp.processor import SPPreprocessor

from _m1_sp_helpers import record_scenario


class M1SPPreprocessorTests(unittest.TestCase):
    def test_entrypoint_versions_and_no_quality(self):
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=0.3, random_seed=41)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            self.assertEqual(result.processing_version, SP_PROCESSING_VERSION)
            self.assertEqual(result.parameter_version, SP_PARAMETER_VERSION)
            self.assertEqual(len(result.parameter_digest), 64)
            self.assertIsNotNone(result.normalized)
            self.assertIsNotNone(result.integrity)
            self.assertIsNotNone(result.windows)
            self.assertFalse(hasattr(result, "quality"))
            self.assertFalse(hasattr(result, "decision"))
            self.assertFalse(hasattr(result, "beats"))
        finally:
            tmp.cleanup()

    def test_deterministic_repeat(self):
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=0.3, random_seed=42)
        try:
            a = SPPreprocessor().preprocess(session, samples)
            b = SPPreprocessor().preprocess(session, samples)
            self.assertEqual(a.parameter_digest, b.parameter_digest)
            self.assertEqual(a.integrity.integrity_ok, b.integrity.integrity_ok)
            self.assertEqual(
                [e.code for e in a.integrity.evidence],
                [e.code for e in b.integrity.evidence],
            )
            self.assertEqual(a.windows.selected_window_id, b.windows.selected_window_id)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
