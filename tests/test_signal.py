import numpy as np
import unittest

from digital_pulse.signal import assess_quality, detect_peaks, estimate_heart_rate, remove_baseline
from digital_pulse.waveform import WaveformConfig, generate_waveform


class SignalTests(unittest.TestCase):
    def test_estimated_heart_rate_matches_synthetic_signal(self):
        sample_rate = 250.0
        _, signal = generate_waveform(
            20.0,
            WaveformConfig(sample_rate_hz=sample_rate, heart_rate_bpm=75.0, seed=4),
        )
        corrected = remove_baseline(signal, sample_rate)
        peaks = detect_peaks(corrected, sample_rate)
        heart_rate = estimate_heart_rate(peaks, sample_rate)
        self.assertIsNotNone(heart_rate)
        self.assertLess(abs(heart_rate - 75.0), 1.0)

    def test_quality_rejects_constant_signal(self):
        result = assess_quality(np.ones(1000), 250.0)
        self.assertEqual(result.label, "no_signal")
        self.assertEqual(result.score, 0.0)

    def test_quality_accepts_clean_synthetic_signal(self):
        _, signal = generate_waveform(
            10.0,
            WaveformConfig(sample_rate_hz=250.0, heart_rate_bpm=72.0, noise_std=0.005),
        )
        result = assess_quality(signal, 250.0)
        self.assertEqual(result.label, "good")


if __name__ == "__main__":
    unittest.main()
