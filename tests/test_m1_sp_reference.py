from __future__ import annotations

import unittest

import numpy as np

from digital_pulse.m1_sp.beats import BeatCandidate
from digital_pulse.m1_sp.parameters import default_p2c_parameter_set
from digital_pulse.m1_sp.processor import create_p2c_processor
from digital_pulse.m1_sp.reference import ReferenceAligner

from _m1_sp_helpers import record_scenario


def _beat(i: int, t_us: int, *, valid: bool = True) -> BeatCandidate:
    return BeatCandidate(i, t_us, 1.0, 1.0, 1000.0, None, None, valid, ())


class M1SPReferenceTests(unittest.TestCase):
    def test_one_to_one_monotonic(self):
        pulse = tuple(_beat(i, i * 800_000) for i in range(5))
        ppg = tuple(_beat(i, i * 800_000 + 76_000) for i in range(5))
        summary = ReferenceAligner().align(
            pulse_beats=pulse,
            ppg_beats=ppg,
            parameters=default_p2c_parameter_set(),
            ppg_channel_available=True,
        )
        self.assertEqual(summary.matched_count, 5)
        self.assertEqual(summary.match_rate, 1.0)
        pulse_idx = [p[0] for p in summary.matched_pairs]
        ppg_idx = [p[1] for p in summary.matched_pairs]
        self.assertEqual(pulse_idx, sorted(pulse_idx))
        self.assertTrue(all(ppg_idx[i] < ppg_idx[i + 1] for i in range(len(ppg_idx) - 1)))
        self.assertEqual(len(set(pulse_idx)), 5)
        self.assertEqual(len(set(ppg_idx)), 5)
        self.assertLessEqual(summary.matched_count, summary.pulse_beat_count)
        self.assertLessEqual(summary.matched_count, summary.ppg_beat_count)

    def test_overlapping_candidates_keep_ppg_indices_strictly_increasing(self):
        pulse = (_beat(0, 0), _beat(1, 20_000))
        # The first pulse prefers PPG 1. For the second pulse, PPG 0 is
        # slightly nearer the lag-window midpoint than PPG 2, but selecting it
        # would reverse the reference timeline.
        ppg = (_beat(0, 50_000), _beat(1, 80_000), _beat(2, 155_000))
        summary = ReferenceAligner().align(
            pulse_beats=pulse,
            ppg_beats=ppg,
            parameters=default_p2c_parameter_set(),
            ppg_channel_available=True,
        )

        pairs = summary.matched_pairs
        pulse_indices = [p[0] for p in pairs]
        ppg_indices = [p[1] for p in pairs]
        self.assertEqual(pairs, ((0, 1, 80.0), (1, 2, 135.0)))
        self.assertEqual(pulse_indices, sorted(pulse_indices))
        self.assertTrue(
            all(ppg_indices[i] < ppg_indices[i + 1] for i in range(len(ppg_indices) - 1))
        )
        self.assertEqual(len(set(ppg_indices)), len(ppg_indices))

    def test_does_not_reorder_ppg_to_improve_match_rate(self):
        pulse = (_beat(0, 0), _beat(1, 20_000))
        ppg = (_beat(0, 50_000), _beat(1, 80_000))
        summary = ReferenceAligner().align(
            pulse_beats=pulse,
            ppg_beats=ppg,
            parameters=default_p2c_parameter_set(),
            ppg_channel_available=True,
        )

        # A non-monotonic matcher could return (pulse 0, PPG 1) followed by
        # (pulse 1, PPG 0) and claim a 100% match rate. Ordering takes priority.
        self.assertEqual(summary.matched_pairs, ((0, 1, 80.0),))
        self.assertEqual(summary.matched_count, 1)
        self.assertEqual(summary.match_rate, 0.5)
        self.assertLessEqual(summary.matched_count, summary.pulse_beat_count)
        self.assertLessEqual(summary.matched_count, summary.ppg_beat_count)

    def test_zero_pulse_beats_match_rate_none(self):
        summary = ReferenceAligner().align(
            pulse_beats=(),
            ppg_beats=(_beat(0, 1000),),
            parameters=default_p2c_parameter_set(),
            ppg_channel_available=True,
        )
        self.assertIsNone(summary.match_rate)
        self.assertTrue(summary.reference_available)

    def test_ppg_unavailable_match_rate_none(self):
        summary = ReferenceAligner().align(
            pulse_beats=(_beat(0, 1000), _beat(1, 900000)),
            ppg_beats=(),
            parameters=default_p2c_parameter_set(),
            ppg_channel_available=False,
        )
        self.assertIsNone(summary.match_rate)
        self.assertFalse(summary.reference_available)

    def test_tie_prefers_earlier_ppg(self):
        pulse = (_beat(0, 1_000_000),)
        # Two PPG at same lag distance around mid window — earlier index wins via algorithm.
        ppg = (_beat(0, 1_000_000 + 76_000), _beat(1, 1_000_000 + 76_000))
        summary = ReferenceAligner().align(
            pulse_beats=pulse,
            ppg_beats=ppg,
            parameters=default_p2c_parameter_set(),
            ppg_channel_available=True,
        )
        self.assertEqual(summary.matched_count, 1)
        self.assertEqual(summary.matched_pairs[0][1], 0)

    def test_normal_and_misalignment_scenarios(self):
        proc = create_p2c_processor()
        tmp_n, _, s_n, samples_n = record_scenario("normal_high_quality", duration_s=8.0, random_seed=1001)
        tmp_m, _, s_m, samples_m = record_scenario("ppg_misalignment", duration_s=8.0, random_seed=1001)
        try:
            n = proc.process(s_n, samples_n).quality_results[0]
            m = proc.process(s_m, samples_m).quality_results[0]
            self.assertEqual(n.label.value, "acceptable")
            self.assertEqual(n.metrics["ppg_match_rate"], 1.0)
            self.assertEqual(m.label.value, "reference_mismatch")
            self.assertIn("reference_mismatch", m.reason_codes)
            self.assertLess(m.metrics["ppg_match_rate"], 0.7)
        finally:
            tmp_n.cleanup()
            tmp_m.cleanup()


if __name__ == "__main__":
    unittest.main()
