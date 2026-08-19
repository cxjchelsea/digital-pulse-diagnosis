"""M1-P4A 正式验收：调用生产 I1RuleEngine，不复制决策算法。"""

from __future__ import annotations

import ast
from dataclasses import replace
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


def _d3_tag_unchanged() -> bool:
    """浅克隆可能没有标签名；失败关闭为 False，不把 git 异常冒成验收崩溃。"""

    try:
        d3_object = _git("rev-parse", "d3-v1.0.0")
        d3_target = _git("rev-parse", "d3-v1.0.0^{commit}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return d3_object == EXPECTED_D3_TAG_OBJECT and d3_target == EXPECTED_D3_TAG_TARGET


def _context(**overrides: Any) -> DecisionContext:
    quality_label = overrides.pop("quality_label", QualityLabel.ACCEPTABLE)
    app_run_id = overrides.pop("app_run_id", "run-p4a-001")
    window_id = overrides.pop("window_id", "window-0001")
    session_id = overrides.pop("session_id", "session-p4a-001")
    quality_reference = None
    if quality_label is not None and window_id is not None:
        quality_reference = QualityReference(session_id=session_id, window_id=window_id)
    retry_count = overrides.pop("retry_count", 0)
    prior_actions = tuple("retry_same_position" for _ in range(retry_count))
    prior_decision_ids = tuple(f"m1-decision-{'a' * 62}{index:02x}" for index in range(retry_count))
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
            retry_count=retry_count,
            max_retry_count=2,
            prior_decision_ids=prior_decision_ids,
            prior_actions=prior_actions,
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


def _scan_source_boundaries(source: str, *, filename: str = "<memory>") -> tuple[bool, bool]:
    """扫描生产 INT 源码的 oracle 依赖与持久化能力；用于真实源码和自检探针。"""

    oracle_ok = True
    persistence_ok = True
    forbidden_prefix = "digital_pulse.m1_simulator"
    persistence_module_roots = {"tempfile", "shutil", "fcntl", "msvcrt", "filelock", "portalocker"}
    persistence_call_attrs = {
        "open",
        "write_text",
        "write_bytes",
        "fsync",
        "fdatasync",
        "replace",
        "rename",
        "remove",
        "unlink",
        "mkdir",
        "makedirs",
        "rmdir",
        "copy",
        "copy2",
        "copyfile",
        "move",
        "rmtree",
        "mkstemp",
        "mkdtemp",
        "NamedTemporaryFile",
        "TemporaryFile",
    }
    oracle_snippets = (
        "expected_int_action",
        "expected_quality_label",
        "expected.json",
        "scenario.json",
        "scenario_id",
    )
    persistence_snippets = (
        "decisions.jsonl",
        "decision-events.jsonl",
        "int/manifest.json",
        "int/reports/",
        "os.open",
        "os.fsync",
        "os.fdatasync",
        "Path.write_text",
        "Path.write_bytes",
        "tempfile",
        "shutil",
        "fcntl",
        "msvcrt",
        "filelock",
        "portalocker",
    )

    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == forbidden_prefix or alias.name.startswith(forbidden_prefix + "."):
                    oracle_ok = False
                if alias.name.split(".", 1)[0] in persistence_module_roots:
                    persistence_ok = False
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == forbidden_prefix or module.startswith(forbidden_prefix + "."):
                oracle_ok = False
            if module == "digital_pulse" and any(alias.name == "m1_simulator" for alias in node.names):
                oracle_ok = False
            if module.split(".", 1)[0] in persistence_module_roots:
                persistence_ok = False
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id == "open":
                    persistence_ok = False
                if func.id == "__import__":
                    oracle_ok = False
            elif isinstance(func, ast.Attribute):
                if func.attr in persistence_call_attrs:
                    persistence_ok = False
                if func.attr == "import_module":
                    oracle_ok = False

    for snippet in oracle_snippets:
        if snippet in source:
            oracle_ok = False
    for snippet in persistence_snippets:
        if snippet in source:
            persistence_ok = False
    return oracle_ok, persistence_ok


def _scan_oracle_and_persistence() -> tuple[bool, bool, bool, bool, bool]:
    oracle_ok = True
    persistence_ok = True
    medical_ok = True
    medical_terms = ("诊断", "疾病", "证型", "治疗", "临床", "diagnosis", "disease", "syndrome", "treatment")
    for path in PKG.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        source_oracle_ok, source_persistence_ok = _scan_source_boundaries(source, filename=str(path))
        oracle_ok = oracle_ok and source_oracle_ok
        persistence_ok = persistence_ok and source_persistence_ok
        lowered = source.lower()
        if any(term.lower() in lowered for term in medical_terms):
            medical_ok = False

    oracle_probe_sources = (
        "import digital_pulse.m1_simulator\n",
        "from digital_pulse import m1_simulator\n",
        "from digital_pulse.m1_simulator import ScenarioDefinition\n",
        "value = 'expected_int_action'\n",
        "value = 'expected_quality_label'\n",
        "value = 'expected.json'\n",
        "value = 'scenario.json'\n",
        "if scenario_id == 'x':\n    pass\n",
    )
    persistence_probe_sources = (
        "open('x', 'w')\n",
        "from pathlib import Path\nPath('x').write_text('x')\n",
        "from pathlib import Path\nPath('x').write_bytes(b'x')\n",
        "import os\nos.open('x', os.O_WRONLY)\n",
        "import os\nos.fsync(1)\n",
        "import tempfile\n",
        "import shutil\nshutil.copy('a', 'b')\n",
        "import fcntl\n",
        "import msvcrt\n",
    )
    oracle_scanner_self_test = all(not _scan_source_boundaries(source)[0] for source in oracle_probe_sources)
    persistence_scanner_self_test = all(
        not _scan_source_boundaries(source)[1] for source in persistence_probe_sources
    )
    return (
        oracle_ok and oracle_scanner_self_test,
        persistence_ok and persistence_scanner_self_test,
        medical_ok,
        oracle_scanner_self_test,
        persistence_scanner_self_test,
    )


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
    for exhausted_label in (
        QualityLabel.UNSTABLE_BASELINE,
        QualityLabel.MOTION_ARTIFACT,
        QualityLabel.INSUFFICIENT_DURATION,
    ):
        record(
            f"retry.2.{exhausted_label.value}",
            _context(quality_label=exhausted_label, retry_count=2),
            "reposition",
            [exhausted_label.value, "retry_limit_reached"],
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

    def _fail_closed(context: DecisionContext, expected_code: str) -> bool:
        try:
            engine.evaluate(context, policy)
        except M1IntError as error:
            return error.code == expected_code
        return False

    sensor_stop = engine.evaluate(
        _early(device_state="FAULT", completion_reason="device_fault", sensor_connection_failure=True),
        policy,
    )
    buffer_abort = engine.evaluate(_early(device_state="FAULT", buffer_overflow=True), policy)
    completion_abort = engine.evaluate(
        _early(device_state="FAULT", completion_reason="device_fault"),
        policy,
    )
    explicit_fault_abort = engine.evaluate(
        _early(device_state="FAULT", completion_reason="complete", device_fault=True),
        policy,
    )
    fault_ok = (
        unclassified_ok
        and sensor_stop.recommended_action.value == "stop"
        and list(sensor_stop.canonical_reason_codes) == ["data_integrity_failure"]
        and buffer_abort.recommended_action.value == "abort_and_release"
        and list(buffer_abort.canonical_reason_codes) == ["device_fault"]
        and completion_abort.recommended_action.value == "abort_and_release"
        and list(completion_abort.canonical_reason_codes) == ["device_fault"]
        and explicit_fault_abort.recommended_action.value == "abort_and_release"
        and list(explicit_fault_abort.canonical_reason_codes) == ["device_fault"]
    )

    safety_conflicts = [
        (
            _context(quality_label=QualityLabel.ACCEPTABLE, emergency_stop=True),
            "abort_and_release",
            ["emergency_stop"],
        ),
        (
            _early(
                device_state="FAULT",
                completion_reason="device_fault",
                device_fault=True,
                quality_label=QualityLabel.WEAK_SIGNAL,
                window_id="window-0001",
                analysis_allowed=True,
                app_run_id="run-p4a-001",
            ),
            "abort_and_release",
            ["device_fault"],
        ),
        (
            _context(quality_label=QualityLabel.ACCEPTABLE, operator_stop=True, emergency_stop=True),
            "abort_and_release",
            ["emergency_stop"],
        ),
        (
            _early(
                raw_persistence_status=RawPersistenceStatus.FAILED,
                emergency_stop=True,
                completion_reason="abort_and_release",
                device_state="SAFE_HOLD",
            ),
            "abort_and_release",
            ["emergency_stop"],
        ),
        (
            _early(device_state="FAULT", sensor_connection_failure=True, hard_overload=True),
            "abort_and_release",
            ["hard_overload"],
        ),
    ]
    safety_ok = True
    for context, expected_action, expected_reasons in safety_conflicts:
        evaluation = engine.evaluate(context, policy)
        safety_ok = safety_ok and evaluation.recommended_action.value == expected_action
        safety_ok = safety_ok and list(evaluation.canonical_reason_codes) == expected_reasons

    retry_exhaustion_names = {
        "retry.2",
        "retry.2.unstable_baseline",
        "retry.2.motion_artifact",
        "retry.2.insufficient_duration",
    }
    retry_exhaustion_cases = {item["name"]: item["passed"] for item in cases if item["name"] in retry_exhaustion_names}
    retry_exhaustion_ok = (
        set(retry_exhaustion_cases) == retry_exhaustion_names
        and all(retry_exhaustion_cases.values())
    )
    retry_off_by_one_names = {"retry.0", "retry.1", "retry.2"}
    retry_off_by_one_cases = {item["name"]: item["passed"] for item in cases if item["name"] in retry_off_by_one_names}
    retry_off_by_one_ok = (
        set(retry_off_by_one_cases) == retry_off_by_one_names
        and all(retry_off_by_one_cases.values())
    )
    early_failure_names = {"sensor.disconnect", "buffer.overflow", "emergency", "raw.failed"}
    early_failure_cases = {item["name"]: item["passed"] for item in cases if item["name"] in early_failure_names}
    early_failure_ok = (
        set(early_failure_cases) == early_failure_names
        and all(early_failure_cases.values())
    )

    missing_app_ok = _fail_closed(
        _context(app_run_id=None, run_signal_processing_version=None),
        "provenance_mismatch",
    ) or _fail_closed(
        _context(app_run_id=None, run_signal_processing_version=None),
        "invalid_input",
    )
    bad_history_base = _context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=2)
    bad_history_context = replace(
        bad_history_base,
        history=HistoryFacts(
            retry_scope_id=bad_history_base.history.retry_scope_id,
            retry_count=2,
            max_retry_count=2,
            prior_decision_ids=(),
            prior_actions=(),
            reposition_acknowledged=False,
        ),
    )
    bad_action_base = _context(quality_label=QualityLabel.WEAK_SIGNAL, retry_count=0)
    bad_action_context = replace(
        bad_action_base,
        history=HistoryFacts(
            retry_scope_id=bad_action_base.history.retry_scope_id,
            retry_count=0,
            max_retry_count=2,
            prior_decision_ids=("m1-decision-" + "a" * 64,),
            prior_actions=("banana",),
            reposition_acknowledged=False,
        ),
    )
    invalid_ok = (
        missing_app_ok
        and _fail_closed(_context(retry_count=3), "invalid_retry_state")
        and _fail_closed(bad_history_context, "invalid_retry_state")
        and _fail_closed(bad_action_context, "invalid_retry_state")
        and _fail_closed(_context(device_state="BANANA"), "unsupported_device_state")
        and _fail_closed(_context(analysis_allowed=None), "invalid_input")
        and _fail_closed(_context(analysis_allowed=False), "invalid_input")
        and _fail_closed(_context(device_fault=True, device_state="ACQUIRE"), "invalid_input")
        and _fail_closed(_context(buffer_overflow=True, device_state="ACQUIRE"), "invalid_input")
    )

    (
        oracle_ok,
        persistence_ok,
        medical_ok,
        oracle_scanner_self_test,
        persistence_scanner_self_test,
    ) = _scan_oracle_and_persistence()

    base_context = _context()
    first = engine.evaluate(base_context, policy)
    second = engine.evaluate(base_context, policy)
    determinism_ok = (
        first.semantic_input_digest == second.semantic_input_digest
        and first.recommended_action == second.recommended_action
        and first.canonical_reason_codes == second.canonical_reason_codes
    )
    clock_a = project_m1_decision(base_context, first, policy, decided_at_utc="2026-01-01T00:00:00Z")
    clock_b = project_m1_decision(base_context, first, policy, decided_at_utc="2026-06-01T12:34:56Z")
    clock_ok = clock_a.decision_id == clock_b.decision_id and clock_a.decided_at_utc != clock_b.decided_at_utc

    cross_context_bind_ok = False
    try:
        project_m1_decision(
            _early(emergency_stop=True, completion_reason="abort_and_release", device_state="SAFE_HOLD"),
            first,
            policy,
            decided_at_utc=DECIDED_AT,
        )
    except M1IntError as error:
        cross_context_bind_ok = error.code == "invalid_input"

    forged_evaluation_ok = False
    try:
        project_m1_decision(
            base_context,
            replace(first, matched_rule_id="forged.accept"),
            policy,
            decided_at_utc=DECIDED_AT,
        )
    except M1IntError as error:
        forged_evaluation_ok = error.code == "invalid_input"

    rule_bind_ok = False
    try:
        project_m1_decision(
            base_context,
            replace(first, rule_version="i1-pre-forged"),
            policy,
            decided_at_utc=DECIDED_AT,
        )
    except M1IntError as error:
        rule_bind_ok = error.code == "invalid_input"

    policy_bind_ok = False
    try:
        project_m1_decision(
            base_context,
            first,
            I1PolicyConfig(priority_table_version="i1-priority-forged"),
            decided_at_utc=DECIDED_AT,
        )
    except M1IntError as error:
        policy_bind_ok = error.code == "version_mismatch"

    projection_binding_ok = cross_context_bind_ok and forged_evaluation_ok and rule_bind_ok and policy_bind_ok

    weak_context = _context(quality_label=QualityLabel.WEAK_SIGNAL)
    weak_eval = engine.evaluate(weak_context, policy)
    weak_decision = project_m1_decision(weak_context, weak_eval, policy, decided_at_utc=DECIDED_AT)
    output_bind_ok = clock_a.decision_id != weak_decision.decision_id

    parameter_context = replace(
        base_context,
        session=replace(base_context.session, parameter_status=ParameterStatus.SYNTHETIC_ONLY),
    )
    parameter_decision = project_m1_decision(
        parameter_context,
        engine.evaluate(parameter_context, policy),
        policy,
        decided_at_utc=DECIDED_AT,
    )
    parameter_identity_ok = clock_a.decision_id != parameter_decision.decision_id

    sp_context = replace(
        base_context,
        provenance=replace(
            base_context.provenance,
            run_signal_processing_version="9.9.9",
            session_signal_processing_version="9.9.9",
        ),
    )
    sp_decision = project_m1_decision(
        sp_context,
        engine.evaluate(sp_context, policy),
        policy,
        decided_at_utc=DECIDED_AT,
    )
    sp_identity_ok = clock_a.decision_id != sp_decision.decision_id

    analysis_true_context = _context(quality_label=QualityLabel.WEAK_SIGNAL, analysis_allowed=True)
    analysis_false_context = replace(
        analysis_true_context,
        quality=replace(analysis_true_context.quality, analysis_allowed=False),
    )
    analysis_true_decision = project_m1_decision(
        analysis_true_context,
        engine.evaluate(analysis_true_context, policy),
        policy,
        decided_at_utc=DECIDED_AT,
    )
    analysis_false_decision = project_m1_decision(
        analysis_false_context,
        engine.evaluate(analysis_false_context, policy),
        policy,
        decided_at_utc=DECIDED_AT,
    )
    analysis_allowed_identity_ok = analysis_true_decision.decision_id != analysis_false_decision.decision_id

    app_run_context = replace(
        base_context,
        provenance=replace(base_context.provenance, app_run_id="run-p4a-002"),
    )
    app_run_decision = project_m1_decision(
        app_run_context,
        engine.evaluate(app_run_context, policy),
        policy,
        decided_at_utc=DECIDED_AT,
    )
    app_run_identity_ok = clock_a.decision_id != app_run_decision.decision_id

    app_fingerprint_context = replace(
        base_context,
        provenance=replace(base_context.provenance, app_analysis_fingerprint="d" * 64),
    )
    app_fingerprint_decision = project_m1_decision(
        app_fingerprint_context,
        engine.evaluate(app_fingerprint_context, policy),
        policy,
        decided_at_utc=DECIDED_AT,
    )
    app_fingerprint_identity_ok = clock_a.decision_id != app_fingerprint_decision.decision_id

    software_sha_context = replace(
        base_context,
        provenance=replace(base_context.provenance, software_commit_sha="f" * 40),
    )
    software_sha_decision = project_m1_decision(
        software_sha_context,
        engine.evaluate(software_sha_context, policy),
        policy,
        decided_at_utc=DECIDED_AT,
    )
    software_sha_excluded_ok = clock_a.decision_id == software_sha_decision.decision_id

    decision_id_ok = (
        clock_ok
        and projection_binding_ok
        and output_bind_ok
        and parameter_identity_ok
        and sp_identity_ok
        and analysis_allowed_identity_ok
        and app_run_identity_ok
        and app_fingerprint_identity_ok
        and software_sha_excluded_ok
        and clock_a.decision_id.startswith("m1-decision-")
        and len(clock_a.decision_id) == len("m1-decision-") + 64
    )

    actual_head = _git("rev-parse", "HEAD")
    if expected_head_sha and actual_head != expected_head_sha:
        failed.append("exact_head")
    if software_commit_sha != actual_head:
        failed.append("software_commit_sha")

    p2_ok = _paths_unchanged(("tests/fixtures/m1_sp/p2d_golden.json",))
    p2_hash = hashlib.sha256(
        subprocess.check_output(
            ["git", "show", "HEAD:tests/fixtures/m1_sp/p2d_golden.json"],
            cwd=ROOT,
        )
    ).hexdigest()
    p2_ok = p2_ok and p2_hash == EXPECTED_P2_GOLDEN

    p3_path = ROOT / "tests" / "fixtures" / "m1_app" / "p3_golden.json"
    p3_payload = json.loads(p3_path.read_text(encoding="utf-8"))
    p3_ok = (
        _paths_unchanged(("tests/fixtures/m1_app/p3_golden.json",))
        and p3_payload.get("golden_source_sha") == EXPECTED_P3_SOURCE
        and p3_payload.get("digest_sha256") == EXPECTED_P3_DIGEST
    )
    d3_ok = _d3_tag_unchanged()
    p0_ok = _paths_unchanged(
        (
            "src/digital_pulse/m1_contracts.py",
            "protocols/m1-decision.schema.json",
        )
    )

    expected_actions = {
        "accept",
        "retry_same_position",
        "reposition",
        "manual_review",
        "stop",
        "abort_and_release",
    }
    action_ok = actions == expected_actions
    label_ok = labels == {item.value for item in QualityLabel}

    gates = {
        "safety_precedence_verified": _gate(
            "safety_precedence_verified",
            safety_ok,
            matrix_case_count=len(safety_conflicts),
        ),
        "fault_discriminator_verified": _gate(
            "fault_discriminator_verified",
            fault_ok,
            unclassified_states=["FAULT", "SAFE_HOLD"],
            completion_reason_only_device_fault=True,
            explicit_device_fault_fact=True,
            buffer_overflow=True,
            sensor_disconnect_precedence=True,
        ),
        "retry_off_by_one_verified": _gate(
            "retry_off_by_one_verified",
            retry_off_by_one_ok,
            cases=sorted(retry_off_by_one_names),
        ),
        "retry_exhaustion_verified": _gate(
            "retry_exhaustion_verified",
            retry_exhaustion_ok,
            cases=sorted(retry_exhaustion_names),
        ),
        "oracle_isolation_verified": _gate(
            "oracle_isolation_verified",
            oracle_ok,
            scanner_self_test=oracle_scanner_self_test,
        ),
        "determinism_verified": _gate("determinism_verified", determinism_ok),
        "projection_binding_verified": _gate(
            "projection_binding_verified",
            projection_binding_ok,
            cross_context=cross_context_bind_ok,
            forged_evaluation=forged_evaluation_ok,
            rule_binding=rule_bind_ok,
            policy_binding=policy_bind_ok,
        ),
        "decision_id_verified": _gate(
            "decision_id_verified",
            decision_id_ok,
            clock_independent=clock_ok,
            output_bound=output_bind_ok,
            parameter_status_bound=parameter_identity_ok,
            signal_processing_version_bound=sp_identity_ok,
            analysis_allowed_bound=analysis_allowed_identity_ok,
            app_run_bound=app_run_identity_ok,
            app_analysis_fingerprint_bound=app_fingerprint_identity_ok,
            software_commit_sha_excluded=software_sha_excluded_ok,
        ),
        "schema_projection_verified": _gate(
            "schema_projection_verified",
            all(item["passed"] for item in cases),
        ),
        "early_failure_verified": _gate(
            "early_failure_verified",
            early_failure_ok,
            cases=sorted(early_failure_names),
        ),
        "invalid_input_fail_closed_verified": _gate(
            "invalid_input_fail_closed_verified",
            invalid_ok,
        ),
        "reserved_actions_absent": _gate(
            "reserved_actions_absent",
            not actions.intersection({"hold", "adjust_pressure", "continue_scan"}),
        ),
        "p0_contract_unchanged": _gate("p0_contract_unchanged", p0_ok),
        "p2_golden_unchanged": _gate("p2_golden_unchanged", p2_ok),
        "p3_golden_unchanged": _gate("p3_golden_unchanged", p3_ok),
        "d3_tag_unchanged": _gate("d3_tag_unchanged", d3_ok),
        "no_persistence": _gate(
            "no_persistence",
            persistence_ok,
            scanner_self_test=persistence_scanner_self_test,
        ),
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
        "d3_tag_unchanged": gates["d3_tag_unchanged"]["passed"],
        "decision_id_verified": gates["decision_id_verified"]["passed"],
        "determinism_verified": gates["determinism_verified"]["passed"],
        "early_failure_verified": gates["early_failure_verified"]["passed"],
        "failed_gates": failed,
        "fault_discriminator_verified": gates["fault_discriminator_verified"]["passed"],
        "gates": gates,
        "invalid_input_fail_closed_verified": gates["invalid_input_fail_closed_verified"]["passed"],
        "max_retry_count": 2,
        "oracle_isolation_verified": gates["oracle_isolation_verified"]["passed"],
        "p0_contract_unchanged": gates["p0_contract_unchanged"]["passed"],
        "p2_golden_unchanged": gates["p2_golden_unchanged"]["passed"],
        "p3_golden_unchanged": gates["p3_golden_unchanged"]["passed"],
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "projection_binding_verified": gates["projection_binding_verified"]["passed"],
        "quality_label_coverage": sorted(labels),
        "reserved_actions_absent": gates["reserved_actions_absent"]["passed"],
        "retry_exhaustion_verified": gates["retry_exhaustion_verified"]["passed"],
        "retry_off_by_one_verified": gates["retry_off_by_one_verified"]["passed"],
        "rule_version": RULE_VERSION,
        "safety_precedence_verified": gates["safety_precedence_verified"]["passed"],
        "schema_projection_verified": gates["schema_projection_verified"]["passed"],
        "software_commit_sha": software_commit_sha,
        "stage": "M1-P4A",
    }
    return result
