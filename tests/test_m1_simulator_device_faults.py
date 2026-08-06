from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import ClippingFlag, SensorStatus
from digital_pulse.m1_simulator import SimulatorDataSource, get_scenario


class M1SimulatorDeviceFaultTests(unittest.TestCase):
    def test_sensor_disconnection_terminal_sample(self):
        config = get_scenario("sensor_disconnection", duration_s=2.0, random_seed=3, trigger_frame_sequence=100)
        samples = list(SimulatorDataSource(config).samples())
        self.assertEqual(len(samples), 101)
        self.assertTrue(all(s.device_state == "ACQUIRE" and s.fault_flags == () for s in samples[:-1]))
        last = samples[-1]
        self.assertEqual(last.frame_sequence, 100)
        self.assertIs(last.pulse.status, SensorStatus.DISCONNECTED)
        self.assertIsNone(last.pulse.value)
        self.assertIs(last.pulse.clipping, ClippingFlag.NONE)
        self.assertIs(last.load.status, SensorStatus.CONNECTED)
        self.assertIsNotNone(last.load.value)
        self.assertIs(last.ppg.status, SensorStatus.CONNECTED)
        self.assertEqual(last.device_state, "FAULT")
        self.assertIn("sensor_disconnected", last.fault_flags)
        self.assertTrue(last.receive_integrity.crc_valid)
        self.assertTrue(last.receive_integrity.sequence_valid)
        self.assertTrue(last.receive_integrity.timestamp_valid)
        again = list(SimulatorDataSource(config).samples())
        self.assertEqual([s.to_dict() for s in samples], [s.to_dict() for s in again])

    def test_abort_safe_hold_and_stop(self):
        config = get_scenario("abort", duration_s=2.0, random_seed=4, trigger_frame_sequence=80)
        samples = list(SimulatorDataSource(config).samples())
        self.assertEqual(len(samples), 81)
        self.assertTrue(all(s.device_state == "ACQUIRE" for s in samples[:-1]))
        last = samples[-1]
        self.assertEqual(last.device_state, "SAFE_HOLD")
        self.assertIn("emergency_stop", last.fault_flags)
        self.assertTrue(last.receive_integrity.timestamp_valid)
        self.assertFalse(any(s.device_state == "ACQUIRE" and s.frame_sequence > 80 for s in samples))
        self.assertEqual(last.frame_sequence, 80)

    def test_device_fault_uses_schema_flag(self):
        config = get_scenario("device_fault", duration_s=2.0, random_seed=6, trigger_frame_sequence=90)
        samples = list(SimulatorDataSource(config).samples())
        last = samples[-1]
        self.assertEqual(last.device_state, "FAULT")
        self.assertEqual(last.fault_flags, ("buffer_overflow",))
        self.assertEqual(len(samples), 91)
        for sample in samples:
            sample.validate_schema()


if __name__ == "__main__":
    unittest.main()
