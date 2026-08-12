"""M1 APP persistence, replay, and projection public surface."""

from .analysis import (
    APP_ANALYSIS_FINGERPRINT_VERSION,
    APP_ANALYSIS_SCHEMA_VERSION,
    AppAnalysis,
    AnalysisProjector,
    compare_app_analysis,
    create_replay_app_provenance,
)
from .checksums import RegisteredChecksum
from .errors import M1AppError
from .gating import APP_GATE_VERSION_P3B, AnalysisQualityGate, AppGateDecision
from .loader import AppSessionLoader, LoadedAppSession
from .models import (
    APP_MANIFEST_SCHEMA_VERSION,
    APP_PROCESSING_VERSION_P3A,
    APP_PROCESSING_VERSION_P3B,
    AppAssetRef,
    AppAssetRole,
    AppExecutionMode,
    AppManifest,
    AppPersistenceState,
    AppProvenance,
    AppRunManifest,
    AppSessionRef,
    ChecksumProvenance,
    ChecksumSource,
    RawIntegrityAssurance,
)
from .paths import SafeSessionPath
from .persistence import AppAssetWrite, AppPersistence
from .replay import ReplayAnalysisResult, ReplayAnalysisService, ReplaySessionSource
from .sp_serialization import SP_RESULT_SCHEMA_VERSION, sp_result_assets, sp_result_document

__all__ = [
    "APP_ANALYSIS_FINGERPRINT_VERSION",
    "APP_ANALYSIS_SCHEMA_VERSION",
    "APP_GATE_VERSION_P3B",
    "APP_MANIFEST_SCHEMA_VERSION",
    "APP_PROCESSING_VERSION_P3A",
    "APP_PROCESSING_VERSION_P3B",
    "AnalysisProjector",
    "AnalysisQualityGate",
    "AppAnalysis",
    "AppAssetRef",
    "AppAssetRole",
    "AppAssetWrite",
    "AppExecutionMode",
    "AppGateDecision",
    "AppManifest",
    "AppPersistence",
    "AppPersistenceState",
    "AppProvenance",
    "AppRunManifest",
    "AppSessionLoader",
    "AppSessionRef",
    "ChecksumProvenance",
    "ChecksumSource",
    "LoadedAppSession",
    "M1AppError",
    "RawIntegrityAssurance",
    "RegisteredChecksum",
    "ReplayAnalysisResult",
    "ReplayAnalysisService",
    "ReplaySessionSource",
    "SP_RESULT_SCHEMA_VERSION",
    "SafeSessionPath",
    "compare_app_analysis",
    "create_replay_app_provenance",
    "sp_result_assets",
    "sp_result_document",
]
