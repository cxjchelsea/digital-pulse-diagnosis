"""M1-P4B-D slice 验收：replay / integrity only。"""

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
    DecisionAction,
    DecisionInputVersions,
    M1Decision,
    ParameterStatus,
    QualityReference,
)
from digital_pulse.m1_int import DecisionLedger, DecisionSourceProvenance, M1IntError
from digital_pulse.m1_int.ledger_models import LEDGER_SCHEMA_VERSION, build_int_ledger_event
from digital_pulse.m1_int.models import dumps_canonical
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

ACCEPTANCE_VERSION = "m1-p4b-d-acceptance-v1"
P4B_C_MERGE_SHA = "bbbf513a68801b7c918e458f44d54d02d074e710"
ARCHITECTURE_BASE_SHA = "b9bdc598b0c464f1dd199505e6e99de1095b0ab4"
ROOT = Path(__file__).resolve().parents[2]
INT_PKG = ROOT / "src" / "digital_pulse" / "m1_int"
REPLAY_FILES = (INT_PKG / "replay.py", INT_PKG / "replay_models.py")
SESSION_ID = "session-p4b-d-acceptance"
DECISION_ID = "m1-decision-" + ("ab" * 32)
SOFTWARE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CONFIG_DIGEST = "cd" * 32
FINGERPRINT = "ef" * 32
CLOCK = "2026-01-01T00:00:00Z"
SCOPE = "m1-retry-scope-" + ("cd" * 32)
REQUIRED_GATES = (
    "replay_basic",
    "deterministic_fold",
    "machine_decision_immutable",
    "event_ordering",
    "event_seq_integrity",
    "decision_event_cross_reference",
    "lifecycle_consistency",
    "partial_tail_fail_closed",
    "corruption_fail_closed",
    "provenance",
    "snapshot_consistency",
    "replay_no_recompute",
    "oracle_isolation",
    "manifest_reconciliation",
    "manifest_final_integrity",
    "action_applied_non_invention",
    "business_read_only_replay",
    "recovery_boundary",
    "p4bb_regression",
    "p4bc_regression",
    "p4c_boundary",
    "report_boundary",
    "hardware_boundary",
    "exact_head",
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
        app_run_id="run-p4b-d",
        app_analysis_fingerprint=FINGERPRINT,
        sp_result_fingerprint=FINGERPRINT,
        run_signal_processing_version="0.4.0-p2d",
        session_signal_processing_version="0.4.0-p2d",
        software_commit_sha=SOFTWARE_SHA,
    )


def _int_dir(root: Path) -> Path:
    return root / SESSION_ID / "int"


def _event_line(event) -> bytes:
    payload = {item.name: getattr(event, item.name) for item in fields(event) if getattr(event, item.name) is not None}
    return (dumps_canonical(payload) + "\n").encode("utf-8")


def _fail_at(point: str):
    def injector(actual: str) -> None:
        if actual == point:
            raise M1IntError("persistence_failure", f"injected failure at {actual}")

    return injector


def _scan_replay_boundaries() -> dict[str, Any]:
    oracle_ok = True
    recompute_ok = True
    p4c_ok = True
    hardware_ok = True
    report_ok = True
    evaluate_ok = True
    for path in REPLAY_FILES:
        source = path.read_text(encoding="utf-8")
        oracle_ok = oracle_ok and _scan_source_boundaries(source, filename=str(path))[0]
        persistence_ok = _scan_source_boundaries(source, filename=str(path))[1]
        if not persistence_ok:
            report_ok = False
        for needle in RECOMPUTE_NEEDLES:
            if needle in source:
                recompute_ok = False
        if ".evaluate(" in source:
            evaluate_ok = False
        for needle in P4C_SCAN_NEEDLES:
            if needle in source:
                p4c_ok = False
        if "hardware" in source.lower():
            hardware_ok = False
        if "int/reports/" in source or "generate_report" in source:
            report_ok = False
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.endswith(".rules") or module.endswith(".projection") or module in {
                    "digital_pulse.m1_int.rules",
                    "digital_pulse.m1_int.projection",
                }:
                    recompute_ok = False
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
        "oracle_scanner_self_test": oracle_self_test,
    }


