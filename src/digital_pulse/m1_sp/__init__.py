"""M1-P2 SP-S1-pre package (P2A preprocess + P2B quality + P2C beat/reference).

Does not implement INT decisions, APP persistence, or formal M1-P2 acceptance.
"""

from .beats import BeatAnalysis, BeatCandidate, BeatDetector, BeatSegment, BeatSegmenter, analyze_beats
from .errors import SPError
from .filters import CausalFIRFilter, FilterBank, FilteredSeries, OfflineReviewFilter, design_lowpass_fir
from .integrity import IntegrityAnalyzer
from .metrics import RawQualityMetrics
from .models import (
    BeatReferenceBundle,
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
    P2A_CONFIGURATION_DIGEST,
    P2B_CHARACTERIZATION_SEEDS,
    P2B_CONFIGURATION_DIGEST,
    P2C_CHARACTERIZATION_SEEDS,
    P2C_CONFIGURATION_DIGEST,
    SP_PARAMETER_VERSION,
    SP_PARAMETER_VERSION_P2A,
    SP_PARAMETER_VERSION_P2B,
    SP_PARAMETER_VERSION_P2C,
    SP_PROCESSING_VERSION,
    SP_PROCESSING_VERSION_P2A,
    SP_PROCESSING_VERSION_P2B,
    SP_PROCESSING_VERSION_P2C,
    SPParameter,
    SPParameterClass,
    SPParameterSet,
    default_p2a_parameter_set,
    default_p2b_parameter_set,
    default_p2c_parameter_set,
)
from .processor import SPPreprocessor, SPQualityProcessor, create_p2c_processor
from .projection import M1QualityProjector
from .quality import (
    PROCESSING_STATUS_BLOCKED,
    PROCESSING_STATUS_EVALUATED,
    QualityEvaluator,
    sort_reason_codes,
)
from .reference import PPGDetector, ReferenceAligner, ReferenceMatchSummary, analyze_reference
from .windows import StableWindowSelector

__all__ = [
    "SPError",
    "METRIC_FORMULA_VERSIONS",
    "P2A_CONFIGURATION_DIGEST",
    "P2B_CHARACTERIZATION_SEEDS",
    "P2B_CONFIGURATION_DIGEST",
    "P2C_CHARACTERIZATION_SEEDS",
    "P2C_CONFIGURATION_DIGEST",
    "SP_PARAMETER_VERSION",
    "SP_PARAMETER_VERSION_P2A",
    "SP_PARAMETER_VERSION_P2B",
    "SP_PARAMETER_VERSION_P2C",
    "SP_PROCESSING_VERSION",
    "SP_PROCESSING_VERSION_P2A",
    "SP_PROCESSING_VERSION_P2B",
    "SP_PROCESSING_VERSION_P2C",
    "BeatAnalysis",
    "BeatCandidate",
    "BeatDetector",
    "BeatReferenceBundle",
    "BeatSegment",
    "BeatSegmenter",
    "CausalFIRFilter",
    "FilterBank",
    "FilteredSeries",
    "InputNormalizer",
    "IntegrityAnalysis",
    "IntegrityAnalyzer",
    "IntegrityConsistency",
    "M1QualityProjector",
    "NormalizedChannelSeries",
    "NormalizedSession",
    "OfflineReviewFilter",
    "PROCESSING_STATUS_BLOCKED",
    "PROCESSING_STATUS_EVALUATED",
    "PPGDetector",
    "ProcessingEvidence",
    "QualityEvaluation",
    "QualityEvaluator",
    "QualityMetricsInternal",
    "RawIdentityConverter",
    "RawQualityMetrics",
    "ReferenceAligner",
    "ReferenceMatchSummary",
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
    "analyze_beats",
    "analyze_reference",
    "create_p2c_processor",
    "default_p2a_parameter_set",
    "default_p2b_parameter_set",
    "default_p2c_parameter_set",
    "design_lowpass_fir",
    "sort_reason_codes",
]
