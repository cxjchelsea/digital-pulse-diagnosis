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
        self.assertEqual(ppg_idx, sorted(ppg_idx))
        self.assertEqual(len(set(pulse_idx)), 5)
        self.assertEqual(len(set(ppg_idx)), 5)

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