def run_m1_p4b_d_acceptance(*, software_commit_sha: str, expected_head_sha: str) -> dict[str, Any]:
    exact_head = software_commit_sha == expected_head_sha
    scan = _scan_replay_boundaries()
    probe_count = 0
    replay_basic = False
    deterministic = False
    immutable = False
    ordering = False
    seq_ok = False
    xref = False
    lifecycle = False
    partial_ok = False
    corruption_ok = False
    provenance_ok = False
    snapshot_ok = False
    no_recompute = False
    reconcile_ok = False
    final_manifest_ok = False
    non_invention = False
    business_ro = False
    recovery_ok = False
    report_ok = False
    p4c_runtime_ok = False

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = DecisionLedger(root, clock=lambda: CLOCK)
        ledger.append_decision(_machine(), _provenance())
        before_decisions = (_int_dir(root) / "decisions.jsonl").read_bytes()
        before_events = (_int_dir(root) / "decision-events.jsonl").read_bytes()
        first = ledger.replay_session(SESSION_ID)
        replay_basic = (
            first.integrity_status == "trusted"
            and first.views[0].machine_action == "accept"
            and first.views[0].replayed_action == "accept"
        )
        probe_count += 1
        second = ledger.replay_session(SESSION_ID)
        deterministic = first.replay_fingerprint == second.replay_fingerprint
        probe_count += 1
        after_decisions = (_int_dir(root) / "decisions.jsonl").read_bytes()
        after_events = (_int_dir(root) / "decision-events.jsonl").read_bytes()
        machine = ledger.load_machine_decision(SESSION_ID, DECISION_ID)
        immutable = (
            after_decisions == before_decisions
            and machine.operator_override is None
            and machine.outcome is None
        )
        business_ro = after_decisions == before_decisions and after_events == before_events
        probe_count += 2
        report_ok = not (root / SESSION_ID / "app").exists()
        seq_ok = first.last_event_seq == 1 and first.events[0].event_seq == 1
        ordering = all(event.event_seq == index for index, event in enumerate(first.events, start=1))
        xref = first.events[0].decision_id == DECISION_ID
        provenance_ok = first.views[0].provenance["software_commit_sha"] == SOFTWARE_SHA
        snapshot_ok = first.machine_decisions[0].decision_id == DECISION_ID
        probe_count += 4

        ledger.persist_operator_override(
            SESSION_ID,
            DECISION_ID,
            requested_action="stop",
            operator_id="op-001",
            note="stop now",
            source_provenance=_provenance(),
        )
        ledger.persist_action_applied(SESSION_ID, DECISION_ID, source_provenance=_provenance())
        applied = ledger.replay_session(SESSION_ID)
        applied_event = [item for item in applied.events if item.event_type == "action_applied"][0]
        non_invention = (
            applied_event.requested_action is None
            and applied.views[0].derived_action_at_apply == "stop"
            and applied.views[0].machine_action == "accept"
            and applied.views[0].outcome == "applied"
        )
        probe_count += 1

        complete_root = root / "complete"
        complete = DecisionLedger(complete_root, clock=lambda: CLOCK)
        complete.append_decision(_machine(), _provenance())
        complete.persist_decision_completed(SESSION_ID, DECISION_ID, source_provenance=_provenance())
        complete.persist_action_applied(SESSION_ID, DECISION_ID, source_provenance=_provenance())
        try:
            complete.replay_session(SESSION_ID)
            lifecycle = False
        except M1IntError as exc:
            lifecycle = exc.code == "lifecycle_conflict"
        probe_count += 1

        (_int_dir(root) / "manifest.json").unlink()
        reconciled = ledger.replay_session(SESSION_ID)
        reconcile_ok = reconciled.integrity_status == "trusted"
        probe_count += 1

        stale = DecisionLedger(root / "stale", clock=lambda: CLOCK)
        stale.append_decision(_machine(), _provenance())
        manifest_path = root / "stale" / SESSION_ID / "int" / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["events_sha256"] = "ab" * 32
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        stale._reconcile_manifest = lambda *args, **kwargs: None  # type: ignore[method-assign]
        try:
            stale.replay_session(SESSION_ID)
            final_manifest_ok = False
        except M1IntError as exc:
            final_manifest_ok = exc.code == "manifest_mismatch"
        probe_count += 1

        tail = DecisionLedger(root / "tail", clock=lambda: CLOCK)
        tail.append_decision(_machine(), _provenance())
        events_path = root / "tail" / SESSION_ID / "int" / "decision-events.jsonl"
        events_path.write_bytes(events_path.read_bytes() + b'{"event_type":"broken"')
        try:
            tail.replay_session(SESSION_ID)
            partial_ok = False
        except M1IntError as exc:
            partial_ok = exc.code == "ledger_untrusted"
        probe_count += 1

        corrupt = DecisionLedger(root / "corrupt", clock=lambda: CLOCK)
        corrupt.append_decision(_machine(), _provenance())
        (root / "corrupt" / SESSION_ID / "int" / "decisions.jsonl").write_text("not-json\n", encoding="utf-8")
        try:
            corrupt.replay_session(SESSION_ID)
            corruption_ok = False
        except M1IntError as exc:
            corruption_ok = exc.code == "ledger_untrusted"
        probe_count += 1

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("rule engine must not run during replay")

        original = I1RuleEngine.evaluate
        I1RuleEngine.evaluate = boom  # type: ignore[method-assign]
        try:
            patched = ledger.replay_session(SESSION_ID)
            no_recompute = patched.integrity_status == "trusted" and scan["recompute_ok"]
        finally:
            I1RuleEngine.evaluate = original
        probe_count += 1

        crash_root = root / "crash"
        DecisionLedger(crash_root, clock=lambda: CLOCK).append_decision(_machine(), _provenance())
        try:
            DecisionLedger(crash_root, clock=lambda: CLOCK, failure_injector=_fail_at("events_append")).persist_action_applied(
                SESSION_ID, DECISION_ID, source_provenance=_provenance()
            )
            recovery_ok = False
        except M1IntError as exc:
            recovery_ok = exc.code == "persistence_failure"
        recovered = DecisionLedger(crash_root, clock=lambda: CLOCK).replay_session(SESSION_ID)
        recovery_ok = recovery_ok and recovered.views[0].outcome == "applied" and len(
            [item for item in recovered.events if item.event_type == "action_applied"]
        ) == 1
        probe_count += 1

        copied = root / "copy"
        shutil.copytree(root / SESSION_ID, copied / SESSION_ID)
        copied_result = DecisionLedger(copied, clock=lambda: "2099-01-01T00:00:00Z").replay_session(SESSION_ID)
        deterministic = deterministic and copied_result.replay_fingerprint == applied.replay_fingerprint
        probe_count += 1

        p4c_ledger = DecisionLedger(root / "p4c", clock=lambda: CLOCK)
        p4c_ledger.append_decision(_machine(), _provenance())
        p4c_ledger.persist_retry_scope_started(SESSION_ID, retry_scope_id=SCOPE, source_provenance=_provenance())
        p4c_result = p4c_ledger.replay_session(SESSION_ID)
        p4c_runtime_ok = (
            len(p4c_result.p4c_facts) == 1
            and p4c_result.p4c_facts[0].event_type == "retry_scope_started"
            and not hasattr(p4c_result, "retry_scope_state")
        )
        extra = build_int_ledger_event(
            event_seq=p4c_result.last_event_seq + 1,
            event_type="action_applied",
            session_id=SESSION_ID,
            occurred_at_utc=CLOCK,
            decision_id="m1-decision-" + ("ff" * 32),
            outcome="applied",
        )
        with (root / "p4c" / SESSION_ID / "int" / "decision-events.jsonl").open("ab") as handle:
            handle.write(_event_line(extra))
        try:
            DecisionLedger(root / "p4c", clock=lambda: CLOCK).replay_session(SESSION_ID)
            xref = False
        except M1IntError as exc:
            xref = xref and exc.code == "dangling_decision_reference"
        probe_count += 2

    p4a = run_m1_p4a_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p4ba = run_m1_p4b_a_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p4bb = run_m1_p4b_b_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p4bc = run_m1_p4b_c_acceptance(software_commit_sha=software_commit_sha, expected_head_sha=software_commit_sha)
    p3_ok = EXPECTED_P3_SOURCE == "2f4f88cc69fbdfb1e129d347025695334542eb9e"
    p3_digest_ok = EXPECTED_P3_DIGEST == "fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f"
    p2_ok = EXPECTED_P2_GOLDEN == "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"
    try:
        d3_ok = _git("rev-parse", "d3-v1.0.0") == EXPECTED_D3_TAG_OBJECT
        d3_ok = d3_ok and _git("rev-parse", "d3-v1.0.0^{commit}") == EXPECTED_D3_TAG_TARGET
    except (subprocess.CalledProcessError, FileNotFoundError):
        d3_ok = False

    gates = {
        "replay_basic": _gate("replay_basic", replay_basic),
        "deterministic_fold": _gate("deterministic_fold", deterministic),
        "machine_decision_immutable": _gate("machine_decision_immutable", immutable),
        "event_ordering": _gate("event_ordering", ordering),
        "event_seq_integrity": _gate("event_seq_integrity", seq_ok),
        "decision_event_cross_reference": _gate("decision_event_cross_reference", xref),
        "lifecycle_consistency": _gate("lifecycle_consistency", lifecycle),
        "partial_tail_fail_closed": _gate("partial_tail_fail_closed", partial_ok),
        "corruption_fail_closed": _gate("corruption_fail_closed", corruption_ok),
        "provenance": _gate("provenance", provenance_ok),
        "snapshot_consistency": _gate("snapshot_consistency", snapshot_ok),
        "replay_no_recompute": _gate("replay_no_recompute", no_recompute),
        "oracle_isolation": _gate("oracle_isolation", scan["oracle_ok"], oracle_scanner_self_test=scan["oracle_scanner_self_test"]),
        "manifest_reconciliation": _gate("manifest_reconciliation", reconcile_ok),
        "manifest_final_integrity": _gate("manifest_final_integrity", final_manifest_ok),
        "action_applied_non_invention": _gate("action_applied_non_invention", non_invention),
        "business_read_only_replay": _gate("business_read_only_replay", business_ro),
        "recovery_boundary": _gate("recovery_boundary", recovery_ok),
        "p4bb_regression": _gate("p4bb_regression", bool(p4bb.get("acceptance"))),
        "p4bc_regression": _gate("p4bc_regression", bool(p4bc.get("acceptance"))),
        "p4c_boundary": _gate("p4c_boundary", scan["p4c_ok"] and p4c_runtime_ok),
        "report_boundary": _gate("report_boundary", scan["report_ok"] and report_ok),
        "hardware_boundary": _gate("hardware_boundary", scan["hardware_ok"]),
        "exact_head": _gate("exact_head", exact_head, software_commit_sha=software_commit_sha),
    }
    failed = [name for name, payload in gates.items() if not payload["passed"]]
    extra_ok = all((p4a.get("acceptance"), p4ba.get("acceptance"), p3_ok and p3_digest_ok, p2_ok, d3_ok, probe_count >= 18))
    acceptance = not failed and extra_ok and set(REQUIRED_GATES) <= set(gates)
    return {
        "acceptance": acceptance,
        "acceptance_version": ACCEPTANCE_VERSION,
        "architecture_base_sha": ARCHITECTURE_BASE_SHA,
        "failed_gates": failed,
        "gates": gates,
        "p4a_acceptance": bool(p4a.get("acceptance")),
        "p4ba_acceptance": bool(p4ba.get("acceptance")),
        "p4b_c_merge_sha": P4B_C_MERGE_SHA,
        "probe_count": probe_count,
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4B-D",
        "frozen_assets_unchanged": p3_ok and p3_digest_ok and p2_ok and d3_ok,
    }
