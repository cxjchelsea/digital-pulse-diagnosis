"""M1-P3A APP persistence foundation public surface."""

from .checksums import RegisteredChecksum
from .errors import M1AppError
from .loader import AppSessionLoader, LoadedAppSession
from .models import (
    APP_MANIFEST_SCHEMA_VERSION,
    APP_PROCESSING_VERSION_P3A,
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

__all__ = [
    "APP_MANIFEST_SCHEMA_VERSION",
    "APP_PROCESSING_VERSION_P3A",
    "AppAssetRef",
    "AppAssetRole",
    "AppAssetWrite",
    "AppExecutionMode",
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
    "SafeSessionPath",
]
