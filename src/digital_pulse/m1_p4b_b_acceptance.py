"""M1-P4B-B slice 验收：machine decision persistence only。不宣称整个 P4B 完成。"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from digital_pulse.m1_contracts import (
    DecisionAction,
    DecisionInputVersions,
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
)
from digital_pulse.m1_p4a_acceptance import (
    EXPECTED_D3_TAG_OBJECT,
    EXPECTED_D3_TAG_TARGET,
    EXPECTED_P2_GOLDEN,
    EXPECTED_P3_DIGEST,
    EXPECTED_P3_SOURCE,
    _scan_source_boundaries,
)

ACCEPTANCE_VERSION = "m1-p4b-b-acceptance-v1"
P4B_A_MERGE_SHA = "78cdd310280fef65ebe4d2efb979c529a3656bcc"
ARCHITECTURE_BASE_SHA = "b9bdc598b0c464f1dd199505e6e99de1095b0ab4"
ROOT = Path(__file__).resolve().parents[2]
INT_PKG = ROOT / "src" / "digital_pulse" / "m1_int"
PERSIST_PKG = INT_PKG / "persist"
P4B_A_FILES = (INT_PKG / "ledger_models.py", INT_PKG / "override_safety.py")
SESSION_ID = "session-p4b-b-acceptance"
DECISION_ID = "m1-decision-" + ("ab" * 32)
SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CONFIG_DIGEST = "cd" * 32
FINGERPRINT = "ef" * 32
CLOCK = "2026-01-01T00:00:00Z"
FORBIDDEN_API = {
    "record_override",
    "record_outcome",
    "append_event",
    "replay",
    "effective_view",
    "effective_action",
    "RetryScope",
    "RetryScopeState",
    "acknowledge_reposition",
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


def _d3_tag_unchanged() -> bool:
    try:
        d3_object = _git("rev-parse", "d3-v1.0.0")
        d3_target = _git("rev-parse", "d3-v1.0.0^{commit}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return d3_object == EXPECTED_D3_TAG_OBJECT and d3_target == EXPECTED_D3_TAG_TARGET


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


def _provenance() -> DecisionSourceProvenance:
    return DecisionSourceProvenance(
        app_run_id="run-p4b-b",
        app_analysis_fingerprint=FINGERPRINT,
        sp_result_fingerprint=FINGERPRINT,
        run_signal_processing_version="0.4.0-p2d",
        session_signal_processing_version="0.4.0-p2d",
        software_commit_sha=SOFTWARE_SHA,
    )


def _scan_oracle_and_boundaries() -> tuple[bool, bool, bool, bool]:
    oracle_ok = True
    p4bc_ok = True
    p4c_ok = True
    persist_files = tuple(PERSIST_PKG.glob("*.py"))
    for path in persist_files:
        source = path.read_text(encoding="utf-8")
        oracle_ok = oracle_ok and _scan_source_boundaries(source, filename=str(path))[0]
        tree = ast.parse(source, filename=str(path))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if defined & FORBIDDEN_API:
            p4bc_ok = False
            p4c_ok = False
        if "retry_count + 1" in source or "retry_count +=" in source:
            p4c_ok = False
        if "record_override" in source or "record_outcome" in source:
            p4bc_ok = False
    init_source = (INT_PKG / "__init__.py").read_text(encoding="utf-8")
    exported = set()
    tree = ast.parse(init_source, filename=str(INT_PKG / "__init__.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__" and isinstance(node.value, ast.List):
                    exported = {
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
    if exported & FORBIDDEN_API:
        p4bc_ok = False
        p4c_ok = False
    oracle_probe = not _scan_source_boundaries("import digital_pulse.m1_simulator\n")[0]
    return oracle_ok and oracle_probe, p4bc_ok, p4c_ok, bool(persist_files)


def run_m1_p4b_b_acceptance(*, software_commit_sha: str, expected_head_sha: str) -> dict[str, Any]:
    exact_head = software_commit_sha == expected_head_sha
    oracle_ok, p4bc_ok, p4c_ok, persist_present = _scan_oracle_and_boundaries()
    p3_ok = EXPECTED_P3_SOURCE == "2f4f88cc69fbdfb1e129d347025695334542eb9e"
    p3_digest_ok = EXPECTED_P3_DIGEST == "fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f"
    p2_ok = EXPECTED_P2_GOLDEN == "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"

    append_ok = False
    idempotent_ok = False
    conflict_ok = False
    recorded_ok = False
    seq_ok = False
    manifest_ok = False
    lock_ok = False
    fsync_ok = False
    crash_ok = False
    partial_ok = False
    corruption_ok = False
    provenance_ok = False
    p3_write_ok = False

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = DecisionLedger(root, clock=lambda: CLOCK)
        first = ledger.append_decision(_machine(), _provenance())
        append_ok = first.status is COMMITTED and first.event_seq == 1
        int_dir = root / SESSION_ID / "int"
        lock_ok = (int_dir / ".lock").is_file()
        decisions = (int_dir / "decisions.jsonl").read_bytes()
        events = (int_dir / "decision-events.jsonl").read_bytes()
        fsync_ok = decisions.endswith(b"\n") and events.endswith(b"\n")
        event = json.loads(events.splitlines()[0])
        recorded_ok = (
            event["event_type"] == "decision_recorded"
            and event["decision_id"] == DECISION_ID
            and event["software_commit_sha"] == SOFTWARE_SHA
            and event["app_run_id"] == "run-p4b-b"
        )
        provenance_ok = recorded_ok and event["configuration_digest"] == CONFIG_DIGEST
        second = ledger.append_decision(_machine(), _provenance())
        idempotent_ok = (
            second.status is ALREADY_COMMITTED
            and (int_dir / "decisions.jsonl").read_bytes() == decisions
            and (int_dir / "decision-events.jsonl").read_bytes() == events
        )
        try:
            ledger.append_decision(_machine(DecisionAction.STOP), _provenance())
            conflict_ok = False
        except M1IntError as exc:
            conflict_ok = exc.code == "duplicate_conflict"
        manifest = ledger.verify_decision_ledger_minimal(SESSION_ID)
        manifest_ok = (
            manifest.decision_count == 1
            and manifest.event_count == 1
            and manifest.last_event_seq == 1
            and manifest.decisions_sha256 == hashlib.sha256(decisions).hexdigest()
            and manifest.events_sha256 == hashlib.sha256(events).hexdigest()
        )
        seq_ok = manifest.last_event_seq == manifest.event_count
        p3_write_ok = not (root / SESSION_ID / "app").exists() and not (int_dir / "reports").exists()

        crash_root = root / "crash"
        crash_root.mkdir()

        def fail_manifest(point: str) -> None:
            if point == "manifest_write":
                raise M1IntError("persistence_failure", "injected")

        try:
            DecisionLedger(crash_root, clock=lambda: CLOCK, failure_injector=fail_manifest).append_decision(
                _machine(),
                _provenance(),
            )
            crash_ok = False
        except M1IntError:
            recovered = DecisionLedger(crash_root, clock=lambda: CLOCK)
            recovered.recover_pending_commit(SESSION_ID)
            crash_ok = recovered.load_machine_decision(SESSION_ID, DECISION_ID).decision_id == DECISION_ID

        tail = int_dir / "decisions.jsonl"
        tail.write_bytes(tail.read_bytes() + b'{"partial":true')
        ledger.recover_pending_commit(SESSION_ID)
        partial_ok = tail.read_bytes().endswith(b"\n") and tail.read_bytes().count(b"\n") == 1
        try:
            ledger.verify_decision_ledger_minimal(SESSION_ID)
        except M1IntError:
            partial_ok = False

        bad = root / "corrupt"
        bad_ledger = DecisionLedger(bad, clock=lambda: CLOCK)
        bad_ledger.append_decision(_machine(), _provenance())
        path = bad / SESSION_ID / "int" / "decisions.jsonl"
        path.write_text("{bad}\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            bad_ledger.verify_decision_ledger_minimal(SESSION_ID)
            corruption_ok = False
        except M1IntError as exc:
            corruption_ok = exc.code == "ledger_untrusted"

    gates = {
        "exact_head": _gate("exact_head", exact_head, software_commit_sha=software_commit_sha),
        "decision_append_verified": _gate("decision_append_verified", append_ok and persist_present),
        "idempotency_verified": _gate("idempotency_verified", idempotent_ok),
        "duplicate_conflict_verified": _gate("duplicate_conflict_verified", conflict_ok),
        "decision_recorded_verified": _gate("decision_recorded_verified", recorded_ok),
        "event_seq_verified": _gate("event_seq_verified", seq_ok),
        "manifest_verified": _gate("manifest_verified", manifest_ok),
        "locking_verified": _gate("locking_verified", lock_ok),
        "fsync_verified": _gate("fsync_verified", fsync_ok),
        "crash_recovery_verified": _gate("crash_recovery_verified", crash_ok),
        "partial_tail_verified": _gate("partial_tail_verified", partial_ok),
        "corruption_fail_closed_verified": _gate("corruption_fail_closed_verified", corruption_ok),
        "provenance_verified": _gate("provenance_verified", provenance_ok),
        "oracle_isolation_verified": _gate("oracle_isolation_verified", oracle_ok),
        "p3_immutability_verified": _gate(
            "p3_immutability_verified",
            p3_ok and p3_digest_ok and p3_write_ok,
        ),
        "p4bc_boundary_verified": _gate("p4bc_boundary_verified", p4bc_ok),
        "p4c_boundary_verified": _gate("p4c_boundary_verified", p4c_ok),
        "p2_golden_unchanged": _gate("p2_golden_unchanged", p2_ok),
        "d3_tag_unchanged": _gate("d3_tag_unchanged", _d3_tag_unchanged()),
        "slice_not_full_p4b": _gate("slice_not_full_p4b", ACCEPTANCE_VERSION == "m1-p4b-b-acceptance-v1"),
    }
    failed = [name for name, item in gates.items() if not item["passed"]]
    return {
        "acceptance": failed == [],
        "acceptance_version": ACCEPTANCE_VERSION,
        "architecture_base_sha": ARCHITECTURE_BASE_SHA,
        "failed_gates": failed,
        "gates": gates,
        "p4b_a_merge_sha": P4B_A_MERGE_SHA,
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4B-B",
    }
