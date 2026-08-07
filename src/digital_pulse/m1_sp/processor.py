"""SP preprocessing (P2A) and quality stage (P2B) entrypoints."""

from __future__ import annotations

from typing import Iterable

from digital_pulse.m1_contracts import M1Sample, M1Session, ParameterStatus

from .integrity import IntegrityAnalyzer
from .metrics import RawQualityMetrics
from .models import SPPreprocessResult, SPQualityStageResult
from .normalization import InputNormalizer
from .parameters import SPParameterSet, default_p2a_parameter_set, default_p2b_parameter_set
from .projection import M1QualityProjector
from .quality import (
    PROCESSING_STATUS_BLOCKED,
    PROCESSING_STATUS_EVALUATED,
    QualityEvaluator,
    empty_metrics_for_integrity,
    is_safety_blocked,
    session_has_integrity_failure,
)
from .windows import StableWindowSelector


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
    """P2B quality stage: preprocess → raw metrics → evaluate → project."""

    def __init__(
        self,
        *,
        parameters: SPParameterSet | None = None,
        preprocessor: SPPreprocessor | None = None,
        metrics: RawQualityMetrics | None = None,
        evaluator: QualityEvaluator | None = None,
        projector: M1QualityProjector | None = None,
    ):
        self._parameters = parameters or default_p2b_parameter_set()
        self._parameters.validate()
        self._preprocessor = preprocessor or SPPreprocessor(parameters=self._parameters)
        self._metrics = metrics or RawQualityMetrics()
        self._evaluator = evaluator or QualityEvaluator()
        self._projector = projector or M1QualityProjector()

    @property
    def parameters(self) -> SPParameterSet:
        return self._parameters

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

        quality_results = []
        metrics_by_window = {}
        evaluations_by_window = {}
        for window in preprocessing.windows.windows:
            metrics = self._metrics.compute(normalized, window, self._parameters)
            evaluation = self._evaluator.evaluate_window(
                session=session,
                normalized=normalized,
                integrity=integrity,
                window=window,
                metrics=metrics,
                profile=self._parameters,
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
        )
