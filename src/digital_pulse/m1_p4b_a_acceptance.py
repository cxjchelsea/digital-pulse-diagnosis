"""M1-P4B-A slice 验收：纯合同层，不宣称整个 P4B 完成。"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import subprocess
from typing import Any

from digital_pulse.m1_int import (
    FROZEN_EVENT_TYPES,
    LEDGER_MANIFEST_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    M1IntError,
    OverrideClassification,
    build_int_ledger_event,
    classify_override,
    event_fingerprint,
    is_override_allowed,
    require_frozen_outcome,
    require_frozen_resolution,
    require_machine_decision_record,
    validate_int_ledger_event,
)
from digital_pulse.m1_int.ledger_models import EMPTY_LEDGER_DIGEST, IntLedgerManifest, validate_int_ledger_manifest
from digital_pulse.m1_p4a_acceptance import (
    EXPECTED_D3_TAG_OBJECT,
    EXPECTED_D3_TAG_TARGET,
    EXPECTED_P2_GOLDEN,
    EXPECTED_P3_DIGEST,
    EXPECTED_P3_SOURCE,
    _scan_source_boundaries,
)

ACCEPTANCE_VERSION = "m1-p4b-a-acceptance-v1"
P4A_MERGE_SHA = "de82869b8bc8dd0580c8067192a73f3151ce89fe"
ARCHITECTURE_BASE_SHA = "b9bdc598b0c464f1dd199505e6e99de1095b0ab4"
ROOT = Path(__file__).resolve().parents[2]
INT_PKG = ROOT / "src" / "digital_pulse" / "m1_int"
P4B_A_FILES = (INT_PKG / "ledger_models.py", INT_PKG / "override_safety.py")
SESSION_ID = "session-p4b-a-acceptance"
DECISION_ID = "m1-decision-" + ("ab" * 32)
SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CONFIG_DIGEST = "cd" * 32
I1_ACTIONS = (
    "accept",
    "retry_same_position",
    "reposition",
    "manual_review",
    "stop",
    "abort_and_release",
)
ALLOWED_OVERRIDE_PAIRS = {
    ("accept", "manual_review"),
    ("accept", "stop"),
    ("retry_same_position", "stop"),
    ("retry_same_position", "manual_review"),
    ("reposition", "stop"),
    ("reposition", "manual_review"),
    ("manual_review", "stop"),
    ("stop", "abort_and_release"),
}


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            completed.stdout,
            completed.stderr,
        )
    return completed.stdout.strip()


def _gate(name: str, passed: bool, **detail: Any) -> dict[str, Any]:
    payload = {"name": name, "passed": passed}
    payload.update(detail)
    return payload


def _d3_tag_unchanged() -> bool:
    try:
        d3_object = _git("rev-parse", "d3-v1.0.0")
        d3_target = _git("rev-parse", "d3-v1.0.0^{commit}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return d3_object == EXPECTED_D3_TAG_OBJECT and d3_target == EXPECTED_D3_TAG_TARGET


def _scan_p4b_a_boundaries() -> tuple[bool, bool, bool]:
    """扫描 P4B-A 生产文件：无 IO、无 RetryScope 编排符号。"""

    persistence_ok = True
    scope_ok = True
    forbidden_names = {
        "RetryScope",
        "RetryScopeState",
        "acknowledge_reposition",
        "consume_retry_budget",
        "schedule_next_attempt",
        "reconstruct_retry_scope",
    }
    for path in P4B_A_FILES:
        source = path.read_text(encoding="utf-8")
        _oracle_ok, source_persistence_ok = _scan_source_boundaries(source, filename=str(path))
        del _oracle_ok
        persistence_ok = persistence_ok and source_persistence_ok
        tree = ast.parse(source, filename=str(path))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if defined & forbidden_names:
            scope_ok = False
        if "retry_count + 1" in source or "retry_count +=" in source:
            scope_ok = False
    persistence_probe = "from pathlib import Path\nPath('x').write_text('x')\n"
    persistence_scanner_self_test = not _scan_source_boundaries(persistence_probe)[1]
    return persistence_ok, scope_ok, persistence_scanner_self_test


def _probe_event_type_contracts() -> tuple[bool, int]:
    """逐类型攻击：缺字段、禁止字段、错 outcome、非法 seq。"""

    cases = 0
    ok = True
    scope = "m1-retry-scope-" + ("aa" * 32)

    def expect_invalid(**kwargs: Any) -> None:
        nonlocal cases, ok
        cases += 1
        try:
            build_int_ledger_event(session_id=SESSION_ID, occurred_at_utc="2026-01-01T00:00:00Z", **kwargs)
            ok = False
        except M1IntError as exc:
            ok = ok and exc.code == "invalid_input"

    expect_invalid(event_seq=1, event_type="override_requested", decision_id=DECISION_ID)
    expect_invalid(event_seq=1, event_type="OPERATOR_OVERRIDE", decision_id=DECISION_ID)
    expect_invalid(event_seq=0, event_type="action_applied", decision_id=DECISION_ID)
    expect_invalid(event_seq=-1, event_type="action_applied", decision_id=DECISION_ID)
    expect_invalid(
        event_seq=1,
        event_type="action_applied",
        decision_id=DECISION_ID,
        operator_id="op",
    )
    expect_invalid(
        event_seq=1,
        event_type="decision_recorded",
        decision_id=DECISION_ID,
        software_commit_sha=SOFTWARE_SHA,
        rule_version="i1-pre-0.1.0",
        configuration_digest=CONFIG_DIGEST,
        retry_scope_id=scope,
    )
    expect_invalid(
        event_seq=1,
        event_type="retry_scope_started",
        retry_scope_id=scope,
        decision_id=DECISION_ID,
    )
    expect_invalid(
        event_seq=1,
        event_type="operator_override",
        decision_id=DECISION_ID,
        requested_action="stop",
        operator_id="op-001",
        note="",
    )
    expect_invalid(
        event_seq=1,
        event_type="decision_completed",
        decision_id=DECISION_ID,
        outcome="applied",
    )
    expect_invalid(
        event_seq=1,
        event_type="manual_review_resolved",
        decision_id=DECISION_ID,
        operator_id="op-001",
        resolution="accept_current_quality",
    )
    return ok, cases


def _probe_identity_collisions() -> tuple[bool, int]:
    """语义字段变化必须改 event_id；墙钟/软件 SHA 不得改。"""

    probes = 0
    first = build_int_ledger_event(
        event_seq=1,
        event_type="decision_recorded",
        session_id=SESSION_ID,
        decision_id=DECISION_ID,
        occurred_at_utc="2026-01-01T00:00:00Z",
        software_commit_sha=SOFTWARE_SHA,
        rule_version="i1-pre-0.1.0",
        configuration_digest=CONFIG_DIGEST,
    )
    second = build_int_ledger_event(
        event_seq=1,
        event_type="decision_recorded",
        session_id=SESSION_ID,
        decision_id=DECISION_ID,
        occurred_at_utc="2026-08-19T15:00:00Z",
        software_commit_sha="b" * 40,
        rule_version="i1-pre-0.1.0",
        configuration_digest=CONFIG_DIGEST,
    )
    probes += 1
    ok = (
        first.event_id == second.event_id
        and first.event_id == f"m1-int-event-{event_fingerprint(first)}"
    )
    base = build_int_ledger_event(
        event_seq=2,
        event_type="operator_override",
        session_id=SESSION_ID,
        decision_id=DECISION_ID,
        occurred_at_utc="2026-01-01T00:00:00Z",
        requested_action="stop",
        operator_id="op-001",
        note="n1",
    )
    variants = [
        build_int_ledger_event(
            event_seq=2,
            event_type="operator_override",
            session_id=SESSION_ID,
            decision_id=DECISION_ID,
            occurred_at_utc="2026-01-01T00:00:00Z",
            requested_action="manual_review",
            operator_id="op-001",
            note="n1",
        ),
        build_int_ledger_event(
            event_seq=2,
            event_type="operator_override",
            session_id=SESSION_ID,
            decision_id=DECISION_ID,
            occurred_at_utc="2026-01-01T00:00:00Z",
            requested_action="stop",
            operator_id="op-002",
            note="n1",
        ),
        build_int_ledger_event(
            event_seq=2,
            event_type="operator_override",
            session_id=SESSION_ID,
            decision_id=DECISION_ID,
            occurred_at_utc="2026-01-01T00:00:00Z",
            requested_action="stop",
            operator_id="op-001",
            note="n2",
        ),
    ]
    probes += 3
    ok = ok and len({base.event_id, *[item.event_id for item in variants]}) == 4
    forged = replace(first, event_id="m1-int-event-" + ("0" * 64))
    probes += 1
    try:
        validate_int_ledger_event(forged)
        ok = False
    except M1IntError as exc:
        ok = ok and exc.code == "invalid_input"
    return ok, probes


def _probe_override_matrix() -> tuple[bool, int]:
    cases = 0
    ok = True
    for machine_action in I1_ACTIONS:
        for requested_action in I1_ACTIONS:
            cases += 1
            classification = classify_override(machine_action, requested_action)
            if machine_action == requested_action:
                ok = ok and classification is OverrideClassification.IDEMPOTENT_SAME_ACTION
                ok = ok and not is_override_allowed(machine_action, requested_action)
            elif (machine_action, requested_action) in ALLOWED_OVERRIDE_PAIRS:
                ok = ok and classification is OverrideClassification.ALLOWED
                ok = ok and is_override_allowed(machine_action, requested_action)
            else:
                ok = ok and classification is OverrideClassification.REJECTED_BY_SAFETY
                ok = ok and not is_override_allowed(machine_action, requested_action)
    return ok, cases


def _probe_manifest_invalid() -> tuple[bool, int]:
    base = IntLedgerManifest(
        schema_version=LEDGER_MANIFEST_SCHEMA_VERSION,
        session_id=SESSION_ID,
        decision_rule_version="i1-pre-0.1.0",
        configuration_digest=CONFIG_DIGEST,
        software_commit_sha=SOFTWARE_SHA,
        decisions_sha256=EMPTY_LEDGER_DIGEST,
        events_sha256=EMPTY_LEDGER_DIGEST,
        decision_count=0,
        event_count=0,
        last_event_seq=0,
        current_decision_id=None,
    )
    attacks = [
        replace(base, decision_count=-1),
        replace(base, last_event_seq=1),
        replace(
            base,
            event_count=2,
            last_event_seq=0,
            decisions_sha256="11" * 32,
            events_sha256="22" * 32,
        ),
        replace(
            base,
            event_count=2,
            last_event_seq=3,
            decisions_sha256="11" * 32,
            events_sha256="22" * 32,
        ),
        replace(base, current_decision_id=DECISION_ID),
        replace(base, decision_count=1, current_decision_id=None, decisions_sha256="11" * 32),
        replace(base, configuration_digest="CD" * 32),
        replace(base, session_id="  "),
        replace(base, schema_version="1.0.0"),
    ]
    ok = True
    for manifest in attacks:
        try:
            validate_int_ledger_manifest(manifest)
            ok = False
        except M1IntError:
            pass
    validate_int_ledger_manifest(base)
    return ok, len(attacks)


def run_m1_p4b_a_acceptance(*, software_commit_sha: str, expected_head_sha: str) -> dict[str, Any]:
    """运行 P4B-A slice 门；失败关闭，不写 ledger 文件。"""

    exact_head = software_commit_sha == expected_head_sha
    schema_ok = (
        LEDGER_SCHEMA_VERSION == "i1-ledger-1.0.0-pre"
        and LEDGER_MANIFEST_SCHEMA_VERSION == "i1-ledger-manifest-1.0.0-pre"
        and LEDGER_SCHEMA_VERSION != "1.0.0"
    )
    event_set_ok = FROZEN_EVENT_TYPES == {
        "decision_recorded",
        "operator_override",
        "action_applied",
        "action_rejected_by_safety",
        "decision_completed",
        "awaiting_operator",
        "reposition_acknowledged",
        "manual_review_resolved",
        "retry_scope_started",
        "retry_scope_closed",
        "retry_attempt_linked",
    }
    event_contract_ok, event_type_contract_case_count = _probe_event_type_contracts()
    identity_ok, identity_collision_probe_count = _probe_identity_collisions()
    override_ok, override_matrix_case_count = _probe_override_matrix()
    manifest_ok, manifest_invalid_case_count = _probe_manifest_invalid()

    resolution_ok = True
    for resolution in ("remain_awaiting", "terminate_stop", "continue_new_acquisition"):
        require_frozen_resolution(resolution)
    try:
        require_frozen_resolution("accept_current_quality")
        resolution_ok = False
    except M1IntError as exc:
        resolution_ok = exc.code == "invalid_input"
    outcome_ok = True
    for outcome in (None, "applied", "superseded", "rejected_by_safety", "awaiting_operator", "completed"):
        require_frozen_outcome(outcome)
    try:
        require_frozen_outcome("retried")
        outcome_ok = False
    except M1IntError as exc:
        outcome_ok = exc.code == "invalid_input"

    from digital_pulse.m1_contracts import (
        DecisionAction,
        DecisionInputVersions,
        M1Decision,
        OperatorOverride,
        ParameterStatus,
        QualityReference,
    )

    machine = M1Decision(
        decision_id=DECISION_ID,
        session_id=SESSION_ID,
        decided_at_utc="2026-01-01T00:00:00Z",
        milestone="M1",
        int_level="I1",
        device_state="ACQUIRE",
        quality_reference=QualityReference(session_id=SESSION_ID, window_id="window-0001"),
        action=DecisionAction.ACCEPT,
        reason_codes=("quality_acceptable",),
        rule_version="i1-pre-0.1.0",
        input_versions=DecisionInputVersions(
            signal_processing_version="0.4.0-p2d",
            decision_rule_version="i1-pre-0.1.0",
            configuration_digest=CONFIG_DIGEST,
        ),
        retry_count=0,
        max_retry_count=2,
        operator_override=None,
        outcome=None,
        parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
    )
    immutability_ok = True
    require_machine_decision_record(machine)
    try:
        require_machine_decision_record(
            replace(
                machine,
                operator_override=OperatorOverride(operator_id="op-001", note="patched"),
            )
        )
        immutability_ok = False
    except M1IntError as exc:
        immutability_ok = exc.code == "invalid_input"
    try:
        require_machine_decision_record(replace(machine, outcome="applied"))
        immutability_ok = False
    except M1IntError as exc:
        immutability_ok = immutability_ok and exc.code == "invalid_input"

    persistence_ok, scope_ok, persistence_scanner_self_test = _scan_p4b_a_boundaries()
    p3_ok = EXPECTED_P3_SOURCE == "2f4f88cc69fbdfb1e129d347025695334542eb9e"
    p3_digest_ok = EXPECTED_P3_DIGEST == "fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f"
    p2_ok = EXPECTED_P2_GOLDEN == "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"

    gates = {
        "exact_head": _gate("exact_head", exact_head, software_commit_sha=software_commit_sha),
        "ledger_schema_pre": _gate("ledger_schema_pre", schema_ok, schema=LEDGER_SCHEMA_VERSION),
        "frozen_event_types": _gate(
            "frozen_event_types",
            event_set_ok and event_contract_ok,
            event_type_contract_case_count=event_type_contract_case_count,
        ),
        "identity_collision_safe": _gate(
            "identity_collision_safe",
            identity_ok,
            identity_collision_probe_count=identity_collision_probe_count,
        ),
        "override_matrix_closed": _gate(
            "override_matrix_closed",
            override_ok,
            override_matrix_case_count=override_matrix_case_count,
        ),
        "resolution_and_outcome": _gate("resolution_and_outcome", resolution_ok and outcome_ok),
        "machine_decision_immutable": _gate("machine_decision_immutable", immutability_ok),
        "manifest_invariants": _gate(
            "manifest_invariants",
            manifest_ok,
            manifest_invalid_case_count=manifest_invalid_case_count,
        ),
        "no_persistence": _gate(
            "no_persistence",
            persistence_ok and persistence_scanner_self_test,
            scanner_self_test=persistence_scanner_self_test,
        ),
        "no_retryscope_orchestration": _gate("no_retryscope_orchestration", scope_ok),
        "p2_golden_unchanged": _gate("p2_golden_unchanged", p2_ok),
        "p3_golden_unchanged": _gate("p3_golden_unchanged", p3_ok and p3_digest_ok),
        "d3_tag_unchanged": _gate("d3_tag_unchanged", _d3_tag_unchanged()),
        "slice_not_full_p4b": _gate("slice_not_full_p4b", ACCEPTANCE_VERSION == "m1-p4b-a-acceptance-v1"),
    }
    failed = [name for name, item in gates.items() if not item["passed"]]
    return {
        "acceptance": failed == [],
        "acceptance_version": ACCEPTANCE_VERSION,
        "architecture_base_sha": ARCHITECTURE_BASE_SHA,
        "event_type_contract_case_count": event_type_contract_case_count,
        "failed_gates": failed,
        "gates": gates,
        "identity_collision_probe_count": identity_collision_probe_count,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "manifest_invalid_case_count": manifest_invalid_case_count,
        "override_matrix_case_count": override_matrix_case_count,
        "p4a_merge_sha": P4A_MERGE_SHA,
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4B-A",
    }
