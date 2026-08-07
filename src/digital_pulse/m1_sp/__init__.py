"""M1-P2 SP-S1-pre package (P2A preprocess + P2B quality stage).

P2B projects formal M1QualityResult. Does not implement filters, beats,
PPG alignment, INT decisions, or APP persistence.
"""

from .errors import SPError
from .integrity import IntegrityAnalyzer
from .metrics import RawQualityMetrics
from .models import (
    IntegrityAnalysis,
    IntegrityConsistency,
    NormalizedChannelSeries,
    NormalizedSession,
    ProcessingEvidence,
    QualityEvaluation,
    QualityMetricsInternal,
    SPPreprocessResult,
    SPQualityStageResult,
    StableWindow,
    StableWindowResult,
)
from .normalization import InputNormalizer, RawIdentityConverter
from .observations import SequenceObservations, TimestampObservations, observe_sequence, observe_timestamps
from .parameters import (
    METRIC_FORMULA_VERSIONS,
    P2B_CHARACTERIZATION_SEEDS,
    SP_PARAMETER_VERSION,
    SP_PARAMETER_VERSION_P2A,
    SP_PARAMETER_VERSION_P2B,
    SP_PROCESSING_VERSION,
    SP_PROCESSING_VERSION_P2A,
    SP_PROCESSING_VERSION_P2B,
    SPParameter,
    SPParameterClass,
    SPParameterSet,
    default_p2a_parameter_set,
    default_p2b_parameter_set,
)
from .processor import SPPreprocessor, SPQualityProcessor
from .projection import M1QualityProjector
from .quality import (
    PROCESSING_STATUS_BLOCKED,
    PROCESSING_STATUS_EVALUATED,
    QualityEvaluator,
    sort_reason_codes,
)
from .windows import StableWindowSelector

__all__ = [
    "SPError",
    "METRIC_FORMULA_VERSIONS",
    "P2B_CHARACTERIZATION_SEEDS",
    "SP_PARAMETER_VERSION",
    "SP_PARAMETER_VERSION_P2A",
    "SP_PARAMETER_VERSION_P2B",
    "SP_PROCESSING_VERSION",
    "SP_PROCESSING_VERSION_P2A",
    "SP_PROCESSING_VERSION_P2B",
    "InputNormalizer",
    "IntegrityAnalysis",
    "IntegrityAnalyzer",
    "IntegrityConsistency",
    "M1QualityProjector",
    "NormalizedChannelSeries",
    "NormalizedSession",
    "PROCESSING_STATUS_BLOCKED",
    "PROCESSING_STATUS_EVALUATED",
    "ProcessingEvidence",
    "QualityEvaluation",
    "QualityEvaluator",
    "QualityMetricsInternal",
    "RawIdentityConverter",
    "RawQualityMetrics",
    "SequenceObservations",
    "TimestampObservations",
    "observe_sequence",
    "observe_timestamps",
    "SPParameter",
    "SPParameterClass",
    "SPParameterSet",
    "SPPreprocessResult",
    "SPPreprocessor",
    "SPQualityProcessor",
    "SPQualityStageResult",
    "StableWindow",
    "StableWindowResult",
    "StableWindowSelector",
    "default_p2a_parameter_set",
    "default_p2b_parameter_set",
    "sort_reason_codes",
]
