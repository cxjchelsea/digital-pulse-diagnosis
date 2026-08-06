from __future__ import annotations

import math
import unittest

import numpy as np

from digital_pulse.m1_contracts import ClippingFlag, SensorStatus, SourceType
from digital_pulse.m1_simulator import (
    SimulatorDataSource,
    build_normal_high_quality,
    get_scenario,
    get_scenario_definition,
    list_scenarios,
)
from digital_pulse.m1_simulator.config import sample_count
from digital_pulse.m1_simulator.timeline import BeatTimeline, derive_rng_streams

P1B_SCENARIOS = (
    "weak_signal",
    "no_contact",
    "upper_saturation",
    "lower_saturation",
    "baseline_drift",
    "motion_artifact",
    "unstable_load",
    "ppg_misalignment",
    "insufficient_duration",
)

NORMAL_DIGEST = "7e8c1845e9a71b235a66727176edbf0521c9f0972752fcbd5e29478493c1f226"
NORMAL_FINGERPRINTS = {
    0: (0, 0, 16034, 79990, 19999, "none"),
    1: (1, 4000, 16001, 80002, 19979, "none"),
    2: (2, 8000, 16016, 79992, 20002, "none"),
    1000: (1000, 4000000, 15964, 80010, 20035, "none"),
    -1: (1999, 7996000, 16272, 80026, 20333, "none"),
}


def _window_masks(config):
    if not config.fault_schedule:
        n = sample_count(config.duration_s, config.sample_rate_hz)
        return np.zeros(n, dtype=bool), np.ones(n, dtype=bool), np.zeros(n, dtype=bool)
    window = config.fault_schedule[0]
    n = sample_count(config.duration_s, config.sample_rate_hz)
    times = np.arange(n, dtype=float) / float(config.sample_rate_hz)
    before = times < window.start_s
    during = (times >= window.start_s) & (times < window.end_s)
    after = times >= window.end_s
    return before, during, after


