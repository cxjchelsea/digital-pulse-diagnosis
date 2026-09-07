"""M1-P4B-E aggregate formal acceptance. No new P4C/P4D/P4E business semantics."""

from __future__ import annotations

import ast
from dataclasses import fields
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from digital_pulse.m1_contracts import (
    SCHEMA_VERSION,
    DecisionAction,
    DecisionInputVersions,
    M1Decision,
    ParameterStatus,
    QualityReference,
)
from digital_pulse.m1_int import DecisionLedger, DecisionSourceProvenance, M1IntError
from digital_pulse.m1_int.ledger_models import LEDGER_SCHEMA_VERSION, build_int_ledger_event
from digital_pulse.m1_int.models import dumps_canonical
from digital_pulse.m1_int.override_safety import OverrideClassification, classify_override
from digital_pulse.m1_int.replay import fold_ledger_snapshot
from digital_pulse.m1_int.replay_models import LedgerSnapshot
from digital_pulse.m1_int.rules import I1RuleEngine
from digital_pulse.m1_p4a_acceptance import (
    EXPECTED_D3_TAG_OBJECT,
    EXPECTED_D3_TAG_TARGET,
    EXPECTED_P2_GOLDEN,
    EXPECTED_P3_DIGEST,
    EXPECTED_P3_SOURCE,
    _scan_source_boundaries,
    run_m1_p4a_acceptance,
)
from digital_pulse.m1_p4b_a_acceptance import run_m1_p4b_a_acceptance
from digital_pulse.m1_p4b_b_acceptance import run_m1_p4b_b_acceptance
from digital_pulse.m1_p4b_c_acceptance import run_m1_p4b_c_acceptance
from digital_pulse.m1_p4b_d_acceptance import run_m1_p4b_d_acceptance

