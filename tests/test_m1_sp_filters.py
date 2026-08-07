from __future__ import annotations

import unittest

import numpy as np

from digital_pulse.m1_sp.filters import (
    CausalFIRFilter,
    OfflineReviewFilter,
    design_lowpass_fir,
)
from digital_pulse.m1_sp.models import NormalizedChannelSeries
from digital_pulse.m1_sp.parameters import default_p2c_parameter_set
from digital_pulse.m1_sp.filters import FilterBank


class M1SPFilterTests(unittest.TestCase):
    def setUp(self):
        self.kernel = design_lowpass_fir(15, cutoff_normalized=0.08)
        self.x = np.sin(2 * np.pi * np.arange(1000) / 50.0).astype(np.float64)

    def test_prefix_invariant(self):
        filt = CausalFIRFilter(self.kernel)
        y_full = filt.process(self.x.copy())
        filt.reset()
        y_prefix = filt.process(self.x[:500].copy())
        np.testing.assert_allclose(y_full[:500], y_prefix, rtol=0, atol=1e-10)

    def test_chunk_equals_whole(self):
        filt = CausalFIRFilter(self.kernel)
        y_whole = filt.process(self.x.copy())
        filt.reset()
        chunks = []
        for start in (0, 100, 350, 800):
            end = {0: 100, 100: 350, 350: 800, 800: 1000}[start]
            chunks.append(filt.process_chunk(self.x[start:end].copy()))
        y_chunk = np.concatenate(chunks)
        np.testing.assert_allclose(y_whole, y_chunk, rtol=0, atol=1e-10)

    def test_reset_reproducible(self):
        filt = CausalFIRFilter(self.kernel)
        a = filt.process(self.x.copy())
        filt.reset()
        b = filt.process(self.x.copy())
        np.testing.assert_array_equal(a, b)

    def test_constant_and_impulse(self):
        filt = CausalFIRFilter(self.kernel)
        const = np.ones(64, dtype=np.float64)
        y = filt.process(const)
        self.assertTrue(np.allclose(y[20:], 1.0, atol=1e-6))
        filt.reset()
        impulse = np.zeros(64, dtype=np.float64)
        impulse[10] = 1.0
        yi = filt.process(impulse)
        self.assertEqual(yi.shape, impulse.shape)
        self.assertGreater(float(np.max(np.abs(yi))), 0.0)

    def test_offline_length_and_mask(self):
        offline = OfflineReviewFilter(design_lowpass_fir(21, cutoff_normalized=0.08))
        y = offline.process(self.x)
        self.assertEqual(y.shape, self.x.shape)
        bank = FilterBank(default_p2c_parameter_set())
        channel = NormalizedChannelSeries(
            values=self.x,
            valid_mask=np.ones(self.x.shape[0], dtype=bool),
            clipping_lower_mask=np.zeros(self.x.shape[0], dtype=bool),
            clipping_upper_mask=np.zeros(self.x.shape[0], dtype=bool),
        )
        series = bank.offline_review(channel)
        self.assertEqual(series.mode, "offline_review")
        self.assertEqual(int(series.values.shape[0]), int(self.x.shape[0]))
        self.assertTrue(bool(np.all(series.valid_mask)))

    def test_nan_gap_separate_runs(self):
        bank = FilterBank(default_p2c_parameter_set())
        values = np.concatenate([np.ones(40), np.full(10, np.nan), np.ones(40)])
        valid = np.concatenate([np.ones(40, dtype=bool), np.zeros(10, dtype=bool), np.ones(40, dtype=bool)])
        channel = NormalizedChannelSeries(
            values=values.astype(np.float64),
            valid_mask=valid,
            clipping_lower_mask=np.zeros(90, dtype=bool),
            clipping_upper_mask=np.zeros(90, dtype=bool),
        )
        out = bank.causal(channel)
        self.assertFalse(bool(np.any(out.valid_mask[40:50])))
        self.assertTrue(bool(np.all(np.isfinite(out.values[out.valid_mask]))))

    def test_group_delay_metadata(self):
        filt = CausalFIRFilter(self.kernel)
        self.assertEqual(filt.group_delay_samples, 7)
        self.assertEqual(filt.num_taps, 15)


if __name__ == "__main__":
    unittest.main()
