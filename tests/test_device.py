import unittest

from digital_pulse.device import DeviceSimulator, PressureStep, SimulationConfig
from digital_pulse.protocol import DeviceState, decode_frame


class DeviceTests(unittest.TestCase):
    def test_device_profile_produces_continuous_sequences(self):
        simulator = DeviceSimulator(SimulationConfig(sample_rate_hz=100))
        profile = (PressureStep(50, 0.2, 0.5), PressureStep(80, 0.2, 0.5))
        samples = list(simulator.samples(profile))
        self.assertEqual(len(samples), 140)
        self.assertEqual([sample.frame_sequence for sample in samples], list(range(140)))
        self.assertTrue(any(sample.device_state is DeviceState.ACQUIRE for sample in samples))
        self.assertEqual(samples[-1].target_force, 80_000)

    def test_generated_frames_decode(self):
        simulator = DeviceSimulator(SimulationConfig(sample_rate_hz=50))
        frames = list(simulator.frames((PressureStep(60, 0.1, 0.2),)))
        decoded = [decode_frame(frame).sample for frame in frames]
        self.assertTrue(all(sample is not None for sample in decoded))
        self.assertEqual(decoded[0].frame_sequence, 0)


if __name__ == "__main__":
    unittest.main()