ACCEPTANCE_VERSION = "m1-p4b-e-acceptance-v1"
P4B_D_MERGE_SHA = "37db161587e8d87c98b4de5feee25f152fb34705"
ARCHITECTURE_BASE_SHA = "b9bdc598b0c464f1dd199505e6e99de1095b0ab4"
ROOT = Path(__file__).resolve().parents[2]
INT_PKG = ROOT / "src" / "digital_pulse" / "m1_int"
REPLAY_FILES = (INT_PKG / "replay.py", INT_PKG / "replay_models.py")
CONTRACT_FILES = (
    INT_PKG / "ledger_models.py",
    INT_PKG / "rules.py",
    INT_PKG / "projection.py",
    INT_PKG / "override_safety.py",
)
SESSION_ID = "session-p4b-e-acceptance"
DECISION_ID = "m1-decision-" + ("ab" * 32)
SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CONFIG_DIGEST = "cd" * 32
FINGERPRINT = "ef" * 32
CLOCK = "2026-01-01T00:00:00Z"
SCOPE = "m1-retry-scope-" + ("cd" * 32)
LINKED_SESSION = "session-p4b-e-linked"
REQUIRED_GATES = (
    "p4ba_regression",
    "p4bb_regression",
    "p4bc_regression",
    "p4bd_regression",
    "machine_decision_immutability",
    "decision_persistence",
    "event_append_only",
    "event_seq_integrity",
    "override_safety",
    "override_replay",
    "outcome_replay",
    "manual_review_lifecycle",
    "completed_terminal",
    "rejected_override_non_effective",
    "action_applied_non_invention",
    "manifest_source_truth",
    "manifest_reconciliation",
    "corruption_fail_closed",
    "partial_tail_fail_closed",
    "decision_event_cross_reference",
    "replay_no_recompute",
    "deterministic_replay",
    "replay_fingerprint",
    "provenance_integrity",
    "schema_freeze",
    "reason_code_boundary",
    "oracle_isolation",
    "p4c_boundary",
    "report_boundary",
    "hardware_boundary",
    "exact_head",
    "aggregate_p4b",
)
P4C_SCAN_NEEDLES = (
    "retry_count + 1",
    "retry_count +=",
    "enforce max_retry_count",
    "consume retry budget",
    "schedule retry",
    "schedule reposition",
    "start acquisition",
    "close RetryScope automatically",
)
RECOMPUTE_NEEDLES = (
    "I1RuleEngine",
    "project_m1_decision",
    "from digital_pulse.m1_int.rules",
    "from digital_pulse.m1_int.projection",
    "from .rules",
    "from .projection",
)
REASON_LEAK_NEEDLES = (
    "reason_codes =",
    "reason_codes +=",
    "reason_codes.append",
)
FROZEN_EVENT_SET = frozenset(
    {
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


def _machine(action: DecisionAction = DecisionAction.ACCEPT, *, session_id: str = SESSION_ID) -> M1Decision:
    reason = "emergency_stop" if action is DecisionAction.ABORT_AND_RELEASE else "quality_acceptable"
    if action is DecisionAction.MANUAL_REVIEW:
        reason = "quality_manual_review_required"
    return M1Decision(
        decision_id=DECISION_ID,
        session_id=session_id,
        decided_at_utc=CLOCK,
        milestone="M1",
        int_level="I1",
        device_state="ACQUIRE",
        quality_reference=QualityReference(session_id=session_id, window_id="window-0001"),
        action=action,
        reason_codes=(reason,),
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
        app_run_id="run-p4b-e",
        app_analysis_fingerprint=FINGERPRINT,
        sp_result_fingerprint=FINGERPRINT,
        run_signal_processing_version="0.4.0-p2d",
        session_signal_processing_version="0.4.0-p2d",
        software_commit_sha=SOFTWARE_SHA,
    )


def _int_dir(root: Path, session_id: str = SESSION_ID) -> Path:
    return root / session_id / "int"


def _event_line(event) -> bytes:
    payload = {item.name: getattr(event, item.name) for item in fields(event) if getattr(event, item.name) is not None}
    return (dumps_canonical(payload) + "\n").encode("utf-8")


def _fail_closed(runner, *codes: str) -> bool:
    try:
        runner()
    except M1IntError as exc:
        return exc.code in codes
    return False


def _events_path(root: Path, session_id: str = SESSION_ID) -> Path:
    return _int_dir(root, session_id) / "decision-events.jsonl"


def _decisions_path(root: Path, session_id: str = SESSION_ID) -> Path:
    return _int_dir(root, session_id) / "decisions.jsonl"


def _rewrite_events(root: Path, mutator) -> None:
    path = _events_path(root)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    path.write_text("".join(dumps_canonical(item) + "\n" for item in mutator(lines)), encoding="utf-8")


def _scan_boundaries() -> dict[str, Any]:
    oracle_ok = True
    recompute_ok = True
    p4c_ok = True
    hardware_ok = True
    report_ok = True
    reason_ok = True
    evaluate_ok = True
    for path in (*REPLAY_FILES, *CONTRACT_FILES):
        source = path.read_text(encoding="utf-8")
        oracle_ok = oracle_ok and _scan_source_boundaries(source, filename=str(path))[0]
        if path in REPLAY_FILES:
            for needle in RECOMPUTE_NEEDLES:
                if needle in source:
                    recompute_ok = False
            if ".evaluate(" in source:
                evaluate_ok = False
        if path.name != "rules.py":
            for needle in P4C_SCAN_NEEDLES:
                if needle in source:
                    p4c_ok = False
        if path in REPLAY_FILES or path.name == "override_safety.py":
            if "hardware" in source.lower():
                hardware_ok = False
            if "int/reports/" in source or "generate_report" in source:
                report_ok = False
        if path in REPLAY_FILES:
            for needle in REASON_LEAK_NEEDLES:
                if needle in source:
                    reason_ok = False
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and path in REPLAY_FILES:
                module = node.module or ""
                if module.endswith(".rules") or module.endswith(".projection") or module in {
                    "digital_pulse.m1_int.rules",
                    "digital_pulse.m1_int.projection",
                }:
                    recompute_ok = False
    persist_root = INT_PKG / "persist"
    for path in persist_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in P4C_SCAN_NEEDLES:
            if needle in source:
                p4c_ok = False
        if "generate_report" in source:
            report_ok = False
    oracle_probes = (
        "value = 'expected_int_action'\n",
        "value = 'scenario.json'\n",
    )
    oracle_self_test = all(not _scan_source_boundaries(source)[0] for source in oracle_probes)
    return {
        "oracle_ok": oracle_ok and oracle_self_test,
        "recompute_ok": recompute_ok and evaluate_ok,
        "p4c_ok": p4c_ok,
        "hardware_ok": hardware_ok,
        "report_ok": report_ok,
        "reason_ok": reason_ok,
        "oracle_scanner_self_test": oracle_self_test,
    }


def _run_aggregate_happy_path(root: Path) -> dict[str, Any]:
    ledger = DecisionLedger(root, clock=lambda: CLOCK)
    ledger.append_decision(_machine(), _provenance())
    before_decision = _decisions_path(root).read_bytes()
    machine_before = ledger.load_machine_decision(SESSION_ID, DECISION_ID)
    recorded = ledger.replay_session(SESSION_ID)
    ledger.persist_operator_override(
        SESSION_ID,
        DECISION_ID,
        requested_action="stop",
        operator_id="op-001",
        note="stop now",
        source_provenance=_provenance(),
    )
    after_override_events = _events_path(root).read_bytes()
    ledger.persist_action_applied(SESSION_ID, DECISION_ID, source_provenance=_provenance())
    after_applied_events = _events_path(root).read_bytes()
    ledger.persist_decision_completed(SESSION_ID, DECISION_ID, source_provenance=_provenance())
    first = ledger.replay_session(SESSION_ID)
    second = ledger.replay_session(SESSION_ID)
    copied = root / "copy"
    shutil.copytree(root / SESSION_ID, copied / SESSION_ID)
    copied_result = DecisionLedger(copied, clock=lambda: "2099-01-01T00:00:00Z").replay_session(SESSION_ID)
    machine_after = ledger.load_machine_decision(SESSION_ID, DECISION_ID)
    applied_event = [item for item in first.events if item.event_type == "action_applied"][0]
    view = first.views[0]
    return {
        "recorded": recorded,
        "first": first,
        "second": second,
        "copied": copied_result,
        "before_decision": before_decision,
        "after_decision": _decisions_path(root).read_bytes(),
        "after_override_events": after_override_events,
        "after_applied_events": after_applied_events,
        "final_events": _events_path(root).read_bytes(),
        "machine_before": machine_before,
        "machine_after": machine_after,
        "applied_event": applied_event,
        "view": view,
        "report_untouched": not (root / SESSION_ID / "app").exists(),
    }


def run_m1_p4b_e_acceptance(*, software_commit_sha: str, expected_head_sha: str) -> dict[str, Any]:
    exact_head = software_commit_sha == expected_head_sha
    scan = _scan_boundaries()
    immutable = False
    persisted = False
    append_only = False
    seq_ok = False
    override_safety_ok = False
    override_replay_ok = False
    outcome_ok = False
    manual_ok = False
    terminal_ok = False
    rejected_ok = False
    non_invention = False
    manifest_sot = False
    reconcile_ok = False
    corruption_ok = False
    partial_ok = False
    xref_ok = False
    no_recompute = False
    deterministic = False
    fingerprint_ok = False
    provenance_ok = False
    schema_ok = False
    reason_ok = False
    p4c_runtime_ok = False
    report_ok = False
    aggregate_ok = False
    adversarial_ok = True

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        happy = _run_aggregate_happy_path(root / "happy")
        view = happy["view"]
        first = happy["first"]
        immutable = (
            happy["before_decision"] == happy["after_decision"]
            and happy["machine_after"].operator_override is None
            and happy["machine_after"].outcome is None
            and happy["machine_after"].action == happy["machine_before"].action
            and happy["machine_after"].reason_codes == happy["machine_before"].reason_codes
        )
        persisted = (
            first.integrity_status == "trusted"
            and first.events[0].event_type == "decision_recorded"
            and first.machine_decisions[0].decision_id == DECISION_ID
        )
        append_only = (
            len(happy["after_override_events"]) > 0
            and happy["after_applied_events"].startswith(happy["after_override_events"])
            and happy["final_events"].startswith(happy["after_applied_events"])
        )
        seq_ok = all(event.event_seq == index for index, event in enumerate(first.events, start=1))
        override_replay_ok = (
            view.machine_action == "accept"
            and view.replayed_action == "stop"
            and view.outcome == "completed"
            and view.completed is True
        )
        outcome_ok = view.outcome == "completed" and view.derived_action_at_apply == "stop"
        non_invention = happy["applied_event"].requested_action is None
        provenance_ok = view.provenance["software_commit_sha"] == SOFTWARE_SHA
        fingerprint_ok = (
            first.replay_fingerprint == happy["second"].replay_fingerprint
            and first.replay_fingerprint == happy["copied"].replay_fingerprint
            and CLOCK not in first.replay_fingerprint
            and str(root) not in first.replay_fingerprint
        )
        deterministic = fingerprint_ok
        report_ok = happy["report_untouched"] and scan["report_ok"]
        reason_ok = (
            scan["reason_ok"]
            and view.machine_reason_codes == ("quality_acceptable",)
            and "lifecycle_conflict" not in view.machine_reason_codes
        )

        reject_root = root / "reject"
        reject = DecisionLedger(reject_root, clock=lambda: CLOCK)
        reject.append_decision(_machine(), _provenance())
        reject.persist_operator_override(
            SESSION_ID,
            DECISION_ID,
            requested_action="retry_same_position",
            operator_id="op-001",
            note="weaken",
            source_provenance=_provenance(),
        )
        rejected = reject.replay_session(SESSION_ID)
        rejected_ok = (
            rejected.views[0].replayed_action == "accept"
            and rejected.views[0].machine_action == "accept"
            and rejected.views[0].rejection_facts[0].event_type == "action_rejected_by_safety"
            and rejected.views[0].outcome is None
        )

        abort_root = root / "abort"
        abort = DecisionLedger(abort_root, clock=lambda: CLOCK)
        abort.append_decision(_machine(DecisionAction.ABORT_AND_RELEASE), _provenance())
        abort.persist_operator_override(
            SESSION_ID,
            DECISION_ID,
            requested_action="accept",
            operator_id="op-001",
            note="weaken abort",
            source_provenance=_provenance(),
        )
        abort_view = abort.replay_session(SESSION_ID).views[0]
        override_safety_ok = (
            classify_override("abort_and_release", "accept") is OverrideClassification.REJECTED_BY_SAFETY
            and abort_view.replayed_action == "abort_and_release"
            and abort_view.rejection_facts[0].requested_action == "accept"
        )

        review_root = root / "review"
        review = DecisionLedger(review_root, clock=lambda: CLOCK)
        review.append_decision(_machine(DecisionAction.MANUAL_REVIEW), _provenance())
        review.persist_manual_review_resolution(
            SESSION_ID,
            DECISION_ID,
            resolution="terminate_stop",
            operator_id="op-001",
            source_provenance=_provenance(),
        )
        reviewed = review.replay_session(SESSION_ID)
        manual_ok = (
            reviewed.views[0].manual_review_resolution == "terminate_stop"
            and reviewed.views[0].awaiting_operator is False
        )

        terminal_root = root / "terminal"
        terminal = DecisionLedger(terminal_root, clock=lambda: CLOCK)
        terminal.append_decision(_machine(), _provenance())
        terminal.persist_decision_completed(SESSION_ID, DECISION_ID, source_provenance=_provenance())
        try:
            terminal.persist_action_applied(SESSION_ID, DECISION_ID, source_provenance=_provenance())
            terminal_ok = _fail_closed(lambda: terminal.replay_session(SESSION_ID), "lifecycle_conflict")
        except M1IntError as exc:
            terminal_ok = exc.code in {"lifecycle_conflict", "invalid_input", "ledger_untrusted"}
        override_after = DecisionLedger(root / "terminal-override", clock=lambda: CLOCK)
        override_after.append_decision(_machine(), _provenance())
        override_after.persist_decision_completed(SESSION_ID, DECISION_ID, source_provenance=_provenance())
        try:
            override_after.persist_operator_override(
                SESSION_ID,
                DECISION_ID,
                requested_action="stop",
                operator_id="op-001",
                note="after complete",
                source_provenance=_provenance(),
            )
            terminal_ok = terminal_ok and _fail_closed(
                lambda: override_after.replay_session(SESSION_ID),
                "lifecycle_conflict",
            )
        except M1IntError as exc:
            terminal_ok = terminal_ok and exc.code in {"lifecycle_conflict", "invalid_input", "ledger_untrusted"}

        (_int_dir(root / "happy") / "manifest.json").unlink()
        reconciled = DecisionLedger(root / "happy", clock=lambda: CLOCK).replay_session(SESSION_ID)
        reconcile_ok = reconciled.integrity_status == "trusted" and reconciled.replay_fingerprint == first.replay_fingerprint

        stale = DecisionLedger(root / "stale", clock=lambda: CLOCK)
        stale.append_decision(_machine(), _provenance())
        manifest_path = _int_dir(root / "stale") / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["events_sha256"] = "ab" * 32
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        stale._reconcile_manifest = lambda *args, **kwargs: None  # type: ignore[method-assign]
        manifest_sot = _fail_closed(lambda: stale.replay_session(SESSION_ID), "manifest_mismatch")
        missing = DecisionLedger(root / "missing-manifest", clock=lambda: CLOCK)
        missing.append_decision(_machine(), _provenance())
        (_int_dir(root / "missing-manifest") / "manifest.json").unlink()
        missing_replay = missing.replay_session(SESSION_ID)
        manifest_sot = manifest_sot and missing_replay.integrity_status == "trusted"

        corrupt = DecisionLedger(root / "corrupt", clock=lambda: CLOCK)
        corrupt.append_decision(_machine(), _provenance())
        _decisions_path(root / "corrupt").write_text("not-json\n", encoding="utf-8")
        corrupt_manifest = _int_dir(root / "corrupt") / "manifest.json"
        healthy = json.loads(corrupt_manifest.read_text(encoding="utf-8"))
        corrupt_manifest.write_text(json.dumps(healthy), encoding="utf-8")
        corruption_ok = _fail_closed(
            lambda: DecisionLedger(root / "corrupt", clock=lambda: CLOCK).replay_session(SESSION_ID),
            "ledger_untrusted",
        )

        tail = DecisionLedger(root / "tail", clock=lambda: CLOCK)
        tail.append_decision(_machine(), _provenance())
        events_path = _events_path(root / "tail")
        events_path.write_bytes(events_path.read_bytes() + b'{"event_type":"broken"')
        partial_ok = _fail_closed(
            lambda: DecisionLedger(root / "tail", clock=lambda: CLOCK).replay_session(SESSION_ID),
            "ledger_untrusted",
        )

        def _replay_mutated(name: str, mutator) -> None:
            ledger = DecisionLedger(root / name, clock=lambda: CLOCK)
            ledger.append_decision(_machine(), _provenance())
            _rewrite_events(root / name, mutator)
            DecisionLedger(root / name, clock=lambda: CLOCK).replay_session(SESSION_ID)

        adversarial_ok = adversarial_ok and _fail_closed(
            lambda: _replay_mutated("gap", lambda lines: [{**lines[0], "event_seq": 3}]),
            "ledger_untrusted",
        )
        adversarial_ok = adversarial_ok and _fail_closed(
            lambda: _replay_mutated(
                "dup-seq",
                lambda lines: lines + [{**lines[0], "event_seq": 1, "event_type": "action_applied", "outcome": "applied"}],
            ),
            "ledger_untrusted",
            "unsupported_event_type",
            "dangling_decision_reference",
        )
        adversarial_ok = adversarial_ok and _fail_closed(
            lambda: _replay_mutated("reorder", lambda lines: list(reversed(lines)) if len(lines) > 1 else [{**lines[0], "event_seq": 2}]),
            "ledger_untrusted",
        )

        dangling = DecisionLedger(root / "dangling", clock=lambda: CLOCK)
        dangling.append_decision(_machine(), _provenance())
        extra = build_int_ledger_event(
            event_seq=2,
            event_type="action_applied",
            session_id=SESSION_ID,
            occurred_at_utc=CLOCK,
            decision_id="m1-decision-" + ("ff" * 32),
            outcome="applied",
        )
        with _events_path(root / "dangling").open("ab") as handle:
            handle.write(_event_line(extra))
        xref_ok = _fail_closed(
            lambda: DecisionLedger(root / "dangling", clock=lambda: CLOCK).replay_session(SESSION_ID),
            "dangling_decision_reference",
        )

        duplicated = DecisionLedger(root / "dup-recorded", clock=lambda: CLOCK)
        duplicated.append_decision(_machine(), _provenance())
        recorded_copy = build_int_ledger_event(
            event_seq=2,
            event_type="decision_recorded",
            session_id=SESSION_ID,
            occurred_at_utc=CLOCK,
            decision_id=DECISION_ID,
            software_commit_sha=SOFTWARE_SHA,
            rule_version="i1-pre-0.1.0",
            configuration_digest=CONFIG_DIGEST,
            app_run_id="run-p4b-e",
            app_analysis_fingerprint=FINGERPRINT,
            sp_result_fingerprint=FINGERPRINT,
        )
        with _events_path(root / "dup-recorded").open("ab") as handle:
            handle.write(_event_line(recorded_copy))
        adversarial_ok = adversarial_ok and _fail_closed(
            lambda: DecisionLedger(root / "dup-recorded", clock=lambda: CLOCK).replay_session(SESSION_ID),
            "decision_record_mismatch",
            "ledger_untrusted",
        )

        conflict = DecisionLedger(root / "conflict", clock=lambda: CLOCK)
        conflict.append_decision(_machine(), _provenance())
        conflict.persist_operator_override(
            SESSION_ID,
            DECISION_ID,
            requested_action="stop",
            operator_id="op-001",
            note="first",
            source_provenance=_provenance(),
        )
        try:
            conflict.persist_operator_override(
                SESSION_ID,
                DECISION_ID,
                requested_action="manual_review",
                operator_id="op-001",
                note="second",
                source_provenance=_provenance(),
            )
            adversarial_ok = adversarial_ok and _fail_closed(
                lambda: conflict.replay_session(SESSION_ID),
                "lifecycle_conflict",
            )
        except M1IntError as exc:
            adversarial_ok = adversarial_ok and exc.code in {"lifecycle_conflict", "invalid_input", "duplicate_conflict"}

        schema = DecisionLedger(root / "schema", clock=lambda: CLOCK)
        schema.append_decision(_machine(), _provenance())

        def _schema(lines):
            lines[0]["ledger_schema_version"] = "i1-ledger-9.9.9"
            return lines

        adversarial_ok = adversarial_ok and _fail_closed(
            lambda: _replay_mutated("schema", _schema),
            "unsupported_schema_version",
            "ledger_untrusted",
        )

        def _unknown(lines):
            lines[0]["event_type"] = "not_a_frozen_event"
            return lines

        adversarial_ok = adversarial_ok and _fail_closed(
            lambda: _replay_mutated("unknown", _unknown),
            "unsupported_event_type",
            "ledger_untrusted",
        )

        def _provenance_tamper(lines):
            lines[0]["event_id"] = "m1-int-event-" + ("00" * 32)
            return lines

        adversarial_ok = adversarial_ok and _fail_closed(
            lambda: _replay_mutated("prov", _provenance_tamper),
            "ledger_untrusted",
        )

        unverified = _fail_closed(
            lambda: fold_ledger_snapshot(
                LedgerSnapshot(
                    session_id=SESSION_ID,
                    machine_decisions=(),
                    events=(),
                    ledger_schema_version=LEDGER_SCHEMA_VERSION,
                    manifest_schema_version="i1-ledger-manifest-1.0.0-pre",
                    decisions_sha256="ab" * 32,
                    events_sha256="cd" * 32,
                    last_event_seq=0,
                    _token=object(),
                )
            ),
            "invalid_input",
        )
        adversarial_ok = adversarial_ok and unverified

        p4c = DecisionLedger(root / "p4c", clock=lambda: CLOCK)
        p4c.append_decision(_machine(), _provenance())
        p4c.persist_retry_scope_started(SESSION_ID, retry_scope_id=SCOPE, source_provenance=_provenance())
        p4c.persist_retry_attempt_linked(
            SESSION_ID,
            DECISION_ID,
            retry_scope_id=SCOPE,
            linked_session_id=LINKED_SESSION,
            source_provenance=_provenance(),
        )
        p4c.persist_reposition_acknowledged(
            SESSION_ID,
            DECISION_ID,
            operator_id="op-001",
            prior_scope_id=SCOPE,
            new_session_id=LINKED_SESSION,
            source_provenance=_provenance(),
        )
        p4c.persist_retry_scope_closed(SESSION_ID, retry_scope_id=SCOPE, source_provenance=_provenance())
        p4c_result = p4c.replay_session(SESSION_ID)
        p4c_types = [item.event_type for item in p4c_result.p4c_facts]
        p4c_runtime_ok = (
            p4c_types == [
                "retry_scope_started",
                "retry_attempt_linked",
                "reposition_acknowledged",
                "retry_scope_closed",
            ]
            and not hasattr(p4c_result, "retry_scope_state")
            and p4c_result.views[0].replayed_action == "accept"
            and getattr(p4c_result, "retry_count", None) is None
        )

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("rule engine must not run during replay")

        original = I1RuleEngine.evaluate
        I1RuleEngine.evaluate = boom  # type: ignore[method-assign]
        try:
            patched = DecisionLedger(root / "happy", clock=lambda: CLOCK).replay_session(SESSION_ID)
            no_recompute = patched.integrity_status == "trusted" and scan["recompute_ok"]
        finally:
            I1RuleEngine.evaluate = original

        aggregate_ok = (
            immutable
            and persisted
            and append_only
            and override_replay_ok
            and outcome_ok
            and rejected_ok
            and terminal_ok
            and p4c_runtime_ok
            and adversarial_ok
        )

    p4a = run_m1_p4a_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p4ba = run_m1_p4b_a_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p4bb = run_m1_p4b_b_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p4bc = run_m1_p4b_c_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p4bd = run_m1_p4b_d_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p3_ok = EXPECTED_P3_SOURCE == "2f4f88cc69fbdfb1e129d347025695334542eb9e"
    p3_digest_ok = EXPECTED_P3_DIGEST == "fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f"
    p2_ok = EXPECTED_P2_GOLDEN == "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"
    try:
        d3_ok = _git("rev-parse", "d3-v1.0.0") == EXPECTED_D3_TAG_OBJECT
        d3_ok = d3_ok and _git("rev-parse", "d3-v1.0.0^{commit}") == EXPECTED_D3_TAG_TARGET
    except (subprocess.CalledProcessError, FileNotFoundError):
        d3_ok = False
    schema_ok = SCHEMA_VERSION == "1.0.0" and LEDGER_SCHEMA_VERSION == "i1-ledger-1.0.0-pre" and p3_ok and p2_ok and d3_ok

    gates = {
        "p4ba_regression": _gate("p4ba_regression", bool(p4ba.get("acceptance"))),
        "p4bb_regression": _gate("p4bb_regression", bool(p4bb.get("acceptance"))),
        "p4bc_regression": _gate("p4bc_regression", bool(p4bc.get("acceptance"))),
        "p4bd_regression": _gate("p4bd_regression", bool(p4bd.get("acceptance"))),
        "machine_decision_immutability": _gate("machine_decision_immutability", immutable),
        "decision_persistence": _gate("decision_persistence", persisted),
        "event_append_only": _gate("event_append_only", append_only),
        "event_seq_integrity": _gate("event_seq_integrity", seq_ok),
        "override_safety": _gate("override_safety", override_safety_ok),
        "override_replay": _gate("override_replay", override_replay_ok),
        "outcome_replay": _gate("outcome_replay", outcome_ok),
        "manual_review_lifecycle": _gate("manual_review_lifecycle", manual_ok),
        "completed_terminal": _gate("completed_terminal", terminal_ok),
        "rejected_override_non_effective": _gate("rejected_override_non_effective", rejected_ok),
        "action_applied_non_invention": _gate("action_applied_non_invention", non_invention),
        "manifest_source_truth": _gate("manifest_source_truth", manifest_sot),
        "manifest_reconciliation": _gate("manifest_reconciliation", reconcile_ok),
        "corruption_fail_closed": _gate("corruption_fail_closed", corruption_ok),
        "partial_tail_fail_closed": _gate("partial_tail_fail_closed", partial_ok),
        "decision_event_cross_reference": _gate("decision_event_cross_reference", xref_ok),
        "replay_no_recompute": _gate("replay_no_recompute", no_recompute),
        "deterministic_replay": _gate("deterministic_replay", deterministic),
        "replay_fingerprint": _gate("replay_fingerprint", fingerprint_ok),
        "provenance_integrity": _gate("provenance_integrity", provenance_ok and adversarial_ok),
        "schema_freeze": _gate("schema_freeze", schema_ok and p3_digest_ok),
        "reason_code_boundary": _gate("reason_code_boundary", reason_ok),
        "oracle_isolation": _gate(
            "oracle_isolation",
            scan["oracle_ok"],
            oracle_scanner_self_test=scan["oracle_scanner_self_test"],
        ),
        "p4c_boundary": _gate("p4c_boundary", scan["p4c_ok"] and p4c_runtime_ok),
        "report_boundary": _gate("report_boundary", report_ok),
        "hardware_boundary": _gate("hardware_boundary", scan["hardware_ok"]),
        "exact_head": _gate("exact_head", exact_head, software_commit_sha=software_commit_sha),
        "aggregate_p4b": _gate("aggregate_p4b", aggregate_ok and bool(p4bd.get("acceptance"))),
    }
    failed = [name for name, payload in gates.items() if not payload["passed"]]
    extra_ok = bool(p4a.get("acceptance")) and set(REQUIRED_GATES) <= set(gates)
    acceptance = not failed and extra_ok
    return {
        "acceptance": acceptance,
        "acceptance_version": ACCEPTANCE_VERSION,
        "aggregate_stage": "M1-P4B",
        "architecture_base_sha": ARCHITECTURE_BASE_SHA,
        "failed_gates": failed,
        "frozen_assets_unchanged": p3_ok and p3_digest_ok and p2_ok and d3_ok,
        "gates": gates,
        "p4a_acceptance": bool(p4a.get("acceptance")),
        "p4b_d_merge_sha": P4B_D_MERGE_SHA,
        "required_gate_count": len(REQUIRED_GATES),
        "passed_gate_count": len(REQUIRED_GATES) - len(failed),
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4B-E",
    }
