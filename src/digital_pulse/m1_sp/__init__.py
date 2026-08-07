"""M1-P2 SP-S1-pre preprocessing package (P2A: normalize / integrity / windows)."""

from .errors import SPError
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
from .parameters import (
    SP_PARAMETER_VERSION,
    SP_PROCESSING_VERSION,
    SPParameter,
    SPParameterClass,
    SPParameterSet,
    default_p2a_parameter_set,
)

__all__ = [
    "SPError",
    "SP_PARAMETER_VERSION",
    "SP_PROCESSING_VERSION",
    "IntegrityAnalysis",
    "IntegrityConsistency",
    "NormalizedChannelSeries",
    "NormalizedSession",
    "ProcessingEvidence",
    "SPParameter",
    "SPParameterClass",
    "SPParameterSet",
    "SPPreprocessResult",
    "StableWindow",
    "StableWindowResult",
    "default_p2a_parameter_set",
]
