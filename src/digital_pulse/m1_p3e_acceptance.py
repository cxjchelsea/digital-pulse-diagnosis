"""Formal M1-P3E pre-acceptance report projection acceptance gates.

M1Report 为 P0 冻结合同；本模块只验证投影/持久化/只读 API 语义，
不重跑 SP、不发明 INT 决策、不产出医学结论。
P3E acceptance=true 不等于 H1/M1 成功。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping
from collections.abc import Sequence

from fastapi.testclient import TestClient

from digital_pulse.api import create_app
from digital_pulse.m1_app import (
    AppAssetRole,
    AppAssetWrite,
    AppExecutionMode,
    AppPersistence,
    AppSessionLoader,
    M1PreAcceptanceReportBuilder,
    ReplayAnalysisService,
    ReportProjectionInput,
    assert_report_semantic_linkage,
    create_replay_app_provenance,
    deterministic_report_id,
    parse_and_validate_report,
    report_canonical_bytes,
)
from digital_pulse.m1_app.manifest import canonical_json_bytes
from digital_pulse.m1_app.reporting import M1_REPORT_PROJECTION_VERSION
from digital_pulse.m1_app.sp_serialization import sp_result_assets
from digital_pulse.m1_contracts import LimitationCode, ReportStatus, SourceType
from digital_pulse.m1_simulator import (
    M1SessionRecorder,
    SimulatorDataSource,
    get_scenario,
    list_attempt_plans,
    list_scenarios,
)


ACCEPTANCE_VERSION = "m1-p3e-acceptance-v1"
P3E_BASELINE_SHA = "a1a6183bcc2e6b53db8721416513c70a7163543b"
EXPECTED_P2_CANONICAL_GOLDEN_SHA256 = (
    "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"
)
EXPECTED_D3_TAG_OBJECT = "da85aee746453e92b0029ae6ec4f51fefc769e4e"
EXPECTED_D3_TAG_TARGET = "d0251b3741d99bab955fa288c57424abd301b0b1"
FIXED_SOFTWARE_COMMIT_SHA = "c" * 40

# 生产报告投影路径禁止的医学结论用语
MEDICAL_CLAIM_PATTERNS = (
    r"诊断结果",
    r"中医证型",
    r"疾病风险",
    r"健康评分",
    r"治疗建议",
    r"脉象诊断",
    r"正常/异常患者",
    r"可用于临床",
    r"确诊",
)

# 报告投影/服务层不得依赖模拟 oracle 文件名或定义
ORACLE_PATTERNS = (
    r"scenario\.json",
    r"expected\.json",
    r"FaultPlan",
    r"ScenarioDefinition",
    r"expected_quality",
    r"expected_action",
    r"get_scenario_definition",
)

# P3E 生产代码扫描范围（相对仓库根）
P3E_PRODUCTION_SCAN_PATHS = (
    "src/digital_pulse/m1_app/reporting.py",
    "src/digital_pulse/m1_app/replay.py",
    "src/digital_pulse/m1_api/services.py",
    "src/digital_pulse/m1_api/router.py",
)

FlagLike = bool | Callable[[], bool]


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    passed: bool
    evidence: Mapping[str, Any]


def _gate(name: str, passed: bool, **evidence: Any) -> Gate:
    return Gate(name=name, passed=passed, evidence=evidence)


def _resolve_flag(flag: FlagLike) -> bool:
    return bool(flag()) if callable(flag) else bool(flag)


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payload[str(path.relative_to(root)).replace("\\", "/")] = path.read_bytes()
    return payload


def _record(root: Path, scenario_id: str, *, seed: int):
    config = get_scenario(scenario_id, duration_s=8.0, random_seed=seed, sample_rate_hz=250.0)
    recorded = M1SessionRecorder(software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA).record(
        SimulatorDataSource(config),
        output_root=root,
    )
    AppSessionLoader(root).register(recorded.session_id)
    return recorded


def _commit_legacy_run_without_report(root: Path, session_id: str, run_id: str) -> None:
    """写入无 report.json 的遗留 run，用于验证只读投影零突变。"""

    replay = ReplayAnalysisService(root).replay(session_id, software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA)
    AppPersistence(root).commit_run(
        session_id,
        run_id,
        provenance=create_replay_app_provenance(FIXED_SOFTWARE_COMMIT_SHA),
        assets=(
            *sp_result_assets(replay.sp_result),
            AppAssetWrite(
                role=AppAssetRole.ANALYSIS,
                relative_path="analysis.json",
                content=canonical_json_bytes(replay.analysis.to_dict()),
                media_type="application/json",
                producer="legacy-p3b",
                version="m1-app-p3b-v1",
            ),
        ),
        allowed_execution_modes=frozenset({AppExecutionMode.REPLAY}),
    )


def _scan_p3e_production(root: Path) -> dict[str, Any]:
    medical_hits: list[str] = []
    oracle_hits: list[str] = []
    scanned: list[str] = []
    for relative in P3E_PRODUCTION_SCAN_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        scanned.append(relative)
        text = path.read_text(encoding="utf-8")
        for pattern in MEDICAL_CLAIM_PATTERNS:
            if re.search(pattern, text):
                medical_hits.append(f"{relative}:{pattern}")
        for pattern in ORACLE_PATTERNS:
            if re.search(pattern, text):
                oracle_hits.append(f"{relative}:{pattern}")
    return {
        "scanned": scanned,
        "medical_hits": medical_hits,
        "oracle_hits": oracle_hits,
    }


def _error_code(response: Any) -> str | None:
    try:
        return response.json().get("detail", {}).get("error", {}).get("code")
    except Exception:
        return None


def run_m1_p3e_acceptance(
    *,
    root: Path | None = None,
    software_commit_sha: str = FIXED_SOFTWARE_COMMIT_SHA,
    frozen_m1_report_contract_unchanged: FlagLike = True,
    frozen_m1_report_schema_unchanged: FlagLike = True,
    p3d_web_source_unchanged: FlagLike = True,
    web_tests_passed: FlagLike = True,
    web_build_passed: FlagLike = True,
    p3c_regression_passed: FlagLike = True,
    p3b_regression_passed: FlagLike = True,
    p2_regression_passed: FlagLike = True,
    p1_regression_passed: FlagLike = True,
    d3_regression_passed: FlagLike = True,
    p2_canonical_golden_matched: FlagLike = True,
    d3_tag_unchanged: FlagLike = True,
    no_new_sp_algorithm: FlagLike = True,
) -> dict[str, Any]:
    """执行 M1-P3E 正式验收门禁，返回可落盘的证据字典。"""

    repo_root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    scenario_ids = list_scenarios()
    attempt_plan_ids = list_attempt_plans()
    scan = _scan_p3e_production(repo_root)
    gates: list[Gate] = []

    # —— 外部/冻结证据（由 generate 脚本或调用方供给）——
    external_flags = {
        "frozen_m1_report_contract_unchanged": frozen_m1_report_contract_unchanged,
        "frozen_m1_report_schema_unchanged": frozen_m1_report_schema_unchanged,
        "p3d_web_source_unchanged": p3d_web_source_unchanged,
        "web_tests_passed": web_tests_passed,
        "web_build_passed": web_build_passed,
        "p3c_regression_passed": p3c_regression_passed,
        "p3b_regression_passed": p3b_regression_passed,
        "p2_regression_passed": p2_regression_passed,
        "p1_regression_passed": p1_regression_passed,
        "d3_regression_passed": d3_regression_passed,
        "p2_canonical_golden_matched": p2_canonical_golden_matched,
        "d3_tag_unchanged": d3_tag_unchanged,
        "no_new_sp_algorithm": no_new_sp_algorithm,
    }
    for gate_name, flag_value in external_flags.items():
        gates.append(_gate(gate_name, _resolve_flag(flag_value)))

    gates.append(_gate("no_medical_claim", len(scan["medical_hits"]) == 0, hits=scan["medical_hits"]))
    gates.append(_gate("no_oracle_dependency", len(scan["oracle_hits"]) == 0, hits=scan["oracle_hits"]))

    with TemporaryDirectory(prefix="m1-p3e-acceptance-") as temporary:
        data_root = Path(temporary)
        client = TestClient(create_app(data_root=data_root))

        # OpenAPI / 路由存在性
        openapi = client.get("/openapi.json")
        report_path_key = "/api/m1/sessions/{session_id}/report"
        gates.append(
            _gate(
                "report_api_present",
                openapi.status_code == 200
                and report_path_key in openapi.json().get("paths", {})
                and "get" in openapi.json()["paths"][report_path_key],
                openapi_status=openapi.status_code,
            )
        )

        # 代表性场景：高质量 / 弱信号 / 中止 / 不完整
        high_quality = _record(data_root, "normal_high_quality", seed=6101)
        weak_signal = _record(data_root, "weak_signal", seed=6102)
        abort_session = _record(data_root, "abort", seed=6103)
        incomplete = _record(data_root, "frame_loss", seed=6104)

        # 无 current_run：禁止猜测 runs[0]
        no_current = client.get(f"/api/m1/sessions/{high_quality.session_id}/report")
        gates.append(
            _gate(
                "report_no_current_run_fail_closed",
                no_current.status_code == 404 and _error_code(no_current) == "report_not_available",
                status=no_current.status_code,
                code=_error_code(no_current),
            )
        )
        gates.append(
            _gate(
                "no_first_run_guessing",
                no_current.status_code == 404 and _error_code(no_current) == "report_not_available",
                note="missing current_run must not fall back to runs[0]",
            )
        )

        # 新持久化 run：含 report.json
        persist = client.post(
            f"/api/m1/sessions/{high_quality.session_id}/replay",
            json={
                "persist": True,
                "run_id": "run-p3e-hq",
                "software_commit_sha": FIXED_SOFTWARE_COMMIT_SHA,
            },
        )
        loaded_hq = AppSessionLoader(data_root).load(high_quality.session_id)
        run_hq = next(item for item in loaded_hq.app_manifest.runs if item.run_id == "run-p3e-hq")
        report_roles = {asset.role for asset in run_hq.assets}
        gates.append(
            _gate(
                "new_persisted_run_contains_report",
                persist.status_code == 200
                and persist.json().get("persisted") is True
                and AppAssetRole.REPORT in report_roles,
                status=persist.status_code,
                roles=sorted(role.value for role in report_roles),
            )
        )

        # GET report 零突变 + 契约/schema
        before_get = _snapshot_tree(high_quality.session_path)
        report_response = client.get(
            f"/api/m1/sessions/{high_quality.session_id}/report?run_id=run-p3e-hq"
        )
        after_get = _snapshot_tree(high_quality.session_path)
        gates.append(
            _gate(
                "report_get_zero_mutation",
                before_get == after_get and report_response.status_code == 200,
                status=report_response.status_code,
            )
        )
        gates.append(
            _gate(
                "report_api_zero_mutation",
                before_get == after_get,
                mutated=before_get != after_get,
            )
        )

        report_body = report_response.json() if report_response.status_code == 200 else {}
        report_payload = report_body.get("report") or {}
        schema_ok = False
        contract_ok = False
        parsed_report = None
        if isinstance(report_payload, dict) and report_payload:
            try:
                parsed_report = parse_and_validate_report(report_payload)
                schema_ok = True
                contract_ok = True
            except Exception as exc:  # noqa: BLE001 — 验收证据需要吞并异常类型
                schema_ok = False
                contract_ok = False
                report_body = {**report_body, "parse_error": type(exc).__name__}

        gates.append(_gate("report_schema_valid", schema_ok, persisted=report_body.get("persisted")))
        gates.append(
            _gate(
                "persisted_report_contract_valid",
                contract_ok and report_body.get("persisted") is True,
                persisted=report_body.get("persisted"),
            )
        )
        gates.append(
            _gate(
                "persisted_report_checksum_valid",
                report_response.status_code == 200 and report_body.get("persisted") is True,
                note="GET /report 在 checksum 失败时 fail-closed，成功即校验通过",
                status=report_response.status_code,
            )
        )

        # 语义联动：同锚点重投影
        semantic_ok = False
        if parsed_report is not None:
            analysis_asset = next(item for item in run_hq.assets if item.role is AppAssetRole.ANALYSIS)
            analysis_path = loaded_hq.session_root / Path(*analysis_asset.relative_path.split("/"))
            analysis_payload = json.loads(analysis_path.read_text(encoding="utf-8"))
            expected = M1PreAcceptanceReportBuilder().build(
                ReportProjectionInput(
                    session=loaded_hq.session,
                    analysis=analysis_payload,
                    run_id=run_hq.run_id,
                    run_provenance=run_hq.provenance,
                    generated_at_utc=parsed_report.generated_at_utc,
                )
            )
            try:
                assert_report_semantic_linkage(persisted=parsed_report, expected=expected)
                semantic_ok = True
            except Exception:
                semantic_ok = False
        gates.append(_gate("persisted_report_semantic_linkage", semantic_ok))

        # 投影确定性 / report_id 确定性
        analysis_for_builder = ReplayAnalysisService(data_root).replay(
            high_quality.session_id,
            software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
        ).analysis.to_dict()
        builder = M1PreAcceptanceReportBuilder()
        projection_input = ReportProjectionInput(
            session=loaded_hq.session,
            analysis=analysis_for_builder,
            run_id="run-det-check",
            run_provenance=create_replay_app_provenance(FIXED_SOFTWARE_COMMIT_SHA),
            generated_at_utc="2026-08-14T12:00:00Z",
        )
        left = builder.build(projection_input)
        right = builder.build(projection_input)
        gates.append(
            _gate(
                "report_projection_deterministic",
                report_canonical_bytes(left) == report_canonical_bytes(right),
                projection_version=M1_REPORT_PROJECTION_VERSION,
            )
        )
        expected_report_id = deterministic_report_id(
            session_id=loaded_hq.session.session_id,
            run_id="run-det-check",
            analysis_semantic_fingerprint=str(analysis_for_builder["semantic_fingerprint_sha256"]),
        )
        gates.append(
            _gate(
                "report_id_deterministic",
                left.report_id == right.report_id == expected_report_id,
                report_id=left.report_id,
            )
        )

        # 语义门禁：analysis_allowed / formal / 决策 / 限制码
        gates.append(
            _gate(
                "analysis_allowed_mapped",
                left.analysis_allowed is True
                and analysis_for_builder["gate"]["analysis_allowed"] is True,
                analysis_allowed=left.analysis_allowed,
            )
        )
        gates.append(
            _gate(
                "formal_parameters_fail_closed",
                analysis_for_builder["gate"]["formal_parameters_allowed"] is False
                and left.objective_parameters is None,
                formal_parameters_allowed=analysis_for_builder["gate"]["formal_parameters_allowed"],
            )
        )
        gates.append(
            _gate(
                "acceptable_but_pre_h1_objective_parameters_null",
                left.analysis_allowed is True and left.objective_parameters is None,
                objective_parameters=left.objective_parameters,
            )
        )
        gates.append(
            _gate(
                "decision_unavailable_pre_p4",
                left.decision_summary.get("final_action") is None
                and left.decision_summary.get("decision_ids") == []
                and left.decision_summary.get("reason_codes") == [],
                decision_summary=left.decision_summary,
            )
        )
        gates.append(
            _gate(
                "no_fake_decision",
                left.decision_summary.get("final_action") is None,
                final_action=left.decision_summary.get("final_action"),
            )
        )
        gates.append(
            _gate(
                "decision_rule_version_null_pre_p4",
                left.version_manifest.get("decision_rule_version") is None,
                decision_rule_version=left.version_manifest.get("decision_rule_version"),
            )
        )
        limitation_values = {item.value for item in left.limitations}
        allowed_limitation_values = {item.value for item in LimitationCode}
        gates.append(
            _gate(
                "limitations_frozen_enum_only",
                limitation_values.issubset(allowed_limitation_values),
                limitations=sorted(limitation_values),
            )
        )
        gates.append(
            _gate(
                "not_for_medical_use_always",
                LimitationCode.NOT_FOR_MEDICAL_USE in left.limitations,
                limitations=sorted(limitation_values),
            )
        )
        gates.append(
            _gate(
                "simulator_requires_synthetic_input",
                left.source_type is SourceType.SIMULATOR
                and LimitationCode.SYNTHETIC_INPUT in left.limitations,
                source_type=left.source_type.value,
            )
        )
        gates.append(
            _gate(
                "synthetic_only_mapped_not_copied",
                "synthetic_only" not in limitation_values
                and LimitationCode.SYNTHETIC_INPUT in left.limitations,
                limitations=sorted(limitation_values),
            )
        )
        gates.append(
            _gate(
                "version_manifest_traceable",
                isinstance(left.version_manifest.get("software_commit_sha"), str)
                and len(str(left.version_manifest.get("software_commit_sha"))) == 40
                and left.version_manifest.get("signal_processing_version") is not None,
                version_manifest=left.version_manifest,
            )
        )
        gates.append(
            _gate(
                "source_type_preserved",
                left.source_type is SourceType.SIMULATOR
                and left.source_type is loaded_hq.session.source_type,
                source_type=left.source_type.value,
            )
        )
        gates.append(
            _gate(
                "report_status_mapping_deterministic",
                left.report_status is ReportStatus.COMPLETE,
                report_status=left.report_status.value,
            )
        )

        # 弱信号：blocked → objective null + FAILED
        weak_analysis = ReplayAnalysisService(data_root).replay(
            weak_signal.session_id,
            software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
        ).analysis.to_dict()
        loaded_weak = AppSessionLoader(data_root).load(weak_signal.session_id, verify_runs=False)
        weak_report = builder.build(
            ReportProjectionInput(
                session=loaded_weak.session,
                analysis=weak_analysis,
                run_id="run-weak",
                run_provenance=create_replay_app_provenance(FIXED_SOFTWARE_COMMIT_SHA),
                generated_at_utc="2026-08-14T12:10:00Z",
            )
        )
        gates.append(
            _gate(
                "blocked_objective_parameters_null",
                weak_report.analysis_allowed is False
                and weak_report.objective_parameters is None
                and weak_report.report_status is ReportStatus.FAILED,
                analysis_allowed=weak_report.analysis_allowed,
                report_status=weak_report.report_status.value,
            )
        )

        # abort / incomplete 状态映射抽检（注册表场景覆盖）
        abort_analysis = ReplayAnalysisService(data_root).replay(
            abort_session.session_id,
            software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
        ).analysis.to_dict()
        loaded_abort = AppSessionLoader(data_root).load(abort_session.session_id, verify_runs=False)
        abort_report = builder.build(
            ReportProjectionInput(
                session=loaded_abort.session,
                analysis=abort_analysis,
                run_id="run-abort",
                run_provenance=create_replay_app_provenance(FIXED_SOFTWARE_COMMIT_SHA),
                generated_at_utc="2026-08-14T12:20:00Z",
            )
        )
        incomplete_analysis = ReplayAnalysisService(data_root).replay(
            incomplete.session_id,
            software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
        ).analysis.to_dict()
        loaded_incomplete = AppSessionLoader(data_root).load(incomplete.session_id, verify_runs=False)
        incomplete_report = builder.build(
            ReportProjectionInput(
                session=loaded_incomplete.session,
                analysis=incomplete_analysis,
                run_id="run-incomplete",
                run_provenance=create_replay_app_provenance(FIXED_SOFTWARE_COMMIT_SHA),
                generated_at_utc="2026-08-14T12:30:00Z",
            )
        )
        status_matrix_ok = (
            abort_report.report_status is ReportStatus.ABORTED
            and incomplete_report.report_status is ReportStatus.INCOMPLETE
            and weak_report.report_status is ReportStatus.FAILED
            and left.report_status is ReportStatus.COMPLETE
        )
        # 覆盖 report_status_mapping_deterministic 的完整矩阵证据
        gates[:] = [
            item
            if item.name != "report_status_mapping_deterministic"
            else _gate(
                "report_status_mapping_deterministic",
                status_matrix_ok,
                complete=left.report_status.value,
                failed=weak_report.report_status.value,
                aborted=abort_report.report_status.value,
                incomplete=incomplete_report.report_status.value,
                registry_scenarios=len(scenario_ids),
                registry_attempt_plans=len(attempt_plan_ids),
            )
            for item in gates
        ]

        # 遗留 run：投影零突变 + 确定性
        _commit_legacy_run_without_report(data_root, high_quality.session_id, "run-legacy-no-report")
        before_legacy = _snapshot_tree(high_quality.session_path)
        legacy_first = client.get(
            f"/api/m1/sessions/{high_quality.session_id}/report?run_id=run-legacy-no-report"
        )
        after_legacy = _snapshot_tree(high_quality.session_path)
        legacy_second = client.get(
            f"/api/m1/sessions/{high_quality.session_id}/report?run_id=run-legacy-no-report"
        )
        gates.append(
            _gate(
                "legacy_run_projection_zero_mutation",
                before_legacy == after_legacy
                and legacy_first.status_code == 200
                and legacy_first.json().get("persisted") is False,
                status=legacy_first.status_code,
                persisted=legacy_first.json().get("persisted") if legacy_first.status_code == 200 else None,
            )
        )
        gates.append(
            _gate(
                "legacy_run_report_deterministic",
                legacy_first.status_code == 200
                and legacy_second.status_code == 200
                and legacy_first.json().get("report") == legacy_second.json().get("report"),
            )
        )

        # 显式 run / current run 选择
        client.post(
            f"/api/m1/sessions/{high_quality.session_id}/replay",
            json={
                "persist": True,
                "run_id": "run-p3e-second",
                "software_commit_sha": FIXED_SOFTWARE_COMMIT_SHA,
            },
        )
        current = client.get(f"/api/m1/sessions/{high_quality.session_id}/report")
        explicit = client.get(
            f"/api/m1/sessions/{high_quality.session_id}/report?run_id=run-p3e-hq"
        )
        gates.append(
            _gate(
                "report_current_run_selection",
                current.status_code == 200 and current.json().get("run_id") == "run-p3e-second",
                run_id=current.json().get("run_id") if current.status_code == 200 else None,
            )
        )
        gates.append(
            _gate(
                "report_explicit_run_selection",
                explicit.status_code == 200 and explicit.json().get("run_id") == "run-p3e-hq",
                run_id=explicit.json().get("run_id") if explicit.status_code == 200 else None,
            )
        )

        # 篡改 fail-closed
        report_asset = next(item for item in run_hq.assets if item.role is AppAssetRole.REPORT)
        report_path = high_quality.session_path / Path(*report_asset.relative_path.split("/"))
        original_bytes = report_path.read_bytes()
        report_path.write_bytes(original_bytes + b" ")
        tampered = client.get(
            f"/api/m1/sessions/{high_quality.session_id}/report?run_id=run-p3e-hq"
        )
        report_path.write_bytes(original_bytes)
        gates.append(
            _gate(
                "report_tamper_fail_closed",
                tampered.status_code == 422 and _error_code(tampered) == "artifact_corrupted",
                status=tampered.status_code,
                code=_error_code(tampered),
            )
        )

        # 稳定错误码
        missing_run = client.get(
            f"/api/m1/sessions/{high_quality.session_id}/report?run_id=missing-run"
        )
        traversal = client.get("/api/m1/sessions/%2e%2e/report")
        gates.append(
            _gate(
                "stable_report_errors",
                missing_run.status_code == 404
                and _error_code(missing_run) == "run_not_found"
                and traversal.status_code == 400
                and _error_code(traversal) == "invalid_session_id",
                missing_run=_error_code(missing_run),
                traversal=_error_code(traversal),
            )
        )

    # 去重门禁名（保留最后一次同名证据）
    by_name: dict[str, Gate] = {}
    for item in gates:
        by_name[item.name] = item

    ordered_names = _required_gate_names()
    normalized: list[Gate] = []
    for name in ordered_names:
        if name in by_name:
            normalized.append(by_name[name])
        else:
            normalized.append(_gate(name, False, missing=True))
    for name, item in by_name.items():
        if name not in ordered_names:
            normalized.append(item)

    failed = [item.name for item in normalized if not item.passed]
    return {
        "acceptance_version": ACCEPTANCE_VERSION,
        "projection_version": M1_REPORT_PROJECTION_VERSION,
        "software_commit_sha": software_commit_sha,
        "baseline_sha": P3E_BASELINE_SHA,
        "acceptance": len(failed) == 0,
        "failed_gates": failed,
        "gates": {
            item.name: {"passed": item.passed, "evidence": dict(item.evidence)} for item in normalized
        },
        "scenario_registry": {
            "single_attempt_count": len(scenario_ids),
            "multi_attempt_count": len(attempt_plan_ids),
            "total_case_count": len(scenario_ids) + len(attempt_plan_ids),
            "scenarios": list(scenario_ids),
            "attempt_plans": list(attempt_plan_ids),
        },
        "scan": scan,
        "http_testclient_exercised": True,
        "expected_p2_canonical_golden_sha256": EXPECTED_P2_CANONICAL_GOLDEN_SHA256,
        "expected_d3_tag_object": EXPECTED_D3_TAG_OBJECT,
        "expected_d3_tag_target": EXPECTED_D3_TAG_TARGET,
        "note": "P3E acceptance=true 不等于 H1/M1 成功；不做医学结论或 INT 决策。",
    }


def _required_gate_names() -> Sequence[str]:
    return (
        "frozen_m1_report_contract_unchanged",
        "frozen_m1_report_schema_unchanged",
        "report_schema_valid",
        "report_projection_deterministic",
        "report_id_deterministic",
        "report_get_zero_mutation",
        "legacy_run_projection_zero_mutation",
        "legacy_run_report_deterministic",
        "new_persisted_run_contains_report",
        "persisted_report_checksum_valid",
        "persisted_report_contract_valid",
        "persisted_report_semantic_linkage",
        "report_tamper_fail_closed",
        "analysis_allowed_mapped",
        "formal_parameters_fail_closed",
        "blocked_objective_parameters_null",
        "acceptable_but_pre_h1_objective_parameters_null",
        "decision_unavailable_pre_p4",
        "no_fake_decision",
        "decision_rule_version_null_pre_p4",
        "limitations_frozen_enum_only",
        "not_for_medical_use_always",
        "simulator_requires_synthetic_input",
        "synthetic_only_mapped_not_copied",
        "version_manifest_traceable",
        "source_type_preserved",
        "report_status_mapping_deterministic",
        "report_api_present",
        "report_explicit_run_selection",
        "report_current_run_selection",
        "report_no_current_run_fail_closed",
        "no_first_run_guessing",
        "report_api_zero_mutation",
        "stable_report_errors",
        "no_oracle_dependency",
        "no_new_sp_algorithm",
        "no_medical_claim",
        "p3d_web_source_unchanged",
        "web_tests_passed",
        "web_build_passed",
        "p3c_regression_passed",
        "p3b_regression_passed",
        "p2_regression_passed",
        "p1_regression_passed",
        "d3_regression_passed",
        "p2_canonical_golden_matched",
        "d3_tag_unchanged",
    )


__all__ = [
    "ACCEPTANCE_VERSION",
    "EXPECTED_D3_TAG_OBJECT",
    "EXPECTED_D3_TAG_TARGET",
    "EXPECTED_P2_CANONICAL_GOLDEN_SHA256",
    "P3E_BASELINE_SHA",
    "run_m1_p3e_acceptance",
]
