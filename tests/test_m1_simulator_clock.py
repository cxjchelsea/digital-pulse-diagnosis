from __future__ import annotations

import unittest

from digital_pulse.m1_simulator import build_normal_high_quality
from digital_pulse.m1_simulator.clock import DeterministicClock
from digital_pulse.m1_simulator.config import sample_count


class M1SimulatorClockTests(unittest.TestCase):
    def test_sample_count_and_monotonic_times(self):
        config = build_normal_high_quality(duration_s=1.0, sample_rate_hz=250.0)
        clock = DeterministicClock(config)
        self.assertEqual(clock.sample_count, sample_count(1.0, 250.0))
        ticks = list(clock.iter_ticks())
        self.assertEqual(len(ticks), 250)
        self.assertEqual([tick.frame_sequence for tick in ticks], list(range(250)))
        device_times = [tick.device_time_us for tick in ticks]
        self.assertEqual(device_times, sorted(device_times))
        self.assertTrue(all(later > earlier for earlier, later in zip(device_times, device_times[1:])))
        hosts = [tick.host_received_at_utc for tick in ticks]
        self.assertTrue(all(later > earlier for earlier, later in zip(hosts, hosts[1:])))
        self.assertEqual(ticks[0].device_time_us, 0)
        self.assertEqual(ticks[-1].device_time_us, round(249 * 1_000_000 / 250))

    def test_non_integer_period_has_no_cumulative_truncation_drift(self):
        config = build_normal_high_quality(duration_s=1.0, sample_rate_hz=333.0)
        clock = DeterministicClock(config)
        ticks = list(clock.iter_ticks())
        expected = [round(index * 1_000_000 / 333.0) for index in range(len(ticks))]
        self.assertEqual([tick.device_time_us for tick in ticks], expected)
        self.assertEqual(ticks[-1].device_time_us, round((len(ticks) - 1) * 1_000_000 / 333.0))

    def test_same_config_is_deterministic(self):
        config = build_normal_high_quality(duration_s=0.5, sample_rate_hz=200.0, random_seed=7)
        first = [(tick.frame_sequence, tick.device_time_us, tick.host_received_at_utc) for tick in DeterministicClock(config).iter_ticks()]
        second = [(tick.frame_sequence, tick.device_time_us, tick.host_received_at_utc) for tick in DeterministicClock(config).iter_ticks()]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
