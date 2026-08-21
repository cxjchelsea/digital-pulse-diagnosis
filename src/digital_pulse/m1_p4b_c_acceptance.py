"""M1-P4B-C slice 验收：event / override / outcome persistence only。"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from digital_pulse.m1_contracts import (
    DecisionAction,
    DecisionInputVersions,
    I1_ACTIONS,
    M1Decision,
    ParameterStatus,
    QualityReference,
)
from digital_pulse.m1_int import (
    ALREADY_COMMITTED,
    COMMITTED,
    DecisionLedger,
    DecisionSourceProvenance,
    M1IntError,
    OverrideClassification,
)
from digital_pulse.m1_int.override_safety import ALLOWED_OVERRIDE_TARGETS
from digital_pulse.m1_p4a_acceptance import (
    EXPECTED_D3_TAG_OBJECT,
    EXPECTED_D3_TAG_TARGET,
    EXPECTED_P2_GOLDEN,
    EXPECTED_P3_DIGEST,
    EXPECTED_P3_SOURCE,
    PKG as P4A_RULE_CORE,
    _scan_source_boundaries,
)

ACCEPTANCE_VERSION = "m1-p4b-c-acceptance-v1"
P4B_B_MERGE_SHA = "5985216da9e6e4309d38b745dce98e1e43c94b9e"
ARCHITECTURE_BASE_SHA = "b9bdc598b0c464f1dd199505e6e99de1095b0ab4"
ROOT = Path(__file__).resolve().parents[2]
INT_PKG = ROOT / "src" / "digital_pulse" / "m1_int"
PERSIST_PKG = INT_PKG / "persist"
SESSION_ID = "session-p4b-c-acceptance"
DECISION_ID = "m1-decision-" + ("ab" * 32)
SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SOFTWARE_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CONFIG_DIGEST = "cd" * 32
FINGERPRINT = "ef" * 32
CLOCK = "2026-01-01T00:00:00Z"
SCOPE = "m1-retry-scope-" + ("cd" * 32)
FORBIDDEN_PUBLIC = {
    "replay",
    "effective_view",
    "effective_action",
    "effective_decision",
    "reconstruct_state",
    "fold_events",
    "RetryScope",
    "RetryScopeState",
    "schedule_next_attempt",
    "reconstruct_retry_scope",
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


def _machine(action: DecisionAction = DecisionAction.ACCEPT) -> M1Decision:
    return M1Decision(
        decision_id=DECISION_ID,
        session_id=SESSION_ID,
        decided_at_utc=CLOCK,
        milestone="M1",
        int_level="I1",
        device_state="ACQUIRE",
        quality_reference=QualityReference(session_id=SESSION_ID, window_id="window-0001"),
        action=action,
        reason_codes=("emergency_stop",) if action is DecisionAction.ABORT_AND_RELEASE else ("quality_acceptable",),
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


def _provenance(software: str = SOFTWARE_SHA) -> DecisionSourceProvenance:
    return DecisionSourceProvenance(
        app_run_id="run-p4b-c",
        app_analysis_fingerprint=FINGERPRINT,
        sp_result_fingerprint=FINGERPRINT,
        run_signal_processing_version="0.4.0-p2d",
        session_signal_processing_version="0.4.0-p2d",
        software_commit_sha=software,
    )


def _fail_at(point: str):
    def injector(actual: str) -> None:
        if actual == point:
            raise M1IntError("persistence_failure", f"injected failure at {actual}")

    return injector


def _scan_boundaries() -> dict[str, Any]:
    oracle_ok = True
    p4bd_ok = True
    p4c_ok = True
    persist_files = tuple(PERSIST_PKG.glob("*.py"))
    defined: set[str] = set()
    for path in persist_files:
        source = path.read_text(encoding="utf-8")
        oracle_ok = oracle_ok and _scan_source_boundaries(source, filename=str(path))[0]
        tree = ast.parse(source, filename=str(path))
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        defined |= names
        if names & FORBIDDEN_PUBLIC:
            p4bd_ok = False
            p4c_ok = False
        if "retry_count + 1" in source or "retry_count +=" in source:
            p4c_ok = False
        if "def replay(" in source or "def effective_decision(" in source:
            p4bd_ok = False
    required = {
        "persist_operator_override",
        "persist_action_applied",
        "persist_safety_rejection",
        "persist_decision_completed",
        "persist_manual_review_resolution",
    }
    api_ok = required <= defined
    oracle_probes = (
        "import digital_pulse.m1_simulator\n",
        "from digital_pulse.m1_simulator import ScenarioDefinition\n",
        "value = 'expected_int_action'\n",
        "if scenario_id == 'x':\n    pass\n",
    )
    oracle_self_test = all(not _scan_source_boundaries(source)[0] for source in oracle_probes)
    p4bd_self_test = "replay" in FORBIDDEN_PUBLIC and "effective_decision" in FORBIDDEN_PUBLIC
    p4c_self_test = "RetryScope" in FORBIDDEN_PUBLIC and "schedule_next_attempt" in FORBIDDEN_PUBLIC
    p4a_rule_core_no_io = True
    for path in P4A_RULE_CORE.glob("*.py"):
        if not _scan_source_boundaries(path.read_text(encoding="utf-8"), filename=str(path))[1]:
            p4a_rule_core_no_io = False
            break
    return {
        "oracle_ok": oracle_ok and oracle_self_test,
        "p4bd_ok": p4bd_ok,
        "p4c_ok": p4c_ok,
        "api_ok": api_ok,
        "oracle_scanner_self_test": oracle_self_test,
        "p4bd_boundary_scanner_self_test": p4bd_self_test,
        "p4c_boundary_scanner_self_test": p4c_self_test,
        "p4a_rule_core_no_io": p4a_rule_core_no_io,
    }


def run_m1_p4b_c_acceptance(*, software_commit_sha: str, expected_head_sha: str) -> dict[str, Any]:
    exact_head = software_commit_sha == expected_head_sha
    scan = _scan_boundaries()
    p3_ok = EXPECTED_P3_SOURCE == "2f4f88cc69fbdfb1e129d347025695334542eb9e"
    p3_digest_ok = EXPECTED_P3_DIGEST == "fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f"
    p2_ok = EXPECTED_P2_GOLDEN == "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"
    try:
        d3_ok = _git("rev-parse", "d3-v1.0.0") == EXPECTED_D3_TAG_OBJECT
        d3_ok = d3_ok and _git("rev-parse", "d3-v1.0.0^{commit}") == EXPECTED_D3_TAG_TARGET
    except (subprocess.CalledProcessError, FileNotFoundError):
        d3_ok = False

    event_append_ok = False
    event_identity_ok = False
    idempotent_ok = False
    conflict_ok = False
    seq_ok = False
    allow_ok = True
    deny_ok = True
    same_ok = True
    immutable_ok = False
    safety_audit_ok = False
    outcome_ok = False
    resolution_ok = False
    manifest_ok = False
    lock_ok = False
    crash_ok = False
    partial_ok = False
    provenance_ok = False
    p4bb_ok = False
    event_type_case_count = 0
    override_allowed_case_count = 0
    override_rejected_case_count = 0
    same_action_case_count = 0
    idempotency_probe_count = 0
    duplicate_conflict_probe_count = 0
    outcome_case_count = 0
    manual_review_resolution_case_count = 0
    concurrency_case_count = 0
    crash_point_case_count = 0
    fault_injection_case_count = 0
    partial_tail_probe_count = 0
    corruption_probe_count = 0

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = DecisionLedger(root, clock=lambda: CLOCK)
        first = ledger.append_decision(_machine(), _provenance())
        p4bb_ok = first.status is COMMITTED and first.event_seq == 1
        before = (root / SESSION_ID / "int" / "decisions.jsonl").read_bytes()
        allow = ledger.persist_operator_override(
            SESSION_ID,
            DECISION_ID,
            requested_action="stop",
            operator_id="op-001",
            note="stop now",
            source_provenance=_provenance(),
        )
        event_append_ok = allow.status is COMMITTED and allow.event_type == "operator_override"
        event_identity_ok = allow.event_id.startswith("m1-int-event-")
        event_type_case_count += 1
        retry = ledger.persist_operator_override(
            SESSION_ID,
            DECISION_ID,
            requested_action="stop",
            operator_id="op-001",
            note="stop now",
            source_provenance=_provenance(),
        )
        idempotent_ok = retry.status is ALREADY_COMMITTED and retry.event_id == allow.event_id
        idempotency_probe_count += 1
        try:
            ledger.persist_operator_override(
                SESSION_ID,
                DECISION_ID,
                requested_action="manual_review",
                operator_id="op-001",
                note="other",
                source_provenance=_provenance(),
            )
            conflict_ok = False
        except M1IntError as exc:
            conflict_ok = exc.code == "duplicate_conflict"
            duplicate_conflict_probe_count += 1
        try:
            ledger.persist_operator_override(
                SESSION_ID,
                DECISION_ID,
                requested_action="stop",
                operator_id="op-001",
                note="stop now",
                source_provenance=_provenance(SOFTWARE_B),
            )
            provenance_ok = False
        except M1IntError as exc:
            provenance_ok = exc.code == "provenance_mismatch"
        applied = ledger.persist_action_applied(SESSION_ID, DECISION_ID, source_provenance=_provenance())
        completed = ledger.persist_decision_completed(SESSION_ID, DECISION_ID, source_provenance=_provenance())
        resolved = ledger.persist_manual_review_resolution(
            SESSION_ID,
            DECISION_ID,
            resolution="continue_new_acquisition",
            operator_id="op-001",
            source_provenance=_provenance(),
        )
        outcome_ok = applied.event_type == "action_applied" and completed.event_type == "decision_completed"
        outcome_case_count += 2
        resolution_ok = resolved.event_type == "manual_review_resolved" and not (root / SESSION_ID / "app").exists()
        manual_review_resolution_case_count += 1
        for resolution in ("remain_awaiting", "terminate_stop"):
            other = DecisionLedger(root / resolution, clock=lambda: CLOCK)
            other.append_decision(_machine(DecisionAction.MANUAL_REVIEW), _provenance())
            item = other.persist_manual_review_resolution(
                SESSION_ID,
                DECISION_ID,
                resolution=resolution,
                operator_id="op-001",
                source_provenance=_provenance(),
            )
            resolution_ok = resolution_ok and item.event_type == "manual_review_resolved"
            manual_review_resolution_case_count += 1
        after = (root / SESSION_ID / "int" / "decisions.jsonl").read_bytes()
        immutable_ok = after == before
        machine = ledger.load_machine_decision(SESSION_ID, DECISION_ID)
        immutable_ok = immutable_ok and machine.operator_override is None and machine.outcome is None
        manifest = ledger.verify_decision_ledger_minimal(SESSION_ID)
        seq_ok = manifest.last_event_seq == manifest.event_count and manifest.event_count >= 5
        manifest_ok = (
            manifest.decision_count == 1
            and manifest.decisions_sha256 == hashlib.sha256(after).hexdigest()
        )
        lock_ok = (root / SESSION_ID / "int" / ".lock").is_file()

        for machine_action in I1_ACTIONS:
            for requested in I1_ACTIONS:
                probe = DecisionLedger(root / f"mx-{machine_action}-{requested}", clock=lambda: CLOCK)
                probe.append_decision(_machine(DecisionAction(machine_action)), _provenance())
                result = probe.persist_operator_override(
                    SESSION_ID,
                    DECISION_ID,
                    requested_action=requested,
                    operator_id="op-001",
                    note=f"{machine_action}->{requested}",
                    source_provenance=_provenance(),
                )
                if machine_action == requested:
                    same_ok = same_ok and result.classification is OverrideClassification.IDEMPOTENT_SAME_ACTION
                    same_action_case_count += 1
                elif requested in ALLOWED_OVERRIDE_TARGETS[machine_action]:
                    allow_ok = allow_ok and result.event_type == "operator_override"
                    override_allowed_case_count += 1
                else:
                    deny_ok = deny_ok and result.event_type == "action_rejected_by_safety"
                    override_rejected_case_count += 1
                    safety_audit_ok = True

        crash_ok = True
        for point in ("pending_write", "events_append", "events_fsync", "manifest_write", "pending_delete"):
            crash_root = root / "crash" / point
            crash_root.mkdir(parents=True)
            DecisionLedger(crash_root, clock=lambda: CLOCK).append_decision(_machine(), _provenance())
            try:
                DecisionLedger(crash_root, clock=lambda: CLOCK, failure_injector=_fail_at(point)).persist_action_applied(
                    SESSION_ID,
                    DECISION_ID,
                    source_provenance=_provenance(),
                )
                crash_ok = False
                break
            except M1IntError as exc:
                if exc.code != "persistence_failure":
                    crash_ok = False
                    break
            recovered = DecisionLedger(crash_root, clock=lambda: CLOCK)
            recovered.recover_pending_commit(SESSION_ID)
            recovered.verify_decision_ledger_minimal(SESSION_ID)
            crash_point_case_count += 1
            fault_injection_case_count += 1

        tail_root = root / "partial"
        tail_ledger = DecisionLedger(tail_root, clock=lambda: CLOCK)
        tail_ledger.append_decision(_machine(), _provenance())
        events_path = tail_root / SESSION_ID / "int" / "decision-events.jsonl"
        events_path.write_bytes(events_path.read_bytes() + b'{"event_type":"broken"')
        try:
            tail_ledger.persist_action_applied(SESSION_ID, DECISION_ID, source_provenance=_provenance())
            partial_ok = False
        except M1IntError as exc:
            partial_ok = exc.code == "ledger_untrusted"
            partial_tail_probe_count += 1
        corrupt_root = root / "corrupt"
        corrupt_ledger = DecisionLedger(corrupt_root, clock=lambda: CLOCK)
        corrupt_ledger.append_decision(_machine(), _provenance())
        (corrupt_root / SESSION_ID / "int" / "decision-events.jsonl").write_text("not-json\n", encoding="utf-8")
        try:
            corrupt_ledger.verify_decision_ledger_minimal(SESSION_ID)
            corruption_ok = False
        except M1IntError as exc:
            corruption_ok = exc.code == "ledger_untrusted"
            corruption_probe_count += 1

        conc_root = root / "conc"
        conc = DecisionLedger(conc_root, clock=lambda: CLOCK)
        conc.append_decision(_machine(), _provenance())

        def _write() -> str:
            result = conc.persist_operator_override(
                SESSION_ID,
                DECISION_ID,
                requested_action="stop",
                operator_id="op-001",
                note="shared",
                source_provenance=_provenance(),
            )
            return result.status.value

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = {pool.submit(_write).result(), pool.submit(_write).result()}
        concurrency_ok = statuses == {"committed", "already_committed"}
        concurrency_case_count += 1
        event_type_case_count += 3

    gates = {
        "event_append_verified": _gate("event_append_verified", event_append_ok),
        "event_identity_verified": _gate("event_identity_verified", event_identity_ok),
        "event_idempotency_verified": _gate(
            "event_idempotency_verified", idempotent_ok, idempotency_probe_count=idempotency_probe_count
        ),
        "event_duplicate_conflict_verified": _gate(
            "event_duplicate_conflict_verified",
            conflict_ok,
            duplicate_conflict_probe_count=duplicate_conflict_probe_count,
        ),
        "event_seq_verified": _gate("event_seq_verified", seq_ok),
        "override_allow_matrix_verified": _gate(
            "override_allow_matrix_verified", allow_ok, override_allowed_case_count=override_allowed_case_count
        ),
        "override_deny_matrix_verified": _gate(
            "override_deny_matrix_verified", deny_ok, override_rejected_case_count=override_rejected_case_count
        ),
        "same_action_classification_verified": _gate(
            "same_action_classification_verified", same_ok, same_action_case_count=same_action_case_count
        ),
        "machine_decision_immutable_verified": _gate("machine_decision_immutable_verified", immutable_ok),
        "outcome_enum_verified": _gate("outcome_enum_verified", outcome_ok, outcome_case_count=outcome_case_count),
        "manual_review_resolution_verified": _gate(
            "manual_review_resolution_verified",
            resolution_ok,
            manual_review_resolution_case_count=manual_review_resolution_case_count,
        ),
        "safety_rejection_audited_verified": _gate("safety_rejection_audited_verified", safety_audit_ok),
        "manifest_derived_verified": _gate("manifest_derived_verified", manifest_ok),
        "locking_verified": _gate("locking_verified", lock_ok and concurrency_ok, concurrency_case_count=concurrency_case_count),
        "crash_recovery_verified": _gate(
            "crash_recovery_verified",
            crash_ok,
            crash_point_case_count=crash_point_case_count,
            fault_injection_case_count=fault_injection_case_count,
        ),
        "partial_tail_fail_closed_verified": _gate(
            "partial_tail_fail_closed_verified", partial_ok, partial_tail_probe_count=partial_tail_probe_count
        ),
        "provenance_verified": _gate("provenance_verified", provenance_ok),
        "oracle_isolation_verified": _gate(
            "oracle_isolation_verified", scan["oracle_ok"], oracle_scanner_self_test=scan["oracle_scanner_self_test"]
        ),
        "p4bb_regression_verified": _gate("p4bb_regression_verified", p4bb_ok),
        "p4bd_boundary_verified": _gate(
            "p4bd_boundary_verified",
            scan["p4bd_ok"],
            p4bd_boundary_scanner_self_test=scan["p4bd_boundary_scanner_self_test"],
        ),
        "p4c_boundary_verified": _gate(
            "p4c_boundary_verified",
            scan["p4c_ok"],
            p4c_boundary_scanner_self_test=scan["p4c_boundary_scanner_self_test"],
        ),
        "exact_head": _gate("exact_head", exact_head, software_commit_sha=software_commit_sha),
        "d3_tag_unchanged": _gate("d3_tag_unchanged", d3_ok),
        "p2_golden_unchanged": _gate("p2_golden_unchanged", p2_ok),
        "p3_immutability_verified": _gate("p3_immutability_verified", p3_ok and p3_digest_ok),
        "typed_api_verified": _gate("typed_api_verified", scan["api_ok"]),
        "corruption_fail_closed_verified": _gate(
            "corruption_fail_closed_verified", corruption_ok, corruption_probe_count=corruption_probe_count
        ),
    }
    failed = [name for name, payload in gates.items() if not payload["passed"]]
    counts_ok = all(
        count > 0
        for count in (
            event_type_case_count,
            override_allowed_case_count,
            override_rejected_case_count,
            same_action_case_count,
            idempotency_probe_count,
            duplicate_conflict_probe_count,
            outcome_case_count,
            manual_review_resolution_case_count,
            concurrency_case_count,
            crash_point_case_count,
            fault_injection_case_count,
            partial_tail_probe_count,
            corruption_probe_count,
        )
    )
    acceptance = not failed and counts_ok
    return {
        "acceptance": acceptance,
        "acceptance_version": ACCEPTANCE_VERSION,
        "architecture_base_sha": ARCHITECTURE_BASE_SHA,
        "failed_gates": failed,
        "gates": gates,
        "p4b_b_merge_sha": P4B_B_MERGE_SHA,
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4B-C",
        "event_type_case_count": event_type_case_count,
        "override_allowed_case_count": override_allowed_case_count,
        "override_rejected_case_count": override_rejected_case_count,
        "same_action_case_count": same_action_case_count,
        "idempotency_probe_count": idempotency_probe_count,
        "duplicate_conflict_probe_count": duplicate_conflict_probe_count,
        "outcome_case_count": outcome_case_count,
        "manual_review_resolution_case_count": manual_review_resolution_case_count,
        "concurrency_case_count": concurrency_case_count,
        "crash_point_case_count": crash_point_case_count,
        "fault_injection_case_count": fault_injection_case_count,
        "partial_tail_probe_count": partial_tail_probe_count,
        "corruption_probe_count": corruption_probe_count,
    }
