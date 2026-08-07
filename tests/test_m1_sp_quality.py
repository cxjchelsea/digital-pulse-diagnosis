from __future__ import annotations

import unittest

import numpy as np

from digital_pulse.m1_contracts import QualityLabel, RawPersistenceStatus, SourceType
from digital_pulse.m1_sp.models import (
    IntegrityAnalysis,
    IntegrityConsistency,
    NormalizedChannelSeries,
    NormalizedSession,
    QualityMetricsInternal,
    StableWindow,
)
from digital_pulse.m1_sp.parameters import default_p2b_parameter_set
from digital_pulse.m1_sp.processor import SPQualityProcessor
from digital_pulse.m1_sp.quality import QualityEvaluator

from _m1_sp_helpers import record_scenario


def _metrics(**overrides) -> QualityMetricsInternal:
    base = dict(
        valid_fraction=1.0,
        clipping_fraction=0.0,
        baseline_drift_raw=0.0,
        pulse_std_raw=700.0,
        lower_clipping_fraction=0.0,
        upper_clipping_fraction=0.0,
        load_median_raw=80000.0,
        load_std_raw=12.0,
        load_range_raw=80.0,
        load_slope_raw_per_s=0.0,
        motion_metric=40.0,
        near_constant_metric=700.0,
        valid_sample_count=100,
        total_sample_count=100,
        evidence=(),
    )
    base.update(overrides)
    return QualityMetricsInternal(**base)


def _channel(n: int, value: float = 100.0) -> NormalizedChannelSeries:
    return NormalizedChannelSeries(
        values=np.full(n, value, dtype=np.float64),
        valid_mask=np.ones(n, dtype=bool),
        clipping_lower_mask=np.zeros(n, dtype=bool),
        clipping_upper_mask=np.zeros(n, dtype=bool),
    )


def _normalized(n: int = 32) -> NormalizedSession:
    return NormalizedSession(
        session_id="s-quality",
        source_type=SourceType.SIMULATOR,
        sample_rate_hz=250.0,
        frame_sequence=np.arange(n, dtype=np.int64),
        device_time_us=np.arange(n, dtype=np.int64) * 4000,
        host_received_at_utc=tuple(f"t{i}" for i in range(n)),
        pulse=_channel(n, 16000.0),
        load=_channel(n, 80000.0),
        ppg=_channel(n, 20000.0),
        device_state=tuple("ACQUIRE" for _ in range(n)),
        fault_flags=tuple(() for _ in range(n)),
        crc_valid=np.ones(n, dtype=np.int8),
        sequence_valid=np.ones(n, dtype=np.int8),
        timestamp_valid=np.ones(n, dtype=np.int8),
    )


def _window(n: int = 32, *, duration_s: float = 8.0) -> StableWindow:
    return StableWindow(
        window_id="window-0001",
        start_index=0,
        end_index=n,
        start_device_time_us=0,
        end_device_time_us=int(duration_s * 1e6),
        sample_count=n,
        duration_s=duration_s,
    )


def _clean_integrity(n: int = 32) -> IntegrityAnalysis:
    return IntegrityAnalysis(
        sample_count=n,
        crc_error_count=0,
        sequence_error_count=0,
        missing_frame_count=0,
        timestamp_error_count=0,
        sensor_disconnection_count=0,
        session_completed=True,
        raw_persistence_status=RawPersistenceStatus.OK,
        integrity_ok=True,
        pre_quality_blocked=False,
        consistency=IntegrityConsistency.CONSISTENT,
        evidence=(),
        blocking_codes=(),
        sequence_anomaly_mask=tuple(False for _ in range(n)),
        timestamp_anomaly_mask=tuple(False for _ in range(n)),
    )


