from __future__ import annotations

from dataclasses import replace
import unittest

import numpy as np

from digital_pulse.m1_contracts import SourceType
from digital_pulse.m1_simulator import SimulatorDataSource, get_scenario
from digital_pulse.m1_sp.errors import SPError
from digital_pulse.m1_sp.normalization import InputNormalizer
from digital_pulse.m1_sp.processor import SPPreprocessor

from _m1_sp_helpers import load_replay_samples, record_scenario


class M1SPNormalizationTests(unittest.TestCase):
    def test_normal_fields(self):
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=0.3, random_seed=11)
        try:
            normalized = InputNormalizer().normalize(session, samples)
            self.assertEqual(normalized.sample_count, len(samples))
            self.assertEqual(normalized.session_id, session.session_id)
            self.assertEqual(normalized.source_type, SourceType.SIMULATOR)
            self.assertTrue(np.all(normalized.pulse.valid_mask))
            self.assertFalse(np.any(np.isnan(normalized.pulse.values)))
            self.assertEqual(list(normalized.frame_sequence), [s.frame_sequence for s in samples])
            self.assertEqual(list(normalized.device_time_us), [s.device_time_us for s in samples])
            self.assertFalse(np.any(normalized.pulse.clipping_upper_mask))
        finally:
            tmp.cleanup()

    def test_disconnected_is_nan_not_zero(self):
        tmp, _path, session, samples = record_scenario("sensor_disconnection", duration_s=0.5, random_seed=12)
        try:
            normalized = InputNormalizer().normalize(session, samples)
            absents = [i for i, s in enumerate(samples) if s.pulse.value is None]
            self.assertTrue(absents)
            for i in absents:
                self.assertTrue(np.isnan(normalized.pulse.values[i]))
                self.assertFalse(normalized.pulse.valid_mask[i])
                self.assertNotEqual(normalized.pulse.values[i], 0.0)
        finally:
            tmp.cleanup()

    def test_session_mismatch_fails(self):
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=0.2, random_seed=13)
        try:
            mismatched = [replace(samples[0], session_id="other-session"), *samples[1:]]
            with self.assertRaises(SPError) as ctx:
                InputNormalizer().normalize(session, mismatched)
            self.assertEqual(ctx.exception.code, "session_id_mismatch")
        finally:
            tmp.cleanup()

    def test_empty_fails(self):
        tmp, _path, session, _samples = record_scenario("normal_high_quality", duration_s=0.2, random_seed=14)
        try:
            with self.assertRaises(SPError) as ctx:
                InputNormalizer().normalize(session, [])
            self.assertEqual(ctx.exception.code, "empty_session")
        finally:
            tmp.cleanup()

    def test_replay_matches_direct(self):
        tmp, session_path, session, samples = record_scenario("normal_high_quality", duration_s=0.3, random_seed=15)
        try:
            direct = SPPreprocessor().preprocess(session, samples)
            replay_session, replay_samples = load_replay_samples(session_path)
            replayed = SPPreprocessor().preprocess(replay_session, replay_samples)
            self.assertEqual(direct.normalized.sample_count, replayed.normalized.sample_count)
            np.testing.assert_array_equal(direct.normalized.frame_sequence, replayed.normalized.frame_sequence)
            np.testing.assert_array_equal(direct.normalized.device_time_us, replayed.normalized.device_time_us)
            np.testing.assert_allclose(direct.normalized.pulse.values, replayed.normalized.pulse.values, equal_nan=True)
            self.assertEqual(direct.integrity.integrity_ok, replayed.integrity.integrity_ok)
            self.assertEqual(direct.windows.selected_window_id, replayed.windows.selected_window_id)
        finally:
            tmp.cleanup()

    def test_does_not_mutate_input_samples(self):
        source = SimulatorDataSource(get_scenario("normal_high_quality", duration_s=0.2, random_seed=16))
        samples = list(source.samples())
        before = [s.pulse.value for s in samples]
        tmp, _path, session, _ = record_scenario("normal_high_quality", duration_s=0.2, random_seed=16)
        try:
            InputNormalizer().normalize(session, samples)
            self.assertEqual([s.pulse.value for s in samples], before)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
