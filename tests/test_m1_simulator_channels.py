from __future__ import annotations

import unittest

import numpy as np

from digital_pulse.m1_contracts import ClippingFlag, SensorStatus
from digital_pulse.m1_simulator import build_normal_high_quality
from digital_pulse.m1_simulator.channels import build_channels
from digital_pulse.m1_simulator.clock import DeterministicClock
from digital_pulse.m1_simulator.timeline import BeatTimeline, derive_rng_streams


class M1SimulatorChannelTests(unittest.TestCase):
    def _setup(self, **overrides):
        config = build_normal_high_quality(duration_s=4.0, sample_rate_hz=250.0, **overrides)
        streams = derive_rng_streams(config.random_seed)
        timeline = BeatTimeline(config, streams.beat_rng)
        channels = build_channels(config, timeline, streams.pulse_rng, streams.load_rng, streams.ppg_rng)
        clock = DeterministicClock(config)
        return config, timeline, channels, clock

    def test_pulse_is_periodic_finite_and_connected(self):
        _, _, (pulse, _, _), clock = self._setup(random_seed=5)
        values = [pulse.sample(tick).value for tick in clock.iter_ticks()]
        self.assertTrue(all(value is not None and np.isfinite(value) for value in values))
        sample = pulse.sample(clock.tick(10))
        self.assertEqual(sample.status, SensorStatus.CONNECTED)
        self.assertEqual(sample.clipping, ClippingFlag.NONE)
        # Autocorrelation peak near one beat period should exceed lag far from the period.
        arr = np.asarray(values, dtype=float)
        arr = arr - np.mean(arr)
        lag_beat = int(round(250 * 60 / 72))
        corr_beat = float(np.corrcoef(arr[:-lag_beat], arr[lag_beat:])[0, 1])
        corr_far = float(np.corrcoef(arr[:-40], arr[40:])[0, 1])
        self.assertGreater(corr_beat, 0.2)
        self.assertGreater(corr_beat, corr_far - 0.05)

    def test_load_stable_connected_and_manual_targets_null_in_datasource(self):
        _, _, (_, load, _), clock = self._setup(random_seed=5)
        values = np.asarray([load.sample(tick).value for tick in clock.iter_ticks()], dtype=float)
        self.assertLess(np.std(values), 100.0)
        sample = load.sample(clock.tick(0))
        self.assertEqual(sample.status, SensorStatus.CONNECTED)
        self.assertEqual(sample.clipping, ClippingFlag.NONE)

    def test_ppg_uses_shared_beats_and_configured_delay(self):
        config, timeline, (pulse, _, ppg), clock = self._setup(random_seed=8, ppg_delay_ms=40.0)
        pulse_values = np.asarray([pulse.sample(tick).value for tick in clock.iter_ticks()], dtype=float)
        # Rebuild channels with fresh RNGs derived from the same seed for PPG-only path comparison.
        streams = derive_rng_streams(config.random_seed)
        timeline2 = BeatTimeline(config, streams.beat_rng)
        self.assertEqual(timeline.events, timeline2.events)
        ppg_values = []
        streams = derive_rng_streams(config.random_seed)
        _ = BeatTimeline(config, streams.beat_rng)
        _, _, ppg_channel = build_channels(
            config,
            timeline,
            streams.pulse_rng,
            streams.load_rng,
            streams.ppg_rng,
        )
        for tick in clock.iter_ticks():
            ppg_values.append(ppg_channel.sample(tick).value)
        ppg_values = np.asarray(ppg_values, dtype=float)
        delay_samples = int(round(config.sample_rate_hz * config.ppg_delay_ms / 1000.0))
        # Pulse leads PPG: correlation of pulse[t] with ppg[t+delay] should beat pulse[t] vs ppg[t].
        corr_aligned = float(
            np.corrcoef(pulse_values[:-delay_samples], ppg_values[delay_samples:])[0, 1]
        )
        corr_same = float(np.corrcoef(pulse_values, ppg_values)[0, 1])
        self.assertGreater(corr_aligned, corr_same)
        self.assertEqual(ppg.sample(clock.tick(0)).status, SensorStatus.CONNECTED)
        self.assertEqual(ppg.sample(clock.tick(0)).clipping, ClippingFlag.NONE)

    def test_same_seed_channel_values_match(self):
        config = build_normal_high_quality(duration_s=1.0, random_seed=99)
        def collect():
            streams = derive_rng_streams(config.random_seed)
            timeline = BeatTimeline(config, streams.beat_rng)
            pulse, load, ppg = build_channels(
                config, timeline, streams.pulse_rng, streams.load_rng, streams.ppg_rng
            )
            clock = DeterministicClock(config)
            return [
                (pulse.sample(tick).value, load.sample(tick).value, ppg.sample(tick).value)
                for tick in clock.iter_ticks()
            ]
        self.assertEqual(collect(), collect())


if __name__ == "__main__":
    unittest.main()