class M1SimulatorSignalScenarioTests(unittest.TestCase):
    def test_registry_lists_p1b_scenarios_sorted_unique(self):
        names = list_scenarios()
        self.assertEqual(names, tuple(sorted(set(names))))
        self.assertEqual(len(names), 10)
        for scenario_id in ("normal_high_quality",) + P1B_SCENARIOS:
            self.assertIn(scenario_id, names)
        for deferred in (
            "frame_loss",
            "timestamp_regression",
            "sensor_disconnection",
            "abort",
            "device_fault",
            "raw_persistence_failure",
            "retry_improves",
            "retry_still_fails",
        ):
            self.assertNotIn(deferred, names)

    def test_normal_p1a_regression_fingerprint(self):
        config = build_normal_high_quality()
        self.assertEqual(config.simulator_version, "0.1.0-p1a")
        self.assertEqual(config.configuration_digest(), NORMAL_DIGEST)
        samples = list(SimulatorDataSource(config).samples())
        self.assertEqual(len(samples), 2000)
        for idx, expected in NORMAL_FINGERPRINTS.items():
            sample = samples[idx]
            actual = (
                sample.frame_sequence,
                sample.device_time_us,
                sample.pulse.value,
                sample.load.value,
                sample.ppg.value,
                sample.pulse.clipping.value,
            )
            self.assertEqual(actual, expected, msg=f"index {idx}")

    def test_each_p1b_scenario_common_invariants(self):
        for scenario_id in P1B_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                definition = get_scenario_definition(scenario_id)
                self.assertEqual(definition.scenario_id, scenario_id)
                self.assertFalse(definition.analysis_allowed)
                self.assertTrue(definition.expected_completion)
                config = get_scenario(scenario_id, random_seed=1001)
                config.validate()
                digest_a = config.configuration_digest()
                digest_b = get_scenario(scenario_id, random_seed=1001).configuration_digest()
                self.assertEqual(digest_a, digest_b)
                source = SimulatorDataSource(config)
                first = list(source.samples())
                second = list(source.samples())
                self.assertEqual(len(first), sample_count(config.duration_s, config.sample_rate_hz))
                self.assertEqual([s.to_dict() for s in first], [s.to_dict() for s in second])
                self.assertEqual([s.frame_sequence for s in first], list(range(len(first))))
                device_times = [s.device_time_us for s in first]
                self.assertEqual(device_times, sorted(set(device_times)))
                for sample in first:
                    self.assertEqual(sample.source_type, SourceType.SIMULATOR)
                    self.assertEqual(sample.device_state, "ACQUIRE")
                    self.assertEqual(sample.fault_flags, ())
                    self.assertTrue(sample.receive_integrity.crc_valid)
                    self.assertTrue(sample.receive_integrity.sequence_valid)
                    self.assertTrue(sample.receive_integrity.timestamp_valid)
                    self.assertIsNone(sample.target_load_raw)
                    self.assertIsNone(sample.motor_position_raw)
                    sample.validate_schema()
                self.assertFalse(hasattr(source, "quality_result"))
                self.assertFalse(any("decision" in sample.to_dict() for sample in first[:3]))

    def test_multi_seed_semantics_stable(self):
        seeds = (11, 22, 33)
        for scenario_id in P1B_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                series = []
                for seed in seeds:
                    config = get_scenario(scenario_id, random_seed=seed)
                    samples = list(SimulatorDataSource(config).samples())
                    series.append([s.pulse.value for s in samples])
                    before, during, after = _window_masks(config)
                    pulse = np.asarray([s.pulse.value for s in samples], dtype=float)
                    load = np.asarray([s.load.value for s in samples], dtype=float)
                    if scenario_id == "weak_signal":
                        normal = list(
                            SimulatorDataSource(
                                build_normal_high_quality(random_seed=seed, duration_s=config.duration_s)
                            ).samples()
                        )
                        normal_pulse = np.asarray([s.pulse.value for s in normal], dtype=float)
                        self.assertLess(np.ptp(pulse[during]), 0.45 * np.ptp(normal_pulse[during]))
                    elif scenario_id == "no_contact":
                        self.assertLess(np.std(pulse[during]), 40.0)
                        self.assertLess(np.mean(load[during]), 1000.0)
                        self.assertTrue(all(s.pulse.status is SensorStatus.CONNECTED for s in samples))
                        self.assertTrue(all(s.load.status is SensorStatus.CONNECTED for s in samples))
                    elif scenario_id == "upper_saturation":
                        self.assertTrue(all(s.pulse.value == 30_000 for s, flag in zip(samples, during) if flag))
                        self.assertTrue(
                            all(s.pulse.clipping is ClippingFlag.UPPER for s, flag in zip(samples, during) if flag)
                        )
                        self.assertTrue(
                            all(s.pulse.clipping is ClippingFlag.NONE for s, flag in zip(samples, before | after) if flag)
                        )
                    elif scenario_id == "lower_saturation":
                        self.assertTrue(all(s.pulse.value == 1_000 for s, flag in zip(samples, during) if flag))
                        self.assertTrue(
                            all(s.pulse.clipping is ClippingFlag.LOWER for s, flag in zip(samples, during) if flag)
                        )
                    elif scenario_id == "unstable_load":
                        normal_load = np.asarray(
                            [
                                s.load.value
                                for s in SimulatorDataSource(
                                    build_normal_high_quality(random_seed=seed, duration_s=config.duration_s)
                                ).samples()
                            ],
                            dtype=float,
                        )
                        self.assertGreater(np.std(load[during]), 5.0 * np.std(normal_load[during]))
                    elif scenario_id == "insufficient_duration":
                        self.assertEqual(config.fault_schedule, ())
                        self.assertLess(len(samples), 2000)
                self.assertEqual(series[0], series[0])
                self.assertNotEqual(series[0], series[1])

    def test_weak_signal_window_semantics(self):
        config = get_scenario("weak_signal", random_seed=7)
        samples = list(SimulatorDataSource(config).samples())
        before, during, after = _window_masks(config)
        normal = list(
            SimulatorDataSource(build_normal_high_quality(random_seed=7, duration_s=config.duration_s)).samples()
        )
        pulse = np.asarray([s.pulse.value for s in samples], dtype=float)
        normal_pulse = np.asarray([s.pulse.value for s in normal], dtype=float)
        self.assertLess(np.ptp(pulse[during]), 0.35 * np.ptp(normal_pulse[during]))
        self.assertGreater(np.ptp(pulse[before]), 0.7 * np.ptp(normal_pulse[before]))
        self.assertTrue(all(s.pulse.value is not None for s in samples))
        self.assertTrue(all(s.pulse.status is SensorStatus.CONNECTED for s in samples))
        self.assertTrue(all(s.pulse.clipping is ClippingFlag.NONE for s in samples))
        beats_a = BeatTimeline(config, derive_rng_streams(7).beat_rng).events
        beats_b = BeatTimeline(
            build_normal_high_quality(random_seed=7, duration_s=config.duration_s),
            derive_rng_streams(7).beat_rng,
        ).events
        self.assertEqual(beats_a, beats_b)

    def test_no_contact_keeps_sensors_connected(self):
        samples = list(SimulatorDataSource(get_scenario("no_contact", random_seed=9)).samples())
        config = get_scenario("no_contact", random_seed=9)
        _, during, _ = _window_masks(config)
        for sample, active in zip(samples, during):
            self.assertIs(sample.pulse.status, SensorStatus.CONNECTED)
            self.assertIs(sample.load.status, SensorStatus.CONNECTED)
            self.assertIs(sample.ppg.status, SensorStatus.CONNECTED)
            self.assertNotEqual(sample.pulse.status.value, "disconnected")
            self.assertNotEqual(sample.pulse.status.value, "read_failed")
            if active:
                self.assertIsNotNone(sample.pulse.value)
                self.assertIsNotNone(sample.load.value)
                self.assertLess(abs(sample.load.value), 500)
        pulse_during = np.asarray([s.pulse.value for s, a in zip(samples, during) if a], dtype=float)
        self.assertLess(np.std(pulse_during), 40.0)

    def test_saturation_value_and_flag_consistency(self):
        for scenario_id, limit, flag in (
            ("upper_saturation", 30_000, ClippingFlag.UPPER),
            ("lower_saturation", 1_000, ClippingFlag.LOWER),
        ):
            with self.subTest(scenario_id=scenario_id):
                config = get_scenario(scenario_id, random_seed=3)
                samples = list(SimulatorDataSource(config).samples())
                before, during, after = _window_masks(config)
                for sample, active in zip(samples, during):
                    if active:
                        self.assertEqual(sample.pulse.value, limit)
                        self.assertIs(sample.pulse.clipping, flag)
                for sample, inactive in zip(samples, before | after):
                    if inactive:
                        self.assertIs(sample.pulse.clipping, ClippingFlag.NONE)
                        self.assertNotEqual(sample.pulse.value, limit)

    def test_baseline_drift_trend_without_phase_break(self):
        config = get_scenario("baseline_drift", random_seed=15)
        samples = list(SimulatorDataSource(config).samples())
        normal = list(
            SimulatorDataSource(build_normal_high_quality(random_seed=15, duration_s=config.duration_s)).samples()
        )
        before, during, _ = _window_masks(config)
        pulse = np.asarray([s.pulse.value for s in samples], dtype=float)
        normal_pulse = np.asarray([s.pulse.value for s in normal], dtype=float)
        # Mid-window mean should rise versus pre-window mean for positive drift.
        self.assertGreater(np.mean(pulse[during]) - np.mean(pulse[before]), 800.0)
        self.assertTrue(np.all(np.isfinite(pulse)))
        # Detrended shapes should remain beat-like even while the baseline rises.
        detrend = pulse[during] - np.linspace(pulse[during][0], pulse[during][-1], during.sum())
        normal_detrend = normal_pulse[during] - np.mean(normal_pulse[during])
        corr = float(np.corrcoef(detrend, normal_detrend)[0, 1])
        self.assertGreater(corr, 0.55)
        lag_beat = int(round(config.sample_rate_hz * 60.0 / config.heart_rate_bpm))
        corr_beat = float(np.corrcoef(detrend[:-lag_beat], detrend[lag_beat:])[0, 1])
        self.assertGreater(corr_beat, 0.2)
        ppg = np.asarray([s.ppg.value for s in samples], dtype=float)
        normal_ppg = np.asarray([s.ppg.value for s in normal], dtype=float)
        np.testing.assert_array_equal(ppg, normal_ppg)

    def test_motion_artifact_windowed_and_bounded(self):
        config = get_scenario("motion_artifact", random_seed=21)
        samples = list(SimulatorDataSource(config).samples())
        normal = list(
            SimulatorDataSource(build_normal_high_quality(random_seed=21, duration_s=config.duration_s)).samples()
        )
        before, during, after = _window_masks(config)
        pulse = np.asarray([s.pulse.value for s in samples], dtype=float)
        load = np.asarray([s.load.value for s in samples], dtype=float)
        normal_pulse = np.asarray([s.pulse.value for s in normal], dtype=float)
        normal_load = np.asarray([s.load.value for s in normal], dtype=float)
        self.assertGreater(np.std(pulse[during]), 2.0 * np.std(normal_pulse[during]))
        self.assertGreater(np.std(load[during]), 2.0 * np.std(normal_load[during]))
        self.assertLess(np.max(np.abs(pulse - normal_pulse)), 20_000)
        np.testing.assert_allclose(pulse[before], normal_pulse[before], atol=0)
        np.testing.assert_allclose(pulse[after], normal_pulse[after], atol=0)
        ppg = np.asarray([s.ppg.value for s in samples], dtype=float)
        normal_ppg = np.asarray([s.ppg.value for s in normal], dtype=float)
        np.testing.assert_array_equal(ppg, normal_ppg)

    def test_unstable_load_connected_and_coupled(self):
        config = get_scenario("unstable_load", random_seed=19, pulse_coupling_scale=0.05)
        samples = list(SimulatorDataSource(config).samples())
        _, during, _ = _window_masks(config)
        load = np.asarray([s.load.value for s, a in zip(samples, during) if a], dtype=float)
        self.assertGreater(np.std(load), 5_000)
        self.assertTrue(all(s.load.status is SensorStatus.CONNECTED for s in samples))
        self.assertTrue(all(s.load.clipping is ClippingFlag.NONE for s in samples))
        self.assertTrue(all(s.device_state == "ACQUIRE" for s in samples))

    def test_ppg_misalignment_shifts_observation_not_timestamps(self):
        config = get_scenario("ppg_misalignment", random_seed=4, extra_delay_ms=180.0)
        samples = list(SimulatorDataSource(config).samples())
        normal = list(
            SimulatorDataSource(
                build_normal_high_quality(
                    random_seed=4,
                    duration_s=config.duration_s,
                    simulator_version=config.simulator_version,
                )
            ).samples()
        )
        before, during, after = _window_masks(config)
        # Timestamps and pulse values remain aligned with the delayed-version-matched normal clock path.
        self.assertEqual([s.device_time_us for s in samples], [s.device_time_us for s in normal])
        self.assertEqual([s.frame_sequence for s in samples], [s.frame_sequence for s in normal])
        pulse = np.asarray([s.pulse.value for s in samples], dtype=float)
        normal_pulse = np.asarray([s.pulse.value for s in normal], dtype=float)
        np.testing.assert_array_equal(pulse, normal_pulse)
        ppg = np.asarray([s.ppg.value for s in samples], dtype=float)
        normal_ppg = np.asarray([s.ppg.value for s in normal], dtype=float)
        # Outside the fault window PPG matches; inside it diverges due to extra delay.
        np.testing.assert_array_equal(ppg[before], normal_ppg[before])
        np.testing.assert_array_equal(ppg[after], normal_ppg[after])
        self.assertGreater(np.mean(np.abs(ppg[during] - normal_ppg[during])), 50.0)
        self.assertTrue(all(s.ppg.status is SensorStatus.CONNECTED for s in samples))
        self.assertTrue(all(s.ppg.clipping is ClippingFlag.NONE for s in samples))
        # Extra observation delay shifts the PPG waveform later on the shared clock.
        extra = int(round(0.180 * config.sample_rate_hz))
        fault_seg = ppg[during]
        normal_seg = normal_ppg[during]
        aligned = float(np.mean(np.abs(fault_seg[extra:] - normal_seg[:-extra])))
        unaligned = float(np.mean(np.abs(fault_seg - normal_seg)))
        wrong_way = float(np.mean(np.abs(fault_seg[:-extra] - normal_seg[extra:])))
        self.assertLess(aligned, 0.35 * unaligned)
        self.assertLess(aligned, wrong_way)

    def test_insufficient_duration_is_normal_but_short(self):
        config = get_scenario("insufficient_duration", random_seed=1001)
        samples = list(SimulatorDataSource(config).samples())
        self.assertEqual(config.fault_schedule, ())
        self.assertEqual(len(samples), sample_count(config.duration_s, config.sample_rate_hz))
        self.assertLess(len(samples), sample_count(8.0, 250.0))
        self.assertTrue(all(s.pulse.clipping is ClippingFlag.NONE for s in samples))
        self.assertTrue(all(s.fault_flags == () for s in samples))
        self.assertTrue(all(s.device_state == "ACQUIRE" for s in samples))
        self.assertTrue(all(math.isfinite(s.pulse.value) for s in samples))


if __name__ == "__main__":
    unittest.main()