class M1SPQualityPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.profile = default_p2b_parameter_set()
        self.eval = QualityEvaluator()
        self._tmp, _path, self.session, _samples = record_scenario(
            "normal_high_quality", duration_s=0.2, random_seed=1
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _eval(self, metrics: QualityMetricsInternal, *, duration_s: float = 8.0):
        return self.eval.evaluate_window(
            session=self.session,
            normalized=_normalized(),
            integrity=_clean_integrity(),
            window=_window(duration_s=duration_s),
            metrics=metrics,
            profile=self.profile,
        )

    def test_weak_plus_saturation_is_saturated(self):
        result = self._eval(
            _metrics(pulse_std_raw=500.0, clipping_fraction=0.2, upper_clipping_fraction=0.2)
        )
        self.assertEqual(result.primary_label, QualityLabel.SATURATED)

    def test_weak_plus_baseline_is_baseline(self):
        result = self._eval(_metrics(pulse_std_raw=500.0, baseline_drift_raw=1200.0))
        self.assertEqual(result.primary_label, QualityLabel.UNSTABLE_BASELINE)

    def test_baseline_plus_motion_is_motion(self):
        result = self._eval(_metrics(baseline_drift_raw=1200.0, motion_metric=200.0))
        self.assertEqual(result.primary_label, QualityLabel.MOTION_ARTIFACT)

    def test_weak_plus_no_contact_is_no_contact(self):
        result = self._eval(
            _metrics(pulse_std_raw=500.0, load_median_raw=40000.0, near_constant_metric=500.0)
        )
        self.assertEqual(result.primary_label, QualityLabel.NO_CONTACT)

    def test_unstable_load_plus_weak_is_weak(self):
        result = self._eval(_metrics(pulse_std_raw=500.0, load_std_raw=9000.0))
        self.assertEqual(result.primary_label, QualityLabel.WEAK_SIGNAL)

    def test_threshold_boundary_weak(self):
        just_at = self._eval(_metrics(pulse_std_raw=620.0))
        just_above = self._eval(_metrics(pulse_std_raw=620.0001))
        self.assertEqual(just_at.primary_label, QualityLabel.WEAK_SIGNAL)
        self.assertEqual(just_above.primary_label, QualityLabel.ACCEPTABLE)


class M1SPQualityScenarioTests(unittest.TestCase):
    def test_scenario_matrix_seed_1001(self):
        proc = SPQualityProcessor()
        expected = {
            "normal_high_quality": ("quality_evaluated", "acceptable", []),
            "weak_signal": ("quality_evaluated", "weak_signal", ["weak_amplitude"]),
            "no_contact": ("quality_evaluated", "no_contact", ["near_constant", "no_contact"]),
            "upper_saturation": ("quality_evaluated", "saturated", ["upper_saturation"]),
            "lower_saturation": ("quality_evaluated", "saturated", ["lower_saturation"]),
            "baseline_drift": ("quality_evaluated", "unstable_baseline", ["unstable_baseline"]),
            "motion_artifact": ("quality_evaluated", "motion_artifact", ["motion_artifact"]),
            "unstable_load": ("quality_evaluated", "manual_review_required", ["manual_review_requested"]),
            "insufficient_duration": ("quality_evaluated", "insufficient_duration", ["too_short"]),
            "frame_loss": ("quality_evaluated", "data_integrity_failure", ["sequence_gaps"]),
            "timestamp_regression": ("quality_evaluated", "data_integrity_failure", ["timestamp_errors"]),
            "sensor_disconnection": ("quality_evaluated", "data_integrity_failure", ["sensor_disconnected"]),
            "raw_persistence_failure": ("quality_evaluated", "data_integrity_failure", ["persistence_failed"]),
            "abort": ("blocked_before_quality", None, None),
            "device_fault": ("blocked_before_quality", None, None),
        }
        for case, (status, label, reasons) in expected.items():
            duration = 1.0 if case == "insufficient_duration" else 8.0
            tmp, _path, session, samples = record_scenario(case, duration_s=duration, random_seed=1001)
            try:
                out = proc.process(session, samples)
                self.assertEqual(out.processing_status, status, case)
                if label is None:
                    self.assertEqual(out.quality_results, ())
                else:
                    self.assertEqual(out.quality_results[0].label.value, label, case)
                    self.assertEqual(list(out.quality_results[0].reason_codes), reasons, case)
            finally:
                tmp.cleanup()

    def test_sensor_disconnect_not_no_contact(self):
        proc = SPQualityProcessor()
        tmp_nc, _, session_nc, samples_nc = record_scenario("no_contact", duration_s=8.0, random_seed=1001)
        tmp_sd, _, session_sd, samples_sd = record_scenario(
            "sensor_disconnection", duration_s=8.0, random_seed=1001
        )
        try:
            nc = proc.process(session_nc, samples_nc)
            sd = proc.process(session_sd, samples_sd)
            self.assertEqual(nc.quality_results[0].label, QualityLabel.NO_CONTACT)
            self.assertEqual(sd.quality_results[0].label, QualityLabel.DATA_INTEGRITY_FAILURE)
            self.assertIn("sensor_disconnected", sd.quality_results[0].reason_codes)
            self.assertFalse(sd.preprocessing.integrity.pre_quality_blocked)
        finally:
            tmp_nc.cleanup()
            tmp_sd.cleanup()

    def test_integrity_beats_pretty_waveform(self):
        proc = SPQualityProcessor()
        for case in ("frame_loss", "timestamp_regression", "sensor_disconnection", "raw_persistence_failure"):
            tmp, _, session, samples = record_scenario(case, duration_s=8.0, random_seed=1002)
            try:
                out = proc.process(session, samples)
                self.assertEqual(out.quality_results[0].label, QualityLabel.DATA_INTEGRITY_FAILURE, case)
            finally:
                tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
