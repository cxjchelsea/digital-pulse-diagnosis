from __future__ import annotations

import math
import unittest

import numpy as np

from digital_pulse.m1_contracts import SourceType
from digital_pulse.m1_sp.errors import SPError
from digital_pulse.m1_sp.metrics import PULSE_STD_DDOF, RawQualityMetrics, _baseline_drift_raw, _motion_metric_v1
from digital_pulse.m1_sp.models import NormalizedChannelSeries, NormalizedSession, StableWindow
from digital_pulse.m1_sp.parameters import default_p2b_parameter_set


def _channel(values, *, valid=None, lower=None, upper=None) -> NormalizedChannelSeries:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.shape[0]
    return NormalizedChannelSeries(
        values=arr,
        valid_mask=np.ones(n, dtype=bool) if valid is None else np.asarray(valid, dtype=bool),
        clipping_lower_mask=np.zeros(n, dtype=bool) if lower is None else np.asarray(lower, dtype=bool),
        clipping_upper_mask=np.zeros(n, dtype=bool) if upper is None else np.asarray(upper, dtype=bool),
    )


def _session(n: int, *, pulse=None, load=None) -> NormalizedSession:
    pulse = pulse if pulse is not None else _channel(np.arange(n, dtype=np.float64) + 100.0)
    load = load if load is not None else _channel(np.full(n, 80000.0))
    return NormalizedSession(
        session_id="s-metrics",
        source_type=SourceType.SIMULATOR,
        sample_rate_hz=250.0,
        frame_sequence=np.arange(n, dtype=np.int64),
        device_time_us=(np.arange(n, dtype=np.int64) * 4000),
        host_received_at_utc=tuple(f"t{i}" for i in range(n)),
        pulse=pulse,
        load=load,
        ppg=_channel(np.full(n, 20000.0)),
        device_state=tuple("ACQUIRE" for _ in range(n)),
        fault_flags=tuple(() for _ in range(n)),
        crc_valid=np.ones(n, dtype=np.int8),
        sequence_valid=np.ones(n, dtype=np.int8),
        timestamp_valid=np.ones(n, dtype=np.int8),
    )


def _window(n: int) -> StableWindow:
    return StableWindow(
        window_id="window-0001",
        start_index=0,
        end_index=n,
        start_device_time_us=0,
        end_device_time_us=(n - 1) * 4000 if n else 0,
        sample_count=n,
        duration_s=((n - 1) * 4000) / 1e6 if n else 0.0,
    )


class M1SPMetricsTests(unittest.TestCase):
    def test_valid_fraction_manual_mask(self):
        pulse = _channel([1.0, 2.0, 3.0, 4.0], valid=[True, True, False, True])
        metrics = RawQualityMetrics().compute(_session(4, pulse=pulse), _window(4), default_p2b_parameter_set())
        self.assertEqual(metrics.valid_fraction, 0.75)
        self.assertEqual(metrics.valid_sample_count, 3)
        self.assertEqual(metrics.total_sample_count, 4)

    def test_clipping_fraction_lower_upper(self):
        pulse = _channel(
            [10.0, 20.0, 30.0, 40.0],
            valid=[True, True, True, False],
            lower=[True, False, False, False],
            upper=[False, True, False, True],
        )
        metrics = RawQualityMetrics().compute(_session(4, pulse=pulse), _window(4), default_p2b_parameter_set())
        self.assertEqual(metrics.clipping_fraction, 2 / 3)
        self.assertEqual(metrics.lower_clipping_fraction, 1 / 3)
        self.assertEqual(metrics.upper_clipping_fraction, 1 / 3)

    def test_pulse_std_raw_ddof0(self):
        values = np.array([1.0, 3.0, 5.0, 7.0], dtype=np.float64)
        pulse = _channel(values)
        metrics = RawQualityMetrics().compute(_session(4, pulse=pulse), _window(4), default_p2b_parameter_set())
        self.assertEqual(PULSE_STD_DDOF, 0)
        self.assertAlmostEqual(metrics.pulse_std_raw, float(np.std(values, ddof=0)))

    def test_baseline_drift_segment_excursion(self):
        # Low / high / low segments → signed excursion uses extreme segment order.
        values = np.concatenate(
            [
                np.full(20, 100.0),
                np.full(20, 300.0),
                np.full(20, 100.0),
            ]
        )
        drift = _baseline_drift_raw(values, segment_fraction=0.2, minimum_segment_samples=8)
        self.assertIsNotNone(drift)
        self.assertGreaterEqual(abs(drift), 190.0)

    def test_motion_metric_smooth_vs_rough(self):
        smooth = np.linspace(0.0, 10.0, 50)
        rough = smooth.copy()
        rough[::2] += 40.0
        self.assertLess(_motion_metric_v1(smooth), _motion_metric_v1(rough))

    def test_load_metrics_constant_oscillating_slope(self):
        n = 20
        const = _channel(np.full(n, 80000.0))
        osc = _channel(80000.0 + 1000.0 * np.sin(np.linspace(0, 6.28, n)))
        slope = _channel(np.linspace(70000.0, 90000.0, n))
        m_const = RawQualityMetrics().compute(_session(n, load=const), _window(n), default_p2b_parameter_set())
        m_osc = RawQualityMetrics().compute(_session(n, load=osc), _window(n), default_p2b_parameter_set())
        m_slope = RawQualityMetrics().compute(_session(n, load=slope), _window(n), default_p2b_parameter_set())
        self.assertLess(m_const.load_std_raw, 1e-9)
        self.assertGreater(m_osc.load_std_raw, 100.0)
        self.assertGreater(m_osc.load_range_raw, 1000.0)
        self.assertGreater(m_slope.load_slope_raw_per_s, 0.0)

    def test_no_valid_pulse_returns_controlled_none(self):
        pulse = _channel([1.0, 2.0, 3.0], valid=[False, False, False])
        metrics = RawQualityMetrics().compute(_session(3, pulse=pulse), _window(3), default_p2b_parameter_set())
        self.assertEqual(metrics.valid_fraction, 0.0)
        self.assertIsNone(metrics.clipping_fraction)
        self.assertIsNone(metrics.pulse_std_raw)
        self.assertIsNone(metrics.baseline_drift_raw)
        self.assertIsNone(metrics.motion_metric)

    def test_empty_window_is_error(self):
        with self.assertRaises(SPError):
            RawQualityMetrics().compute(_session(4), StableWindow("w", 2, 2, 0, 0, 0, 0.0), default_p2b_parameter_set())

    def test_one_valid_pulse_finite(self):
        pulse = _channel([5.0, 9.0], valid=[True, False])
        metrics = RawQualityMetrics().compute(_session(2, pulse=pulse), _window(2), default_p2b_parameter_set())
        self.assertEqual(metrics.pulse_std_raw, 0.0)
        self.assertTrue(math.isfinite(metrics.valid_fraction))


if __name__ == "__main__":
    unittest.main()
