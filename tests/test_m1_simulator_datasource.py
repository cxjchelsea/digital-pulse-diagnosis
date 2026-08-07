from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import SourceType
from digital_pulse.m1_simulator import SimulatorDataSource, build_normal_high_quality, get_scenario
from digital_pulse.m1_simulator.config import sample_count


class M1SimulatorDataSourceTests(unittest.TestCase):
    def test_samples_are_formal_m1_and_schema_valid(self):
        config = build_normal_high_quality(duration_s=1.0, sample_rate_hz=100.0, random_seed=1001)
        source = SimulatorDataSource(config)
        samples = list(source.samples())
        self.assertEqual(len(samples), sample_count(1.0, 100.0))
        self.assertEqual(source.source_type, SourceType.SIMULATOR.value)
        for sample in samples:
            self.assertEqual(sample.source_type, SourceType.SIMULATOR)
            self.assertEqual(sample.device_state, "ACQUIRE")
            self.assertEqual(sample.fault_flags, ())
            self.assertTrue(sample.receive_integrity.crc_valid)
            self.assertTrue(sample.receive_integrity.sequence_valid)
            self.assertTrue(sample.receive_integrity.timestamp_valid)
            self.assertIsNone(sample.target_load_raw)
            self.assertIsNone(sample.motor_position_raw)
            self.assertEqual(sample.pulse.status.value, "connected")
            self.assertEqual(sample.load.status.value, "connected")
            self.assertEqual(sample.ppg.status.value, "connected")
            sample.validate_schema()

    def test_sequence_time_and_finite_iteration(self):
        source = SimulatorDataSource(build_normal_high_quality(duration_s=0.5, sample_rate_hz=200.0))
        samples = list(source.samples())
        self.assertEqual([sample.frame_sequence for sample in samples], list(range(len(samples))))
        device_times = [sample.device_time_us for sample in samples]
        self.assertEqual(device_times, sorted(set(device_times)))
        self.assertTrue(all(later > earlier for earlier, later in zip(device_times, device_times[1:])))

    def test_samples_call_regenerates_deterministically(self):
        source = SimulatorDataSource(get_scenario("normal_high_quality", duration_s=1.0, random_seed=17))
        first = [sample.to_dict() for sample in source.samples()]
        second = [sample.to_dict() for sample in source.samples()]
        self.assertEqual(first, second)

    def test_different_seeds_change_noise_not_semantics(self):
        a = list(SimulatorDataSource(build_normal_high_quality(duration_s=1.0, random_seed=1)).samples())
        b = list(SimulatorDataSource(build_normal_high_quality(duration_s=1.0, random_seed=2)).samples())
        self.assertEqual(len(a), len(b))
        self.assertNotEqual([s.pulse.value for s in a], [s.pulse.value for s in b])
        self.assertTrue(all(sample.fault_flags == () for sample in a + b))
        self.assertTrue(all(sample.device_state == "ACQUIRE" for sample in a + b))

    def test_three_channels_share_frame_clock(self):
        samples = list(SimulatorDataSource(build_normal_high_quality(duration_s=0.2, sample_rate_hz=250.0)).samples())
        for sample in samples:
            self.assertIsInstance(sample.pulse.value, int)
            self.assertIsInstance(sample.load.value, int)
            self.assertIsInstance(sample.ppg.value, int)
            self.assertEqual(sample.host_received_at_utc.count("T"), 1)


if __name__ == "__main__":
    unittest.main()
