from __future__ import annotations

import unittest

import numpy as np

from digital_pulse.m1_simulator import build_normal_high_quality
from digital_pulse.m1_simulator.timeline import BeatTimeline, derive_rng_streams


class M1SimulatorTimelineTests(unittest.TestCase):
    def test_same_seed_events_match_and_intervals_positive(self):
        config = build_normal_high_quality(duration_s=5.0, random_seed=42, heart_rate_bpm=72.0)
        first = BeatTimeline(config, derive_rng_streams(42).beat_rng).events
        second = BeatTimeline(config, derive_rng_streams(42).beat_rng).events
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 4)
        self.assertTrue(all(event.interval_s > 0 for event in first))
        self.assertTrue(all(later.beat_time_s > earlier.beat_time_s for earlier, later in zip(first, first[1:])))

    def test_different_seed_changes_detail_not_mean_rate_band(self):
        config_a = build_normal_high_quality(duration_s=10.0, random_seed=1, heart_rate_bpm=72.0)
        config_b = build_normal_high_quality(duration_s=10.0, random_seed=2, heart_rate_bpm=72.0)
        timeline_a = BeatTimeline(config_a, derive_rng_streams(1).beat_rng)
        timeline_b = BeatTimeline(config_b, derive_rng_streams(2).beat_rng)
        self.assertNotEqual(timeline_a.events, timeline_b.events)
        self.assertTrue(60.0 <= timeline_a.mean_heart_rate_bpm() <= 85.0)
        self.assertTrue(60.0 <= timeline_b.mean_heart_rate_bpm() <= 85.0)

    def test_pulse_and_ppg_share_same_timeline_object_semantics(self):
        config = build_normal_high_quality(duration_s=3.0, random_seed=9, ppg_delay_ms=40.0)
        timeline = BeatTimeline(config, derive_rng_streams(9).beat_rng)
        event_pulse, phase_pulse = timeline.phase_at(1.0)
        event_ppg, phase_ppg = timeline.phase_at(1.0 - 0.040)
        self.assertEqual(event_pulse.beat_index, timeline.phase_at(1.0)[0].beat_index)
        self.assertNotEqual((event_pulse.beat_index, round(phase_pulse, 6)), (event_ppg.beat_index, round(phase_ppg, 6)))
        # Delay moves PPG earlier on the shared timeline without inventing a second beat train.
        self.assertLessEqual(event_ppg.beat_time_s, event_pulse.beat_time_s + event_pulse.interval_s)

    def test_rng_streams_are_independent(self):
        streams = derive_rng_streams(123)
        beat_draw = streams.beat_rng.normal(size=3)
        pulse_draw = streams.pulse_rng.normal(size=3)
        # Re-derive and consume only pulse stream: beat stream remains unaffected.
        streams2 = derive_rng_streams(123)
        _ = streams2.pulse_rng.normal(size=3)
        beat_draw2 = streams2.beat_rng.normal(size=3)
        np.testing.assert_allclose(beat_draw, beat_draw2)
        self.assertFalse(np.allclose(beat_draw, pulse_draw))


if __name__ == "__main__":
    unittest.main()
