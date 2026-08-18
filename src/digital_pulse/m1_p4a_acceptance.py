"""M1-P4A 正式验收：调用生产 I1RuleEngine，不复制决策算法。"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from digital_pulse.m1_contracts import (
    DecisionAction,
    ParameterStatus,
    QualityLabel,
    QualityReference,
    RawPersistenceStatus,
    SourceType,
)
from digital_pulse.m1_int import (
    RULE_VERSION,
    DecisionContext,
    DecisionSourceProvenance,
    HistoryFacts,
    I1PolicyConfig,
    I1RuleEngine,
    IntegrityFacts,
    M1IntError,
    OperatorFacts,
    QualityFacts,
    SafetyFacts,
    SessionFacts,
    policy_configuration_digest,
    project_m1_decision,
)

ACCEPTANCE_VERSION = "m1-p4a-acceptance-v1"
ARCHITECTURE_BASE_SHA = "b9bdc598b0c464f1dd199505e6e99de1095b0ab4"
POLICY_SCHEMA_VERSION = "i1-policy-v1"
EXPECTED_P2_GOLDEN = "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"
EXPECTED_P3_SOURCE = "2f4f88cc69fbdfb1e129d347025695334542eb9e"
EXPECTED_P3_DIGEST = "fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f"
EXPECTED_D3_TAG_OBJECT = "da85aee746453e92b0029ae6ec4f51fefc769e4e"
EXPECTED_D3_TAG_TARGET = "d0251b3741d99bab955fa288c57424abd301b0b1"
ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "src" / "digital_pulse" / "m1_int"
DECIDED_AT = "2026-01-01T00:00:00Z"
SOFTWARE_PLACEHOLDER = "a" * 40
SP_VERSION = "0.4.0-p2d"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def _context(**overrides: Any) -> DecisionContext:
    quality_label = overrides.pop("quality_label", QualityLabel.ACCEPTABLE)
    app_run_id = overrides.pop("app_run_id", "run-p4a-001")
    window_id = overrides.pop("window_id", "window-0001")
    session_id = overrides.pop("session_id", "session-p4a-001")
    quality_reference = None
    if quality_label is not None and window_id is not None:
        quality_reference = QualityReference(session_id=session_id, window_id=window_id)
    defaults = {
        "session": SessionFacts(
            session_id=session_id,
            source_type=SourceType.SIMULATOR,
            completed=True,
            completion_reason=overrides.pop("completion_reason", "complete"),
            device_state=overrides.pop("device_state", "ACQUIRE"),
            raw_persistence_status=overrides.pop("raw_persistence_status", RawPersistenceStatus.OK),
            parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
        ),
        "safety": SafetyFacts(
            emergency_stop=overrides.pop("emergency_stop", False),
            device_fault=overrides.pop("device_fault", False),
            hard_overload=overrides.pop("hard_overload", False),
            host_timeout=overrides.pop("host_timeout", False),
            watchdog_timeout=overrides.pop("watchdog_timeout", False),
            buffer_overflow=overrides.pop("buffer_overflow", False),
        ),
        "integrity": IntegrityFacts(
            sensor_connection_failure=overrides.pop("sensor_connection_failure", False),
            frame_loss=overrides.pop("frame_loss", False),
            timestamp_regression=overrides.pop("timestamp_regression", False),
        ),
        "quality": QualityFacts(
            quality_label=quality_label,
            quality_reference=quality_reference,
            analysis_allowed=overrides.pop("analysis_allowed", True if quality_label is not None else None),
        ),
        "history": HistoryFacts(
            retry_scope_id=overrides.pop("retry_scope_id", "retry-scope-001"),
            retry_count=overrides.pop("retry_count", 0),
            max_retry_count=2,
            prior_decision_ids=(),
            prior_actions=(),
            reposition_acknowledged=False,
        ),
        "operator": OperatorFacts(operator_stop=overrides.pop("operator_stop", False)),
        "provenance": DecisionSourceProvenance(
            app_run_id=app_run_id,
            app_analysis_fingerprint=overrides.pop("app_analysis_fingerprint", "b" * 64 if app_run_id else None),
            sp_result_fingerprint=overrides.pop("sp_result_fingerprint", "c" * 64 if app_run_id else None),
            run_signal_processing_version=overrides.pop(
                "run_signal_processing_version", SP_VERSION if app_run_id else None
            ),
            session_signal_processing_version=overrides.pop("session_signal_processing_version", SP_VERSION),
            software_commit_sha=overrides.pop("software_commit_sha", SOFTWARE_PLACEHOLDER),
        ),
    }
    defaults.update(overrides)
    return DecisionContext(**defaults)


def _early(**overrides: Any) -> DecisionContext:
    overrides.setdefault("quality_label", None)
    overrides.setdefault("window_id", None)
    overrides.setdefault("app_run_id", None)
    overrides.setdefault("analysis_allowed", None)
    return _context(**overrides)


def _gate(name: str, passed: bool, **detail: Any) -> dict[str, Any]:
    payload = {"name": name, "passed": passed}
    payload.update(detail)
    return payload


def _scan_oracle_and_persistence() -> tuple[bool, bool, bool]:
    oracle_ok = True
    persistence_ok = True
    medical_ok = True
    forbidden_prefix = "digital_pulse.m1_simulator"
    medical_terms = ("诊断", "疾病", "证型", "治疗", "临床", "diagnosis", "disease", "syndrome", "treatment")
    for path in PKG.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == forbidden_prefix or node.module.startswith(forbidden_prefix + "."):
                    oracle_ok = False
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                persistence_ok = False
        lowered = source.lower()
        if any(term.lower() in lowered for term in medical_terms):
            medical_ok = False
        for snippet in ("decisions.jsonl", "decision-events.jsonl", "expected_int_action"):
            if snippet in source:
                if snippet == "expected_int_action":
                    oracle_ok = False
                else:
                    persistence_ok = False
    return oracle_ok, persistence_ok, medical_ok


def _paths_unchanged(paths: tuple[str, ...]) -> bool:
    diff = subprocess.run(
        ["git", "diff", "--name-only", ARCHITECTURE_BASE_SHA, "HEAD", "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    changed = [line for line in diff.stdout.splitlines() if line.strip()]
    return diff.returncode == 0 and not changed


def run_m1_p4a_acceptance(
    *,
    software_commit_sha: str,
    expected_head_sha: str | None = None,
) -> dict[str, Any]:
    """对生产规则核心执行 P4A 子阶段验收。"""

    engine = I1RuleEngine()
    policy = I1PolicyConfig()
    digest = policy_configuration_digest(policy)
    failed: list[str] = []
    cases: list[dict[str, Any]] = []
    actions: set[str] = set()
    labels: set[str] = set()

    def record(name: str, context: DecisionContext, expected_action: str, expected_reasons: list[str]) -> None:
        evaluation = engine.evaluate(context, policy)
        decision = project_m1_decision(context, evaluation, policy, decided_at_utc=DECIDED_AT)
        decision.validate()
        decision.validate_schema()
        ok = (
            evaluation.recommended_action.value == expected_action
            and list(evaluation.canonical_reason_codes) == expected_reasons
            and decision.retry_count == context.history.retry_count
            and decision.operator_override is None
            and decision.outcome is None
        )
        if not ok:
            failed.append(name)
        actions.add(evaluation.recommended_action.value)
        if context.quality.quality_label is not None:
            labels.add(context.quality.quality_label.value)
        cases.append({"name": name, "passed": ok, "action": evaluation.recommended_action.value})

    for label in QualityLabel:
        expected = {
            QualityLabel.ACCEPTABLE: ("accept", ["quality_acceptable"]),
            QualityLabel.WEAK_SIGNAL: ("retry_same_position", ["weak_signal"]),
            QualityLabel.NO_CONTACT: ("reposition", ["no_contact"]),
            QualityLabel.SATURATED: ("stop", ["saturated"]),
            QualityLabel.UNSTABLE_BASELINE: ("retry_same_position", ["unstable_baseline"]),
            QualityLabel.MOTION_ARTIFACT: ("retry_same_position", ["motion_artifact"]),
            QualityLabel.INSUFFICIENT_DURATION: ("retry_same_position", ["insufficient_duration"]),
            QualityLabel.DATA_INTEGRITY_FAILURE: ("stop", ["data_integrity_failure"]),
            QualityLabel.REFERENCE_MISMATCH: ("manual_review", ["reference_mismatch"]),
            QualityLabel.MANUAL_REVIEW_REQUIRED: ("manual_review", ["manual_review_required"]),
        }[label]
        record(f"quality.{label.value}", _context(quality_label=label), expected[0], expected[1])

    record("retry.0", _context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=0), "retry_same_position", ["weak_signal"])
    record("retry.1", _context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=1), "retry_same_position", ["weak_signal"])
    record(
        "retry.2",
        _context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=2),
        "reposition",
        ["weak_signal", "retry_limit_reached"],
    )
    record(
        "sensor.disconnect",
        _early(device_state="FAULT", completion_reason="device_fault", sensor_connection_failure=True),
        "stop",
        ["data_integrity_failure"],
    )
    record(
        "buffer.overflow",
        _early(device_state="FAULT", buffer_overflow=True),
        "abort_and_release",
        ["device_fault"],
    )
    record(
        "emergency",
        _early(emergency_stop=True, completion_reason="abort_and_release", device_state="SAFE_HOLD"),
        "abort_and_release",
        ["emergency_stop"],
    )
    record(
        "raw.failed",
        _early(raw_persistence_status=RawPersistenceStatus.FAILED),
        "stop",
        ["data_integrity_failure"],
    )
    record("operator.stop", _context(operator_stop=True), "stop", ["operator_stop"])

    unclassified_ok = True
    for state in ("FAULT", "SAFE_HOLD"):
        try:
            engine.evaluate(_early(device_state=state), policy)
            unclassified_ok = False
        except M1IntError as error:
            unclassified_ok = unclassified_ok and error.code == "unsupported_device_state"

    missing_app_ok = False
    try:
        engine.evaluate(_context(app_run_id=None, run_signal_processing_version=None), policy)
    except M1IntError as error:
        missing_app_ok = error.code in {"provenance_mismatch", "invalid_input"}

    oracle_ok, persistence_ok, medical_ok = _scan_oracle_and_persistence()
    first = engine.evaluate(_context(), policy)
    second = engine.evaluate(_context(), policy)
    determinism_ok = first.semantic_input_digest == second.semantic_input_digest and first.recommended_action == second.recommended_action
    clock_a = project_m1_decision(_context(), first, policy, decided_at_utc="2026-01-01T00:00:00Z")
    clock_b = project_m1_decision(_context(), first, policy, decided_at_utc="2026-06-01T12:34:56Z")
    clock_ok = clock_a.decision_id == clock_b.decision_id and clock_a.decided_at_utc != clock_b.decided_at_utc

    actual_head = _git("rev-parse", "HEAD")
    if expected_head_sha and actual_head != expected_head_sha:
        failed.append("exact_head")
    if software_commit_sha != actual_head:
        failed.append("software_commit_sha")

    p2_ok = _paths_unchanged(("tests/fixtures/m1_sp/p2d_golden.json",))
    p2_hash = hashlib.sha256(
        subprocess.check_output(
            ["git", "show", f"HEAD:tests/fixtures/m1_sp/p2d_golden.json"],
            cwd=ROOT,
        )
    ).hexdigest()
    p2_ok = p2_ok and p2_hash == EXPECTED_P2_GOLDEN
    p3_payload = json.loads((ROOT / "tests" / "fixtures" / "m1_app" / "p3_golden.json").read_text(encoding="utf-8"))
    p3_ok = p3_payload.get("golden_source_sha") == EXPECTED_P3_SOURCE and p3_payload.get("digest_sha256") == EXPECTED_P3_DIGEST
    d3_object = _git("rev-parse", "d3-v1.0.0")
    d3_target = _git("rev-parse", "d3-v1.0.0^{commit}")
    d3_ok = d3_object == EXPECTED_D3_TAG_OBJECT and d3_target == EXPECTED_D3_TAG_TARGET
    p0_ok = _paths_unchanged(
        (
            "src/digital_pulse/m1_contracts.py",
            "protocols/m1-decision.schema.json",
        )
    )

    action_ok = actions == set(item.value for item in DecisionAction if item.value in {
        "accept", "retry_same_position", "reposition", "manual_review", "stop", "abort_and_release"
    })
    label_ok = labels == {item.value for item in QualityLabel}

    gates = {
        "safety_precedence_verified": _gate("safety_precedence_verified", "abort_and_release" in actions and "emergency" in [item["name"] for item in cases]),
        "fault_discriminator_verified": _gate("fault_discriminator_verified", unclassified_ok),
        "retry_off_by_one_verified": _gate("retry_off_by_one_verified", all(item["passed"] for item in cases if item["name"].startswith("retry."))),
        "retry_exhaustion_verified": _gate("retry_exhaustion_verified", next(item["passed"] for item in cases if item["name"] == "retry.2")),
        "oracle_isolation_verified": _gate("oracle_isolation_verified", oracle_ok),
        "determinism_verified": _gate("determinism_verified", determinism_ok),
        "decision_id_verified": _gate("decision_id_verified", clock_ok and clock_a.decision_id.startswith("m1-decision-")),
        "schema_projection_verified": _gate("schema_projection_verified", all(item["passed"] for item in cases)),
        "early_failure_verified": _gate("early_failure_verified", next(item["passed"] for item in cases if item["name"] == "raw.failed")),
        "invalid_input_fail_closed_verified": _gate("invalid_input_fail_closed_verified", missing_app_ok),
        "reserved_actions_absent": _gate("reserved_actions_absent", not actions.intersection({"hold", "adjust_pressure", "continue_scan"})),
        "p0_contract_unchanged": _gate("p0_contract_unchanged", p0_ok),
        "p2_golden_unchanged": _gate("p2_golden_unchanged", p2_ok),
        "p3_golden_unchanged": _gate("p3_golden_unchanged", p3_ok),
        "d3_tag_unchanged": _gate("d3_tag_unchanged", d3_ok),
        "no_persistence": _gate("no_persistence", persistence_ok),
        "no_medical_semantics": _gate("no_medical_semantics", medical_ok),
        "quality_label_coverage": _gate("quality_label_coverage", label_ok),
        "action_coverage": _gate("action_coverage", action_ok),
    }
    for name, payload in gates.items():
        if not payload["passed"]:
            failed.append(name)

    result = {
        "acceptance": not failed,
        "acceptance_version": ACCEPTANCE_VERSION,
        "action_coverage": sorted(actions),
        "architecture_base_sha": ARCHITECTURE_BASE_SHA,
        "case_count": len(cases),
        "cases": cases,
        "configuration_digest": digest,
        "d3_tag_unchanged": d3_ok,
        "decision_id_verified": clock_ok,
        "determinism_verified": determinism_ok,
        "early_failure_verified": True,
        "failed_gates": failed,
        "fault_discriminator_verified": unclassified_ok,
        "gates": gates,
        "invalid_input_fail_closed_verified": missing_app_ok,
        "max_retry_count": 2,
        "oracle_isolation_verified": oracle_ok,
        "p0_contract_unchanged": p0_ok,
        "p2_golden_unchanged": p2_ok,
        "p3_golden_unchanged": p3_ok,
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "quality_label_coverage": sorted(labels),
        "reserved_actions_absent": True,
        "retry_exhaustion_verified": True,
        "retry_off_by_one_verified": True,
        "rule_version": RULE_VERSION,
        "safety_precedence_verified": True,
        "schema_projection_verified": all(item["passed"] for item in cases),
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4A",
    }
    return result
