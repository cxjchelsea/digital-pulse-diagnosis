from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import QualityLabel
from digital_pulse.m1_sp.models import BeatReferenceBundle, QualityMetricsInternal
from digital_pulse.m1_sp.parameters import default_p2c_parameter_set
from digital_pulse.m1_sp.processor import create_p2c_processor
from digital_pulse.m1_sp.quality import QualityEvaluator

from _m1_sp_helpers import record_scenario
from test_m1_sp_quality import _clean_integrity, _normalized, _window


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
        beat_count=10,
        ppg_match_rate=1.0,
    )
    base.update(overrides)
    return QualityMetricsInternal(**base)


def _bref(**overrides) -> BeatReferenceBundle:
    base = dict(
        beat_count=10,
        interval_cv=0.05,
        ppg_match_rate=1.0,
        reference_available=True,
        lag_mad_ms=2.0,
        median_lag_ms=76.0,
        ppg_valid_fraction=1.0,
    )
    base.update(overrides)
    return BeatReferenceBundle(**base)


class M1SPP2CQualityTests(unittest.TestCase):
    def setUp(self):
        self.profile = default_p2c_parameter_set()
        self.eval = QualityEvaluator()
        self._tmp, _, self.session, _ = record_scenario("normal_high_quality", duration_s=0.2, random_seed=1)

    def tearDown(self):
        self._tmp.cleanup()

    def _eval(self, metrics, beat_ref, *, duration_s=8.0):
        from test_m1_sp_quality import _clean_integrity, _normalized, _window

        return self.eval.evaluate_window(
            session=self.session,
            normalized=_normalized(),
            integrity=_clean_integrity(),
            window=_window(duration_s=duration_s),
            metrics=metrics,
            profile=self.profile,
            beat_ref=beat_ref,
        )

    def test_weak_plus_reference_mismatch_stays_weak(self):
        result = self._eval(
            _metrics(pulse_std_raw=500.0),
            _bref(ppg_match_rate=0.2),
        )
        self.assertEqual(result.primary_label, QualityLabel.WEAK_SIGNAL)

    def test_motion_plus_unstable_intervals_stays_motion(self):
        result = self._eval(
            _metrics(motion_metric=200.0),
            _bref(interval_cv=0.9),
        )
        self.assertEqual(result.primary_label, QualityLabel.MOTION_ARTIFACT)

    def test_baseline_plus_insufficient_beats_stays_baseline(self):
        result = self._eval(
            _metrics(baseline_drift_raw=1200.0),
            _bref(beat_count=1),
        )
        self.assertEqual(result.primary_label, QualityLabel.UNSTABLE_BASELINE)

    def test_acceptable_raw_insufficient_beats(self):
        result = self._eval(_metrics(), _bref(beat_count=2))
        self.assertEqual(result.primary_label, QualityLabel.INSUFFICIENT_DURATION)
        self.assertIn("insufficient_beats", result.reason_codes)

    def test_acceptable_raw_unstable_intervals(self):
        result = self._eval(_metrics(), _bref(interval_cv=0.9))
        self.assertEqual(result.primary_label, QualityLabel.MANUAL_REVIEW_REQUIRED)
        self.assertIn("unstable_intervals", result.reason_codes)

    def test_acceptable_raw_ppg_unavailable(self):
        result = self._eval(
            _metrics(),
            _bref(reference_available=False, ppg_match_rate=None, ppg_valid_fraction=0.0),
        )
        self.assertEqual(result.primary_label, QualityLabel.MANUAL_REVIEW_REQUIRED)
        self.assertIn("reference_unavailable", result.reason_codes)

    def test_acceptable_raw_ppg_mismatch(self):
        result = self._eval(_metrics(), _bref(ppg_match_rate=0.2))
        self.assertEqual(result.primary_label, QualityLabel.REFERENCE_MISMATCH)

    def test_fully_good_acceptable(self):
        result = self._eval(_metrics(), _bref())
        self.assertEqual(result.primary_label, QualityLabel.ACCEPTABLE)

    def test_scenario_matrix_includes_ppg_misalignment(self):
        proc = create_p2c_processor()
        expected = {
            "normal_high_quality": "acceptable",
            "weak_signal": "weak_signal",
            "ppg_misalignment": "reference_mismatch",
            "motion_artifact": "motion_artifact",
            "unstable_load": "manual_review_required",
            "abort": None,
        }
        for case, label in expected.items():
            tmp, _, session, samples = record_scenario(case, duration_s=8.0, random_seed=1001)
            try:
                out = proc.process(session, samples)
                if label is None:
                    self.assertEqual(out.quality_results, ())
                    self.assertEqual(out.processing_status, "blocked_before_quality")
                else:
                    self.assertEqual(out.quality_results[0].label.value, label, case)
            finally:
                tmp.cleanup()

    def test_no_stable_window_is_not_silent_empty_quality(self):
        """Too-short acquire run → quality_evaluated with deterministic fallback."""
        proc = create_p2c_processor()
        # < minimum_window_sample_count (8 @ 250 Hz) → no StableWindow.
        tmp, _, session, samples = record_scenario(
            "normal_high_quality", duration_s=0.02, random_seed=1001
        )
        try:
            out = proc.process(session, samples)
            self.assertEqual(out.processing_status, "quality_evaluated")
            self.assertEqual(len(out.quality_results), 1)
            q = out.quality_results[0]
            self.assertEqual(q.window_id, "window-none-0001")
            self.assertEqual(q.label, QualityLabel.INSUFFICIENT_DURATION)
            self.assertEqual(list(q.reason_codes), ["too_short"])
            self.assertEqual(q.valid_duration_s, 0.0)
            self.assertEqual(dict(q.metrics), {})
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
