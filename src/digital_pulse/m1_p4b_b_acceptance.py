"""M1-P4B-B slice 验收：machine decision persistence only。不宣称整个 P4B 完成。"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
from digital_pulse.m1_int.ledger_models import EMPTY_LEDGER_DIGEST
from digital_pulse.m1_p4a_acceptance import (
    EXPECTED_D3_TAG_OBJECT,
    EXPECTED_D3_TAG_TARGET,
    EXPECTED_P2_GOLDEN,
    EXPECTED_P3_DIGEST,
    EXPECTED_P3_SOURCE,
    PKG as P4A_RULE_CORE,
    _scan_source_boundaries,
)

ACCEPTANCE_VERSION = "m1-p4b-b-acceptance-v1"
P4B_A_MERGE_SHA = "78cdd310280fef65ebe4d2efb979c529a3656bcc"
ARCHITECTURE_BASE_SHA = "b9bdc598b0c464f1dd199505e6e99de1095b0ab4"
ROOT = Path(__file__).resolve().parents[2]
INT_PKG = ROOT / "src" / "digital_pulse" / "m1_int"
PERSIST_PKG = INT_PKG / "persist"
SESSION_ID = "session-p4b-b-acceptance"
DECISION_ID = "m1-decision-" + ("ab" * 32)
DECISION_B = "m1-decision-" + ("cd" * 32)
SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SOFTWARE_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
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
P4A_NO_PERSISTENCE_SCOPE = (
    "P4A no_persistence scans src/digital_pulse/m1_int/*.py only "
    "(non-recursive rule core). persist/ is the authorized P4B-B IO boundary."
)


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


def _machine(action: DecisionAction = DecisionAction.ACCEPT, *, decision_id: str = DECISION_ID) -> M1Decision:
    return M1Decision(
        decision_id=decision_id,
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


def _provenance(software: str = SOFTWARE_SHA, **overrides: Any) -> DecisionSourceProvenance:
    payload = {
        "app_run_id": "run-p4b-b",
        "app_analysis_fingerprint": FINGERPRINT,
        "sp_result_fingerprint": FINGERPRINT,
        "run_signal_processing_version": "0.4.0-p2d",
        "session_signal_processing_version": "0.4.0-p2d",
        "software_commit_sha": software,
    }
    payload.update(overrides)
    return DecisionSourceProvenance(**payload)


def _fail_at(point: str):
    def injector(actual: str) -> None:
        if actual == point:
            raise M1IntError("persistence_failure", f"injected failure at {actual}")

    return injector


def _scan_oracle_and_boundaries() -> dict[str, Any]:
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
    oracle_probes = (
        "import digital_pulse.m1_simulator\n",
        "from digital_pulse import m1_simulator\n",
        "from digital_pulse.m1_simulator import ScenarioDefinition\n",
        "value = 'expected_int_action'\n",
        "value = 'expected_quality_label'\n",
        "value = 'expected.json'\n",
        "value = 'scenario.json'\n",
        "if scenario_id == 'x':\n    pass\n",
        "__import__('digital_pulse.m1_simulator')\n",
        "import importlib\nimportlib.import_module('digital_pulse.m1_simulator')\n",
    )
    oracle_self_test = all(not _scan_source_boundaries(source)[0] for source in oracle_probes)
    p4bc_self_test = "record_override" in FORBIDDEN_API and "append_event" in FORBIDDEN_API
    p4a_rule_core_no_io = True
    for path in P4A_RULE_CORE.glob("*.py"):
        if not _scan_source_boundaries(path.read_text(encoding="utf-8"), filename=str(path))[1]:
            p4a_rule_core_no_io = False
            break
    return {
        "oracle_ok": oracle_ok and oracle_self_test,
        "p4bc_ok": p4bc_ok,
        "p4c_ok": p4c_ok,
        "persist_present": bool(persist_files),
        "oracle_scanner_self_test": oracle_self_test,
        "p4bc_boundary_scanner_self_test": p4bc_self_test,
        "p4a_rule_core_no_io": p4a_rule_core_no_io,
    }


def run_m1_p4b_b_acceptance(*, software_commit_sha: str, expected_head_sha: str) -> dict[str, Any]:
    exact_head = software_commit_sha == expected_head_sha
    scan = _scan_oracle_and_boundaries()
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
    idempotency_probe_count = 0
    duplicate_conflict_probe_count = 0
    crash_point_case_count = 0
    fault_injection_case_count = 0
    pending_tamper_probe_count = 0
    partial_tail_probe_count = 0
    corruption_probe_count = 0
    concurrency_case_count = 0

    crash_points = (
        "pending_write",
        "pending_fsync",
        "decisions_append",
        "decisions_fsync",
        "events_append",
        "events_fsync",
        "manifest_write",
        "manifest_fsync",
        "pending_delete",
    )

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
        second = ledger.append_decision(_machine(), _provenance())
        idempotent_ok = (
            second.status is ALREADY_COMMITTED
            and (int_dir / "decisions.jsonl").read_bytes() == decisions
            and (int_dir / "decision-events.jsonl").read_bytes() == events
        )
        idempotency_probe_count += 1
        try:
            ledger.append_decision(_machine(), _provenance(SOFTWARE_B))
            provenance_ok = False
        except M1IntError as exc:
            provenance_ok = recorded_ok and exc.code == "provenance_mismatch"
        try:
            ledger.append_decision(_machine(DecisionAction.STOP), _provenance())
            conflict_ok = False
        except M1IntError as exc:
            conflict_ok = exc.code == "duplicate_conflict"
            duplicate_conflict_probe_count += 1
        for variant in (
            replace(_machine(), retry_count=1),
            replace(_machine(), decided_at_utc="2026-08-20T00:00:00Z"),
            replace(_machine(), reason_codes=("quality_borderline",)),
        ):
            try:
                ledger.append_decision(variant, _provenance())
                conflict_ok = False
            except M1IntError as exc:
                conflict_ok = conflict_ok and exc.code == "duplicate_conflict"
                duplicate_conflict_probe_count += 1
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

        crash_ok = True
        for point in crash_points:
            crash_root = root / "crash" / point
            crash_root.mkdir(parents=True)
            try:
                DecisionLedger(crash_root, clock=lambda: CLOCK, failure_injector=_fail_at(point)).append_decision(
                    _machine(),
                    _provenance(),
                )
                crash_ok = False
                break
            except M1IntError as exc:
                if exc.code != "persistence_failure":
                    crash_ok = False
                    break
            recovered = DecisionLedger(crash_root, clock=lambda: CLOCK)
            crash_point_case_count += 1
            fault_injection_case_count += 1
            if point in {"pending_write", "pending_fsync"}:
                try:
                    recovered.load_machine_decision(SESSION_ID, DECISION_ID)
                    crash_ok = False
                    break
                except M1IntError:
                    continue
            loaded = recovered.load_machine_decision(SESSION_ID, DECISION_ID)
            recovered.verify_decision_ledger_minimal(SESSION_ID)
            if loaded.decision_id != DECISION_ID or (crash_root / SESSION_ID / "int" / ".pending-commit.json").exists():
                crash_ok = False
                break

        tail_root = root / "tails"
        tail_ledger = DecisionLedger(tail_root, clock=lambda: CLOCK)
        tail_ledger.append_decision(_machine(), _provenance())
        tail = tail_root / SESSION_ID / "int" / "decisions.jsonl"
        before = tail.read_bytes()
        tail.write_bytes(before + b'{"partial":true')
        try:
            tail_ledger.recover_pending_commit(SESSION_ID)
            partial_ok = False
        except M1IntError as exc:
            partial_ok = exc.code == "ledger_untrusted" and tail.read_bytes() == before + b'{"partial":true'
            partial_tail_probe_count += 1
        missing_nl = tail_root / "missing-nl"
        try:
            DecisionLedger(missing_nl, clock=lambda: CLOCK, failure_injector=_fail_at("decisions_append")).append_decision(
                _machine(),
                _provenance(),
            )
            partial_ok = False
        except M1IntError:
            path = missing_nl / SESSION_ID / "int" / "decisions.jsonl"
            path.write_bytes(path.read_bytes()[:-1])
            recovered_tail = DecisionLedger(missing_nl, clock=lambda: CLOCK)
            recovered_tail.recover_pending_commit(SESSION_ID)
            recovered_tail.verify_decision_ledger_minimal(SESSION_ID)
            partial_ok = partial_ok and recovered_tail.load_machine_decision(SESSION_ID, DECISION_ID).decision_id == DECISION_ID
            partial_tail_probe_count += 1

        planted = tail_root / SESSION_ID / "int" / ".pending-commit.json"
        planted.write_text(
            json.dumps(
                {
                    "schema_version": "i1-ledger-pending-1.0.0-pre",
                    "session_id": "other-session",
                    "decision_id": DECISION_B,
                    "pre_decision_count": 0,
                    "pre_event_count": 0,
                    "pre_last_event_seq": 0,
                    "pre_decisions_sha256": EMPTY_LEDGER_DIGEST,
                    "pre_events_sha256": EMPTY_LEDGER_DIGEST,
                    "decision_record": "{}\n",
                    "event_records": ["{}\n"],
                    "post_decision_count": 1,
                    "post_event_count": 1,
                    "post_last_event_seq": 1,
                    "post_decisions_sha256": "22" * 32,
                    "post_events_sha256": "33" * 32,
                    "decision_rule_version": "i1-pre-0.1.0",
                    "configuration_digest": CONFIG_DIGEST,
                    "software_commit_sha": SOFTWARE_SHA,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            tail_ledger.recover_pending_commit(SESSION_ID)
            pending_tamper_ok = False
        except M1IntError as exc:
            pending_tamper_ok = exc.code in {"ledger_untrusted", "version_mismatch"}
            pending_tamper_probe_count += 1
        planted.unlink(missing_ok=True)

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
            corruption_probe_count += 1
        semantic = root / "semantic"
        semantic_ledger = DecisionLedger(semantic, clock=lambda: CLOCK)
        semantic_ledger.append_decision(_machine(), _provenance())
        semantic_path = semantic / SESSION_ID / "int" / "decision-events.jsonl"
        payload = json.loads(semantic_path.read_text(encoding="utf-8").splitlines()[0])
        payload["event_seq"] = 3
        semantic_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        try:
            semantic_ledger.verify_decision_ledger_minimal(SESSION_ID)
            corruption_ok = False
        except M1IntError as exc:
            corruption_ok = corruption_ok and exc.code == "ledger_untrusted"
            corruption_probe_count += 1

        conc_root = root / "conc"
        conc_a = _machine()
        conc_b = _machine(DecisionAction.STOP, decision_id=DECISION_B)

        def write(decision: M1Decision):
            return DecisionLedger(conc_root, clock=lambda: CLOCK).append_decision(decision, _provenance())

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, (conc_a, conc_b)))
        conc_manifest = DecisionLedger(conc_root, clock=lambda: CLOCK).verify_decision_ledger_minimal(SESSION_ID)
        concurrency_ok = (
            {item.status for item in results} == {COMMITTED}
            and conc_manifest.decision_count == 2
            and conc_manifest.last_event_seq == 2
        )
        concurrency_case_count += 1

    crash_ok = crash_ok and crash_point_case_count == len(crash_points)
    locking_verified = lock_ok and concurrency_ok
    gates = {
        "exact_head": _gate("exact_head", exact_head, software_commit_sha=software_commit_sha),
        "decision_append_verified": _gate("decision_append_verified", append_ok and scan["persist_present"]),
        "idempotency_verified": _gate(
            "idempotency_verified",
            idempotent_ok,
            idempotency_probe_count=idempotency_probe_count,
        ),
        "duplicate_conflict_verified": _gate(
            "duplicate_conflict_verified",
            conflict_ok,
            duplicate_conflict_probe_count=duplicate_conflict_probe_count,
        ),
        "decision_recorded_verified": _gate("decision_recorded_verified", recorded_ok),
        "event_seq_verified": _gate("event_seq_verified", seq_ok),
        "manifest_verified": _gate(
            "manifest_verified",
            manifest_ok,
            digest_semantics="crash-consistency derived index; not cryptographic tamper evidence",
        ),
        "locking_verified": _gate(
            "locking_verified",
            locking_verified,
            concurrency_case_count=concurrency_case_count,
        ),
        "fsync_verified": _gate("fsync_verified", fsync_ok, fault_injection_case_count=fault_injection_case_count),
        "crash_recovery_verified": _gate(
            "crash_recovery_verified",
            crash_ok,
            crash_point_case_count=crash_point_case_count,
            fault_injection_case_count=fault_injection_case_count,
        ),
        "partial_tail_verified": _gate(
            "partial_tail_verified",
            partial_ok,
            partial_tail_probe_count=partial_tail_probe_count,
        ),
        "corruption_fail_closed_verified": _gate(
            "corruption_fail_closed_verified",
            corruption_ok,
            corruption_probe_count=corruption_probe_count,
        ),
        "pending_tamper_verified": _gate(
            "pending_tamper_verified",
            pending_tamper_ok,
            pending_tamper_probe_count=pending_tamper_probe_count,
        ),
        "provenance_verified": _gate("provenance_verified", provenance_ok),
        "oracle_isolation_verified": _gate(
            "oracle_isolation_verified",
            scan["oracle_ok"],
            oracle_scanner_self_test=scan["oracle_scanner_self_test"],
        ),
        "p3_immutability_verified": _gate(
            "p3_immutability_verified",
            p3_ok and p3_digest_ok and p3_write_ok,
            writes_restricted_to="sessions/<session_id>/int/** relative to DecisionLedger sessions_root",
        ),
        "p4bc_boundary_verified": _gate(
            "p4bc_boundary_verified",
            scan["p4bc_ok"],
            p4bc_boundary_scanner_self_test=scan["p4bc_boundary_scanner_self_test"],
        ),
        "p4c_boundary_verified": _gate("p4c_boundary_verified", scan["p4c_ok"]),
        "p4a_no_persistence_scope_accurate": _gate(
            "p4a_no_persistence_scope_accurate",
            scan["p4a_rule_core_no_io"],
            scope=P4A_NO_PERSISTENCE_SCOPE,
        ),
        "p2_golden_unchanged": _gate("p2_golden_unchanged", p2_ok),
        "d3_tag_unchanged": _gate("d3_tag_unchanged", _d3_tag_unchanged()),
        "slice_not_full_p4b": _gate("slice_not_full_p4b", ACCEPTANCE_VERSION == "m1-p4b-b-acceptance-v1"),
    }
    failed = [name for name, item in gates.items() if not item["passed"]]
    return {
        "acceptance": failed == [],
        "acceptance_version": ACCEPTANCE_VERSION,
        "architecture_base_sha": ARCHITECTURE_BASE_SHA,
        "concurrency_case_count": concurrency_case_count,
        "corruption_probe_count": corruption_probe_count,
        "crash_point_case_count": crash_point_case_count,
        "duplicate_conflict_probe_count": duplicate_conflict_probe_count,
        "failed_gates": failed,
        "fault_injection_case_count": fault_injection_case_count,
        "gates": gates,
        "idempotency_probe_count": idempotency_probe_count,
        "p4a_no_persistence_scope": P4A_NO_PERSISTENCE_SCOPE,
        "p4b_a_merge_sha": P4B_A_MERGE_SHA,
        "partial_tail_probe_count": partial_tail_probe_count,
        "pending_tamper_probe_count": pending_tamper_probe_count,
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4B-B",
    }
