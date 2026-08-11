from __future__ import annotations

import unittest

from digital_pulse.m1_sp.processor import SPPreprocessor

from _m1_sp_helpers import record_scenario


class M1SPWindowTests(unittest.TestCase):
    def test_normal_has_window(self):
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=0.4, random_seed=31)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            self.assertGreaterEqual(len(result.windows.windows), 1)
            self.assertIsNotNone(result.windows.selected_window_id)
            selected = next(w for w in result.windows.windows if w.window_id == result.windows.selected_window_id)
            self.assertGreater(selected.sample_count, 0)
            self.assertEqual(selected.end_index - selected.start_index, selected.sample_count)
            self.assertTrue(selected.window_id.startswith("window-"))
        finally:
            tmp.cleanup()

    def test_weak_and_no_contact_may_still_have_structural_windows(self):
        for scenario_id in ("weak_signal", "no_contact", "motion_artifact"):
            with self.subTest(scenario_id=scenario_id):
                tmp, _path, session, samples = record_scenario(scenario_id, duration_s=0.4, random_seed=32)
                try:
                    result = SPPreprocessor().preprocess(session, samples)
                    # P2A must not classify weak/no_contact/motion; structural windows may exist.
                    self.assertTrue(result.windows.windows or result.windows.evidence)
                    self.assertFalse(hasattr(result, "quality_label"))
                finally:
                    tmp.cleanup()

    def test_frame_loss_splits_or_excludes_gap_samples(self):
        tmp, _path, session, samples = record_scenario("frame_loss", duration_s=0.6, random_seed=33)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            for window in result.windows.windows:
                segment_seq = result.normalized.sequence_valid[window.start_index : window.end_index]
                self.assertFalse(any(int(v) == 0 for v in segment_seq))
        finally:
            tmp.cleanup()

    def test_sensor_disconnection_windows_end_before_or_at_disconnect(self):
        tmp, _path, session, samples = record_scenario("sensor_disconnection", duration_s=0.6, random_seed=34)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            first_bad = next(
                (i for i, s in enumerate(samples) if "sensor_disconnected" in s.fault_flags or s.pulse.value is None),
                None,
            )
            self.assertIsNotNone(first_bad)
            for window in result.windows.windows:
                self.assertLessEqual(window.end_index, first_bad + 1)
                # Invalid disconnect samples must not be inside window
                for idx in range(window.start_index, window.end_index):
                    self.assertTrue(result.normalized.pulse.valid_mask[idx])
        finally:
            tmp.cleanup()

    def test_abort_terminal_samples_excluded(self):
        tmp, _path, session, samples = record_scenario("abort", duration_s=0.6, random_seed=35)
        try:
            result = SPPreprocessor().preprocess(session, samples)
            for window in result.windows.windows:
                for idx in range(window.start_index, window.end_index):
                    self.assertNotEqual(result.normalized.device_state[idx], "SAFE_HOLD")
                    self.assertNotIn("emergency_stop", result.normalized.fault_flags[idx])
        finally:
            tmp.cleanup()

    def test_deterministic_window_selection(self):
        tmp, _path, session, samples = record_scenario("normal_high_quality", duration_s=0.4, random_seed=36)
        try:
            a = SPPreprocessor().preprocess(session, samples)
            b = SPPreprocessor().preprocess(session, samples)
            self.assertEqual(a.windows.selected_window_id, b.windows.selected_window_id)
            self.assertEqual(
                [(w.window_id, w.start_index, w.end_index) for w in a.windows.windows],
                [(w.window_id, w.start_index, w.end_index) for w in b.windows.windows],
            )
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
