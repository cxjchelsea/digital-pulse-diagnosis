"""M1-P4A 规则核心、P4B-A 合同、P4B-B/C persistence 与 P4B-D replay API。"""

from .errors import M1IntError
from .ledger_models import (
    EMPTY_LEDGER_DIGEST,
    FROZEN_EVENT_TYPES,
    FROZEN_OUTCOMES,
    FROZEN_RESOLUTIONS,
    LEDGER_MANIFEST_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    IntLedgerEvent,
    IntLedgerManifest,
    build_int_ledger_event,
    canonical_event_bytes,
    canonical_event_payload,
    event_fingerprint,
    require_frozen_outcome,
    require_frozen_resolution,
    require_machine_decision_record,
    validate_int_ledger_event,
    validate_int_ledger_manifest,
)
from .models import (
    RULE_VERSION,
    DecisionContext,
    DecisionEvaluation,
    DecisionSourceProvenance,
    HistoryFacts,
    IntegrityFacts,
    OperatorFacts,
    QualityFacts,
    SafetyFacts,
    SessionFacts,
    history_fingerprint,
)
from .override_safety import OverrideClassification, classify_override, is_override_allowed
from .persist import (
    ALREADY_COMMITTED,
    COMMITTED,
    AppendResult,
    AppendStatus,
    DecisionLedger,
    EventAppendResult,
)
from .policy import I1PolicyConfig, policy_configuration_digest
from .projection import project_m1_decision
from .replay import fold_ledger_snapshot
from .replay_models import LedgerReplayResult, ReconstructedDecisionView
from .rules import I1RuleEngine

__all__ = [
    "ALREADY_COMMITTED",
    "COMMITTED",
    "AppendResult",
    "AppendStatus",
    "DecisionContext",
    "DecisionLedger",
    "DecisionEvaluation",
    "DecisionSourceProvenance",
    "EventAppendResult",
    "EMPTY_LEDGER_DIGEST",
    "FROZEN_EVENT_TYPES",
    "FROZEN_OUTCOMES",
    "FROZEN_RESOLUTIONS",
    "HistoryFacts",
    "I1PolicyConfig",
    "I1RuleEngine",
    "IntLedgerEvent",
    "IntLedgerManifest",
    "IntegrityFacts",
    "LEDGER_MANIFEST_SCHEMA_VERSION",
    "LEDGER_SCHEMA_VERSION",
    "LedgerReplayResult",
    "M1IntError",
    "ReconstructedDecisionView",
    "OperatorFacts",
    "OverrideClassification",
    "QualityFacts",
    "RULE_VERSION",
    "SafetyFacts",
    "SessionFacts",
    "build_int_ledger_event",
    "canonical_event_bytes",
    "canonical_event_payload",
    "classify_override",
    "event_fingerprint",
    "fold_ledger_snapshot",
    "history_fingerprint",
    "is_override_allowed",
    "policy_configuration_digest",
    "project_m1_decision",
    "require_frozen_outcome",
    "require_frozen_resolution",
    "require_machine_decision_record",
    "validate_int_ledger_event",
    "validate_int_ledger_manifest",
]

# P4B-B forbids a public name `replay` on this package. The fold module file
# remains replay.py; only the package attribute is hidden.
if "replay" in globals():
    del replay

