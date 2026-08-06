from __future__ import annotations

import unittest

from digital_pulse.m1_simulator import (
    M1SimulatorConfigError,
    SimulatorDataSource,
    build_normal_high_quality,
    get_scenario,
)
from digital_pulse.m1_simulator.transport import FrameLossPlan, TimestampRegressionPlan


class M1SimulatorTransportFaultTests(unittest.TestCase):
    def test_frame_loss_gap_and_sequence_flag(self):
        config = get_scenario("frame_loss", duration_s=2.0, random_seed=7, lost_frame_count=3)
        plan = config.transport_fault_schedule[0]
        assert isinstance(plan, FrameLossPlan)
        samples = list(SimulatorDataSource(config).samples())
        sequences = [s.frame_sequence for s in samples]
        self.assertEqual(len(samples), 500 - 3)
        lost = set(range(plan.start_frame_sequence, plan.start_frame_sequence + plan.lost_frame_count))
        self.assertTrue(lost.isdisjoint(sequences))
        # No renumbering: max sequence still reaches original last frame.
        self.assertEqual(max(sequences), 499)
        first_after = next(s for s in samples if s.frame_sequence == plan.start_frame_sequence + 3)
        self.assertFalse(first_after.receive_integrity.sequence_valid)
        self.assertTrue(first_after.receive_integrity.crc_valid)
        self.assertTrue(first_after.receive_integrity.timestamp_valid)
        later = next(s for s in samples if s.frame_sequence == plan.start_frame_sequence + 4)
        self.assertTrue(later.receive_integrity.sequence_valid)

    def test_frame_loss_preserves_channel_values_for_kept_frames(self):
        seed = 11
        lost_config = get_scenario("frame_loss", duration_s=2.0, random_seed=seed, lost_frame_count=2)
        normal = build_normal_high_quality(
            duration_s=2.0,
            random_seed=seed,
            simulator_version=lost_config.simulator_version,
        )
        lost_by_seq = {s.frame_sequence: s for s in SimulatorDataSource(lost_config).samples()}
        normal_by_seq = {s.frame_sequence: s for s in SimulatorDataSource(normal).samples()}
        plan = lost_config.transport_fault_schedule[0]
        for seq, sample in lost_by_seq.items():
            if plan.start_frame_sequence <= seq < plan.start_frame_sequence + plan.lost_frame_count:
                self.fail("lost frame should not be emitted")
            ref = normal_by_seq[seq]
            self.assertEqual(sample.pulse.value, ref.pulse.value)
            self.assertEqual(sample.load.value, ref.load.value)
            self.assertEqual(sample.ppg.value, ref.ppg.value)

    def test_frame_loss_seed_does_not_move_gap(self):
        a = get_scenario("frame_loss", duration_s=2.0, random_seed=1).transport_fault_schedule[0]
        b = get_scenario("frame_loss", duration_s=2.0, random_seed=99).transport_fault_schedule[0]
        self.assertEqual(a.start_frame_sequence, b.start_frame_sequence)
        self.assertEqual(a.lost_frame_count, b.lost_frame_count)

    def test_timestamp_regression_semantics(self):
        config = get_scenario(
            "timestamp_regression",
            duration_s=2.0,
            random_seed=5,
            frame_sequence=200,
            regression_us=4000,
        )
        samples = list(SimulatorDataSource(config).samples())
        self.assertEqual([s.frame_sequence for s in samples], list(range(500)))
        target = samples[200]
        previous = samples[199]
        self.assertLess(target.device_time_us, previous.device_time_us)
        self.assertGreaterEqual(target.device_time_us, 0)
        self.assertFalse(target.receive_integrity.timestamp_valid)
        self.assertTrue(target.receive_integrity.sequence_valid)
        self.assertTrue(target.receive_integrity.crc_valid)
        self.assertTrue(samples[201].receive_integrity.timestamp_valid)
        host_times = [s.host_received_at_utc for s in samples]
        self.assertEqual(host_times, sorted(host_times))
        # Channels equal to normal at same seed/version.
        normal = build_normal_high_quality(
            duration_s=2.0,
            random_seed=5,
            simulator_version=config.simulator_version,
        )
        normal_samples = list(SimulatorDataSource(normal).samples())
        self.assertEqual([s.pulse.value for s in samples], [s.pulse.value for s in normal_samples])

    def test_invalid_transport_plans_fail(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "exceeds session|leave at least one"):
            get_scenario(
                "frame_loss",
                duration_s=1.0,
                transport_fault_schedule=(FrameLossPlan(start_frame_sequence=249, lost_frame_count=2),),
            )
        with self.assertRaisesRegex(M1SimulatorConfigError, "negative"):
            # Force an impossible regression relative to first usable previous time by huge value.
            cfg = get_scenario(
                "timestamp_regression",
                duration_s=1.0,
                frame_sequence=1,
                regression_us=10**12,
            )
            list(SimulatorDataSource(cfg).samples())


if __name__ == "__main__":
    unittest.main()
