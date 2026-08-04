import numpy as np
import unittest

from digital_pulse.waveform import WaveformConfig, generate_waveform, pressure_gain


class WaveformTests(unittest.TestCase):
    def test_waveform_is_deterministic(self):
        config = WaveformConfig(seed=12)
        t1, y1 = generate_waveform(5.0, config)
        t2, y2 = generate_waveform(5.0, config)
        np.testing.assert_array_equal(t1, t2)
        np.testing.assert_array_equal(y1, y2)

    def test_pressure_gain_has_optimum(self):
        gains = pressure_gain(np.array([20.0, 80.0, 140.0]))
        self.assertGreater(gains[1], gains[0])
        self.assertGreater(gains[1], gains[2])

    def test_motion_event_changes_only_requested_run(self):
        config = WaveformConfig(seed=1, noise_std=0.0)
        _, clean = generate_waveform(4.0, config)
        _, moved = generate_waveform(4.0, config, motion_events=((1.0, 0.4, 2.0),))
        self.assertGreater(np.max(np.abs(clean - moved)), 0.5)


if __name__ == "__main__":
    unittest.main()
