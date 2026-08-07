"""P2A preprocessing entrypoint — no M1QualityResult / filters / beats."""

from __future__ import annotations

from typing import Iterable

from digital_pulse.m1_contracts import M1Sample, M1Session

from .integrity import IntegrityAnalyzer
from .models import SPPreprocessResult
from .normalization import InputNormalizer
from .parameters import SPParameterSet, default_p2a_parameter_set
from .windows import StableWindowSelector


class SPPreprocessor:
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
