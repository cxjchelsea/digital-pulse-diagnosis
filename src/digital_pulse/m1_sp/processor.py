"""SP preprocessing (P2A), quality (P2B), and beat/reference stage (P2C)."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

import numpy as np

from digital_pulse.m1_contracts import M1Sample, M1Session, ParameterStatus, QualityLabel

from .beats import analyze_beats
from .filters import MODE_CAUSAL, MODE_OFFLINE, FilterBank
from .integrity import IntegrityAnalyzer
from .metrics import RawQualityMetrics
from .models import (
    BeatReferenceBundle,
    ProcessingEvidence,
    QualityEvaluation,
    QualityMetricsInternal,
    SPPreprocessResult,
    SPProcessingProvenance,
    SPProcessingResult,
    SPQualityStageResult,
    StableWindow,
)
from .normalization import InputNormalizer
from .parameters import (
    SP_PARAMETER_VERSION_P2C,
    SPParameterSet,
    default_p2a_parameter_set,
    default_p2b_parameter_set,
    default_p2c_parameter_set,
)
from .projection import M1QualityProjector
from .quality import (
    PROCESSING_STATUS_BLOCKED,
    PROCESSING_STATUS_EVALUATED,
    QualityEvaluator,
    empty_metrics_for_integrity,
    is_safety_blocked,
    session_has_integrity_failure,
    sort_reason_codes,
)
from .reference import analyze_reference
from .windows import StableWindowSelector

SP_PROCESSING_VERSION_P2D = "0.4.0-p2d"


class SPPreprocessor:
    """P2A preprocessing — no M1QualityResult / filters / beats."""

    def __init__(
        self,
        *,
        parameters: SPParameterSet | None = None,
        normalizer: InputNormalizer | None = None,
        integrity_analyzer: IntegrityAnalyzer | None = None,
        window_selector: StableWindowSelector | None = None,
    ):
        self._parameters = parameters or default_p2a_parameter_set()
        self._parameters.validate()
        self._normalizer = normalizer or InputNormalizer()
        self._integrity = integrity_analyzer or IntegrityAnalyzer()
        self._windows = window_selector or StableWindowSelector()

    @property
    def parameters(self) -> SPParameterSet:
        return self._parameters

    @property
    def engineering_unit_conversion(self):
        return self._normalizer.engineering_unit_conversion

    def preprocess(self, session: M1Session, samples: Iterable[M1Sample]) -> SPPreprocessResult:
        normalized = self._normalizer.normalize(session, samples)
        integrity = self._integrity.analyze(session, normalized)
        windows = self._windows.select(normalized, integrity, self._parameters)
        return SPPreprocessResult(
            normalized=normalized,
            integrity=integrity,
            windows=windows,
            processing_version=self._parameters.processing_version,
            parameter_version=self._parameters.parameter_version,
            parameter_digest=self._parameters.configuration_digest,
        )


class SPQualityProcessor:
    """P2B/P2C quality stage.

    P2B path: preprocess → raw metrics → evaluate → project
    P2C path: + per-window filter/beat/reference before supplemental evaluation
    """

    def __init__(
        self,
        *,
        parameters: SPParameterSet | None = None,
        preprocessor: SPPreprocessor | None = None,
        metrics: RawQualityMetrics | None = None,
        evaluator: QualityEvaluator | None = None,
        projector: M1QualityProjector | None = None,
        filter_bank: FilterBank | None = None,
    ):
        self._parameters = parameters or default_p2b_parameter_set()
        self._parameters.validate()
        self._preprocessor = preprocessor or SPPreprocessor(parameters=self._parameters)
        self._metrics = metrics or RawQualityMetrics()
        self._evaluator = evaluator or QualityEvaluator()
        self._projector = projector or M1QualityProjector()
        self._filter_bank = filter_bank
        if self._is_p2c() and self._filter_bank is None:
            self._filter_bank = FilterBank(self._parameters)

    @property
    def parameters(self) -> SPParameterSet:
        return self._parameters

    @property
    def engineering_unit_conversion(self):
        return self._preprocessor.engineering_unit_conversion

    def _is_p2c(self) -> bool:
        return self._parameters.parameter_version == SP_PARAMETER_VERSION_P2C

    def process(self, session: M1Session, samples: Iterable[M1Sample]) -> SPQualityStageResult:
        preprocessing = self._preprocessor.preprocess(session, samples)
        integrity = preprocessing.integrity
        normalized = preprocessing.normalized

        if is_safety_blocked(integrity):
            return SPQualityStageResult(
                preprocessing=preprocessing,
                processing_status=PROCESSING_STATUS_BLOCKED,
                quality_results=(),
                metrics_by_window={},
                evaluations_by_window={},
                blocking_codes=tuple(integrity.blocking_codes),
                processing_version=self._parameters.processing_version,
                parameter_version=self._parameters.parameter_version,
                parameter_status=ParameterStatus.SYNTHETIC_ONLY,
                configuration_digest=self._parameters.configuration_digest,
            )

        if session_has_integrity_failure(integrity):
            metrics = empty_metrics_for_integrity(normalized.sample_count)
            evaluation = self._evaluator.evaluate_integrity(
                session=session,
                normalized=normalized,
                integrity=integrity,
                metrics=metrics,
            )
            assert evaluation is not None
            projected = self._projector.project_integrity(
                session=session,
                normalized=normalized,
                evaluation=evaluation,
                profile=self._parameters,
            )
            return SPQualityStageResult(
                preprocessing=preprocessing,
                processing_status=PROCESSING_STATUS_EVALUATED,
                quality_results=(projected,),
                metrics_by_window={"integrity-0001": metrics},
                evaluations_by_window={"integrity-0001": evaluation},
                blocking_codes=tuple(integrity.blocking_codes),
                processing_version=self._parameters.processing_version,
                parameter_version=self._parameters.parameter_version,
                parameter_status=ParameterStatus.SYNTHETIC_ONLY,
                configuration_digest=self._parameters.configuration_digest,
            )

        windows = preprocessing.windows.windows
        if not windows:
            return self._no_stable_window_result(session, preprocessing)

        quality_results = []
        metrics_by_window = {}
        evaluations_by_window = {}
        filter_views_by_window = {}
        beats_by_window = {}
        reference_by_window = {}

        for window in windows:
            metrics = self._metrics.compute(normalized, window, self._parameters)
            beat_ref = None
            if self._is_p2c():
                assert self._filter_bank is not None
                pack = self._run_p2c_window(normalized, window)
                filter_views_by_window[window.window_id] = pack["filters"]
                beats_by_window[window.window_id] = pack["beats"]
                reference_by_window[window.window_id] = pack["reference"]
                metrics = replace(
                    metrics,
                    beat_count=pack["beats"].beat_count,
                    ppg_match_rate=pack["reference"].match_rate,
                )
                beat_ref = BeatReferenceBundle(
                    beat_count=pack["beats"].beat_count,
                    interval_cv=pack["beats"].interval_cv,
                    ppg_match_rate=pack["reference"].match_rate,
                    reference_available=pack["reference"].reference_available,
                    lag_mad_ms=pack["reference"].lag_mad_ms,
                    median_lag_ms=pack["reference"].median_lag_ms,
                    ppg_valid_fraction=pack["ppg_valid_fraction"],
                )

            evaluation = self._evaluator.evaluate_window(
                session=session,
                normalized=normalized,
                integrity=integrity,
                window=window,
                metrics=metrics,
                profile=self._parameters,
                beat_ref=beat_ref,
            )
            # Keep supplemental metrics on evaluation result.
            evaluation = QualityEvaluation(
                primary_label=evaluation.primary_label,
                reason_codes=evaluation.reason_codes,
                internal_evidence=evaluation.internal_evidence,
                metrics=metrics,
            )
            projected = self._projector.project(
                session=session,
                window=window,
                evaluation=evaluation,
                profile=self._parameters,
            )
            quality_results.append(projected)
            metrics_by_window[window.window_id] = metrics
            evaluations_by_window[window.window_id] = evaluation

        return SPQualityStageResult(
            preprocessing=preprocessing,
            processing_status=PROCESSING_STATUS_EVALUATED,
            quality_results=tuple(quality_results),
            metrics_by_window=metrics_by_window,
            evaluations_by_window=evaluations_by_window,
            blocking_codes=tuple(integrity.blocking_codes),
            processing_version=self._parameters.processing_version,
            parameter_version=self._parameters.parameter_version,
            parameter_status=ParameterStatus.SYNTHETIC_ONLY,
            configuration_digest=self._parameters.configuration_digest,
            filter_views_by_window=filter_views_by_window,
            beats_by_window=beats_by_window,
            reference_by_window=reference_by_window,
        )

    def _run_p2c_window(self, normalized, window: StableWindow) -> dict:
        assert self._filter_bank is not None
        sl = slice(window.start_index, window.end_index)
        pulse_causal = self._filter_bank.filter_window_channel(
            normalized.pulse, window.start_index, window.end_index, mode=MODE_CAUSAL
        )
        pulse_offline = self._filter_bank.filter_window_channel(
            normalized.pulse, window.start_index, window.end_index, mode=MODE_OFFLINE
        )
        ppg_offline = self._filter_bank.filter_window_channel(
            normalized.ppg, window.start_index, window.end_index, mode=MODE_OFFLINE
        )
        beats = analyze_beats(
            filtered=pulse_offline,
            raw_values=np.asarray(normalized.pulse.values[sl], dtype=np.float64),
            device_time_us=np.asarray(normalized.device_time_us[sl], dtype=np.int64),
            sample_rate_hz=float(normalized.sample_rate_hz),
            window=window,
            parameters=self._parameters,
        )
        ppg_valid = np.asarray(normalized.ppg.valid_mask[sl], dtype=bool)
        ppg_valid_fraction = float(np.mean(ppg_valid)) if ppg_valid.size else 0.0
        reference = analyze_reference(
            pulse_beats=beats.candidates,
            ppg_filtered=ppg_offline,
            ppg_raw=np.asarray(normalized.ppg.values[sl], dtype=np.float64),
            ppg_valid_mask=ppg_valid,
            device_time_us=np.asarray(normalized.device_time_us[sl], dtype=np.int64),
            sample_rate_hz=float(normalized.sample_rate_hz),
            parameters=self._parameters,
            window_offset=window.start_index,
        )
        return {
            "filters": {"causal": pulse_causal, "offline_review": pulse_offline, "ppg_offline": ppg_offline},
            "beats": beats,
            "reference": reference,
            "ppg_valid_fraction": ppg_valid_fraction,
        }

    def _no_stable_window_result(
        self, session: M1Session, preprocessing: SPPreprocessResult
    ) -> SPQualityStageResult:
        """Frozen semantics: never silent quality_evaluated + []."""
        window = StableWindow(
            window_id="window-none-0001",
            start_index=0,
            end_index=0,
            start_device_time_us=0,
            end_device_time_us=0,
            sample_count=0,
            duration_s=0.0,
        )
        metrics = QualityMetricsInternal(
            valid_fraction=None,
            clipping_fraction=None,
            baseline_drift_raw=None,
            pulse_std_raw=None,
            lower_clipping_fraction=None,
            upper_clipping_fraction=None,
            load_median_raw=None,
            load_std_raw=None,
            load_range_raw=None,
            load_slope_raw_per_s=None,
            motion_metric=None,
            near_constant_metric=None,
            valid_sample_count=0,
            total_sample_count=0,
            evidence=(
                ProcessingEvidence(
                    code="NO_STABLE_WINDOW",
                    severity="error",
                    details={"policy": "insufficient_duration/too_short"},
                ),
            ),
            beat_count=None,
            ppg_match_rate=None,
        )
        evaluation = QualityEvaluation(
            primary_label=QualityLabel.INSUFFICIENT_DURATION,
            reason_codes=sort_reason_codes(("too_short",)),
            internal_evidence=metrics.evidence,
            metrics=metrics,
        )
        projected = self._projector.project(
            session=session,
            window=window,
            evaluation=evaluation,
            profile=self._parameters,
            valid_duration_s=0.0,
        )
        return SPQualityStageResult(
            preprocessing=preprocessing,
            processing_status=PROCESSING_STATUS_EVALUATED,
            quality_results=(projected,),
            metrics_by_window={window.window_id: metrics},
            evaluations_by_window={window.window_id: evaluation},
            blocking_codes=tuple(preprocessing.integrity.blocking_codes),
            processing_version=self._parameters.processing_version,
            parameter_version=self._parameters.parameter_version,
            parameter_status=ParameterStatus.SYNTHETIC_ONLY,
            configuration_digest=self._parameters.configuration_digest,
        )


def create_p2c_processor() -> SPQualityProcessor:
    return SPQualityProcessor(parameters=default_p2c_parameter_set())


class SPProcessor:
    """Formal P2D facade over the frozen P2C processing profile."""

    def __init__(self, *, quality_processor: SPQualityProcessor | None = None):
        self._quality = quality_processor or create_p2c_processor()

    @property
    def parameters(self) -> SPParameterSet:
        return self._quality.parameters

    @property
    def processing_version(self) -> str:
        return SP_PROCESSING_VERSION_P2D

    @property
    def engineering_unit_conversion(self):
        return self._quality.engineering_unit_conversion

    def process(
        self,
        session: M1Session,
        samples: Iterable[M1Sample],
        *,
        provenance: SPProcessingProvenance,
    ) -> SPProcessingResult:
        stage = self._quality.process(session, samples)
        preprocessing = stage.preprocessing
        limitations = tuple(
            item.value if hasattr(item, "value") else str(item) for item in session.limitations
        )
        result = SPProcessingResult(
            session_id=session.session_id,
            source_type=preprocessing.normalized.source_type,
            processing_status=stage.processing_status,
            quality_results=stage.quality_results,
            blocking_codes=stage.blocking_codes,
            limitations=limitations,
            processing_version=SP_PROCESSING_VERSION_P2D,
            parameter_version=stage.parameter_version,
            parameter_status=stage.parameter_status,
            configuration_digest=stage.configuration_digest,
            software_commit_sha=provenance.software_commit_sha,
            engineering_unit_conversion=self._quality.engineering_unit_conversion,
            stage_result=stage,
            result_sha256="0" * 64,
        )
        from .summary import sp_result_sha256

        return replace(result, result_sha256=sp_result_sha256(result))


def create_p2d_processor() -> SPProcessor:
    return SPProcessor(quality_processor=create_p2c_processor())
