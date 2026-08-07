"""M1-P2 SP-S1-pre preprocessing package (P2A: normalize / integrity / windows).

Does not produce M1QualityResult, filters, beats, or decisions.
"""

from .errors import SPError
from .integrity import IntegrityAnalyzer
from .models import (
    IntegrityAnalysis,
    IntegrityConsistency,
    NormalizedChannelSeries,
    NormalizedSession,
    ProcessingEvidence,
    SPPreprocessResult,
    StableWindow,
    StableWindowResult,
)
from .normalization import InputNormalizer, RawIdentityConverter
from .observations import SequenceObservations, TimestampObservations, observe_sequence, observe_timestamps
from .parameters import (
    SP_PARAMETER_VERSION,
    SP_PROCESSING_VERSION,
    SPParameter,
    SPParameterClass,
    SPParameterSet,
    default_p2a_parameter_set,
)
from .processor import SPPreprocessor
from .windows import StableWindowSelector

__all__ = [
    "SPError",
    "SP_PARAMETER_VERSION",
    "SP_PROCESSING_VERSION",
    "InputNormalizer",
    "IntegrityAnalysis",
    "IntegrityAnalyzer",
    "IntegrityConsistency",
    "NormalizedChannelSeries",
    "NormalizedSession",
    "ProcessingEvidence",
    "RawIdentityConverter",
    "SequenceObservations",
    "TimestampObservations",
    "observe_sequence",
    "observe_timestamps",
    "SPParameter",
    "SPParameterClass",
    "SPParameterSet",
    "SPPreprocessResult",
    "SPPreprocessor",
    "StableWindow",
    "StableWindowResult",
    "StableWindowSelector",
    "default_p2a_parameter_set",
]
