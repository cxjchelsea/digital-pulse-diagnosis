from __future__ import annotations

import unittest

import numpy as np

from digital_pulse.m1_sp.beats import BeatDetector, analyze_beats, compute_interval_stats
from digital_pulse.m1_sp.filters import FilterBank, MODE_OFFLINE
from digital_pulse.m1_sp.models import NormalizedChannelSeries, StableWindow
from digital_pulse.m1_sp.parameters import default_p2c_parameter_set
from digital_pulse.m1_sp.processor import create_p2c_processor

from _m1_sp_helpers import record_scenario


class M1SPBeatTests(unittest.TestCase):
    def test_synthetic_sinusoid_peaks(self):
        fs = 250.0
        t = np.arange(0, 4.0, 1 / fs)
        # Positive pulses every 0.8s
        x = np.zeros_like(t)
        for k in range(5):
            center = int((0.4 + 0.8 * k) * fs)
            width = int(0.05 * fs)
            idx = np.arange(max(0, center - width), min(len(x), center + width))
            x[idx] = 2000.0 * np.exp(-0.5 * ((idx - center) / (width / 2)) ** 2)
        x = x + 16000.0
        channel = NormalizedChannelSeries(
            values=x.astype(np.float64),
            valid_mask=np.ones(x.shape[0], dtype=bool),
            clipping_lower_mask=np.zeros(x.shape[0], dtype=bool),
            clipping_upper_mask=np.zeros(x.shape[0], dtype=bool),
        )
        filtered = FilterBank(default_p2c_parameter_set()).offline_review(channel)
        window = StableWindow("window-0001", 0, len(x), 0, int((len(x) - 1) * 1e6 / fs), len(x), float(t[-1]))
        analysis = analyze_beats(
            filtered=filtered,
            raw_values=x,
            device_time_us=(t * 1e6).astype(np.int64),
            sample_rate_hz=fs,
            window=window,
            parameters=default_p2c_parameter_set(),
        )
        self.assertGreaterEqual(analysis.beat_count, 4)
        self.assertEqual(analysis.detection_source, "offline_review")
        self.assertEqual(len(analysis.segments), analysis.beat_count)
        # Non-overlapping segments
        for a, b in zip(analysis.segments, analysis.segments[1:]):
            self.assertLessEqual(a.end_index, b.start_index)
            self.assertLess(a.peak_index, b.peak_index)

    def test_normal_scenario_beat_count_stable(self):
        proc = create_p2c_processor()
        tmp, _, session, samples = record_scenario("normal_high_quality", duration_s=8.0, random_seed=1001)
        try:
            out = proc.process(session, samples)
            q = out.quality_results[0]
            self.assertEqual(q.label.value, "acceptable")
            self.assertEqual(q.metrics["beat_count"], 10)
            beats = out.beats_by_window[q.window_id]
            again = proc.process(session, samples)
            self.assertEqual(
                [b.peak_index for b in beats.candidates],
                [b.peak_index for b in again.beats_by_window[q.window_id].candidates],
            )
            self.assertEqual(
                [s.beat_id for s in beats.segments],
                [s.beat_id for s in again.beats_by_window[q.window_id].segments],
            )
        finally:
            tmp.cleanup()

    def test_two_close_peaks_conflict(self):
        fs = 250.0
        x = np.zeros(200, dtype=np.float64) + 16000.0
        x[80] = 19000.0
        x[90] = 18500.0  # within 0.5s
        channel = NormalizedChannelSeries(
            values=x,
            valid_mask=np.ones(200, dtype=bool),
            clipping_lower_mask=np.zeros(200, dtype=bool),
            clipping_upper_mask=np.zeros(200, dtype=bool),
        )
        filtered = FilterBank(default_p2c_parameter_set()).offline_review(channel)
        # Force filtered local shape
        filtered_vals = filtered.values.copy()
        filtered_vals[:] = 16000.0
        filtered_vals[80] = 19000.0
        filtered_vals[90] = 18500.0
        from digital_pulse.m1_sp.filters import FilteredSeries

        forced = FilteredSeries(
            values=filtered_vals,
            valid_mask=np.ones(200, dtype=bool),
            mode=MODE_OFFLINE,
            group_delay_samples=filtered.group_delay_samples,
            filter_version=filtered.filter_version,
            num_taps=filtered.num_taps,
        )
        cands = BeatDetector().detect(
            filtered=forced,
            raw_values=x,
            device_time_us=(np.arange(200) * 4000).astype(np.int64),
            sample_rate_hz=fs,
            parameters=default_p2c_parameter_set(),
        )
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].peak_index, 80)

    def test_interval_cv_none_with_one_beat(self):
        from digital_pulse.m1_sp.beats import BeatCandidate

        c = BeatCandidate(1, 1000, 1.0, 1.0, 10.0, None, None, True, ())
        mean, std, cv = compute_interval_stats((c,))
        self.assertIsNone(mean)
        self.assertIsNone(cv)

    def test_short_scenario_few_beats(self):
        proc = create_p2c_processor()
        tmp, _, session, samples = record_scenario("insufficient_duration", duration_s=1.0, random_seed=1001)
        try:
            out = proc.process(session, samples)
            q = out.quality_results[0]
            self.assertEqual(q.label.value, "insufficient_duration")
            self.assertIn("too_short", q.reason_codes)
            self.assertLess(q.metrics.get("beat_count", 0), 4)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
