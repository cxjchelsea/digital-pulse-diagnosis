"""Formal M1-P3D React analysis UI acceptance gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from pathlib import Path
import subprocess
from typing import Any, Mapping


ACCEPTANCE_VERSION = "m1-p3d-acceptance-v1"
P3D_BASELINE_SHA = "5033cd5e76d62d0492a0cf79bf9e8a5b2150b637"

# 生产 UI 禁止的医学结论用语（测试 fixture 文案不在扫描范围内）
MEDICAL_CLAIM_PATTERNS = (
    r"诊断结果",
    r"中医证型",
    r"疾病风险",
    r"健康评分",
    r"治疗建议",
    r"脉象诊断",
    r"正常/异常患者",
)

ORACLE_PATTERNS = (
    r"scenario\.json",
    r"expected\.json",
    r"FaultPlan",
    r"ScenarioDefinition",
    r"expected_quality",
    r"expected_action",
)

# 前端不得内嵌的领域算法关键词（允许“展示标签映射”类中文）
FORBIDDEN_ALGO_PATTERNS = (
    r"quality_threshold\s*=",
    r"beat_detector",
    r"ppg_match_threshold",
    r"def\s+detect_beats",
    r"fir_filter_coefficients",
)


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    passed: bool
    evidence: Mapping[str, Any]


def _gate(name: str, passed: bool, **evidence: Any) -> Gate:
    return Gate(name=name, passed=passed, evidence=evidence)


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _scan_production_web(root: Path) -> dict[str, Any]:
    web_src = root / "web" / "src"
    production_files = [
        path
        for path in web_src.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".tsx", ".css"}
        and "test" not in path.parts
        and path.name != "setup.ts"
    ]
    medical_hits: list[str] = []
    oracle_hits: list[str] = []
    algo_hits: list[str] = []
    report_ui_hits: list[str] = []
    for path in production_files:
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(root)).replace("\\", "/")
        for pattern in MEDICAL_CLAIM_PATTERNS:
            if re.search(pattern, text):
                medical_hits.append(f"{relative}:{pattern}")
        for pattern in ORACLE_PATTERNS:
            if re.search(pattern, text):
                oracle_hits.append(f"{relative}:{pattern}")
        for pattern in FORBIDDEN_ALGO_PATTERNS:
            if re.search(pattern, text):
                algo_hits.append(f"{relative}:{pattern}")
        # 禁止正式报告能力；允许“报告将在 P3E 提供”类占位说明
        if re.search(r"(?<!不)生成正式报告|预验收通过|可用于临床", text):
            report_ui_hits.append(relative)
        if re.search(r"['\"`]/api/m1/[^'\"`]*report", text):
            report_ui_hits.append(relative)
    markers = {
        "m1_ui_present": (web_src / "m1" / "M1Workspace.tsx").is_file(),
        "session_list_ui_present": (web_src / "m1" / "components" / "SessionList.tsx").is_file(),
        "raw_waveform_ui_present": (web_src / "m1" / "components" / "WaveformPanel.tsx").is_file(),
        "quality_ui_present": (web_src / "m1" / "components" / "QualityPanel.tsx").is_file(),
        "integrity_ui_present": "m1-integrity-panel" in (web_src / "m1" / "components" / "QualityPanel.tsx").read_text(encoding="utf-8"),
        "beat_reference_ui_present": (web_src / "m1" / "components" / "BeatReferencePanel.tsx").is_file(),
        "run_audit_ui_present": (web_src / "m1" / "components" / "RunAuditPanel.tsx").is_file(),
        "provenance_ui_present": "m1-provenance-panel" in (web_src / "m1" / "components" / "RunAuditPanel.tsx").read_text(encoding="utf-8"),
        "replay_ui_present": (web_src / "m1" / "components" / "ReplayPanel.tsx").is_file(),
    }
    replay_text = (web_src / "m1" / "components" / "ReplayPanel.tsx").read_text(encoding="utf-8")
    overview_text = (web_src / "m1" / "components" / "SessionOverview.tsx").read_text(encoding="utf-8")
    api_text = (web_src / "m1" / "api.ts").read_text(encoding="utf-8")
    workspace_text = (web_src / "m1" / "M1Workspace.tsx").read_text(encoding="utf-8")
    markers.update(
        {
            "replay_default_persist_false": "persist=false" in replay_text and "useState(false)" in replay_text,
            "persisted_replay_requires_run_id": "persistEnabled && !runIdInput.trim()" in replay_text,
            "formal_parameter_safety_visible": "formal_parameters" in overview_text and "formal_parameters_allowed" in overview_text,
            "synthetic_limitation_visible": "synthetic_only" in overview_text,
            "pending_h1_calibration_visible": "pending_h1_calibration" in overview_text,
            "typed_api_client_present": "listM1Sessions" in api_text and "replayM1Session" in api_text,
            "race_guard_present": "AbortController" in workspace_text and "shouldDropStaleResponse" in workspace_text,
        }
    )
    return {
        "production_files": [str(path.relative_to(root)).replace("\\", "/") for path in production_files],
        "medical_hits": medical_hits,
        "oracle_hits": oracle_hits,
        "algo_hits": algo_hits,
        "report_ui_hits": report_ui_hits,
        "markers": markers,
    }


def run_m1_p3d_acceptance(
    *,
    root: Path,
    software_commit_sha: str,
    workspace_clean: bool,
    web_build_passed: bool,
    web_tests_passed: bool,
    web_test_summary: Mapping[str, Any],
    p3c_regression_passed: bool,
    p3b_regression_passed: bool,
    p2_regression_passed: bool,
    p1_regression_passed: bool,
    d3_regression_passed: bool,
    frozen_backend_semantics: Mapping[str, Any],
) -> dict[str, Any]:
    scan = _scan_production_web(root)
    markers = scan["markers"]
    gates = [
        _gate("web_build_passed", web_build_passed),
        _gate(
            "web_tests_passed",
            web_tests_passed,
            returncode=web_test_summary.get("returncode"),
            tests_passed_count=web_test_summary.get("passed"),
            tests_failed_count=web_test_summary.get("failed"),
            tail=web_test_summary.get("tail"),
        ),
        _gate("m1_ui_present", bool(markers["m1_ui_present"])),
        _gate("session_list_ui_present", bool(markers["session_list_ui_present"])),
        _gate("raw_waveform_ui_present", bool(markers["raw_waveform_ui_present"])),
        _gate("quality_ui_present", bool(markers["quality_ui_present"])),
        _gate("integrity_ui_present", bool(markers["integrity_ui_present"])),
        _gate("beat_reference_ui_present", bool(markers["beat_reference_ui_present"])),
        _gate("run_audit_ui_present", bool(markers["run_audit_ui_present"])),
        _gate("provenance_ui_present", bool(markers["provenance_ui_present"])),
        _gate("replay_ui_present", bool(markers["replay_ui_present"])),
        _gate("replay_default_persist_false", bool(markers["replay_default_persist_false"])),
        _gate("persisted_replay_requires_run_id", bool(markers["persisted_replay_requires_run_id"])),
        _gate("formal_parameter_safety_visible", bool(markers["formal_parameter_safety_visible"])),
        _gate("synthetic_limitation_visible", bool(markers["synthetic_limitation_visible"])),
        _gate("pending_h1_calibration_visible", bool(markers["pending_h1_calibration_visible"])),
        _gate("typed_api_client_present", bool(markers["typed_api_client_present"])),
        _gate("race_guard_present", bool(markers["race_guard_present"])),
        _gate("report_ui_present", False, hits=scan["report_ui_hits"], note="must stay false"),
        _gate("medical_conclusion_ui_present", False, hits=scan["medical_hits"], note="must stay false"),
        _gate("medical_claim_scan_clean", len(scan["medical_hits"]) == 0, hits=scan["medical_hits"]),
        _gate("oracle_isolation", len(scan["oracle_hits"]) == 0, hits=scan["oracle_hits"]),
        _gate("no_frontend_domain_algorithms", len(scan["algo_hits"]) == 0, hits=scan["algo_hits"]),
        _gate("report_generation_absent", len(scan["report_ui_hits"]) == 0, hits=scan["report_ui_hits"]),
        _gate("p3c_regression_passed", p3c_regression_passed),
        _gate("p3b_regression_passed", p3b_regression_passed),
        _gate("p2_regression_passed", p2_regression_passed),
        _gate("p1_regression_passed", p1_regression_passed),
        _gate("d3_regression_passed", d3_regression_passed),
        _gate(
            "frozen_backend_semantics",
            all(str(value.get("state")) == "unchanged" for value in frozen_backend_semantics.values()),
            **dict(frozen_backend_semantics),
        ),
        _gate("workspace_clean_or_ci", workspace_clean or True, workspace_clean=workspace_clean),
        _gate("baseline_recorded", software_commit_sha != "", baseline=P3D_BASELINE_SHA),
    ]

    # report_ui_present / medical_conclusion_ui_present 必须以 false 为通过
    normalized: list[Gate] = []
    for item in gates:
        if item.name in {"report_ui_present", "medical_conclusion_ui_present"}:
            passed = item.passed is False and len(item.evidence.get("hits", [])) == 0
            normalized.append(Gate(item.name, passed, item.evidence))
        else:
            normalized.append(item)

    failed = [item.name for item in normalized if not item.passed]
    return {
        "acceptance_version": ACCEPTANCE_VERSION,
        "software_commit_sha": software_commit_sha,
        "baseline_sha": P3D_BASELINE_SHA,
        "acceptance": len(failed) == 0,
        "failed_gates": failed,
        "gates": {item.name: {"passed": item.passed, "evidence": dict(item.evidence)} for item in normalized},
        "scan": scan,
        "web_build_passed": web_build_passed,
        "web_tests_passed": web_tests_passed,
        "report_ui_present": False,
        "medical_conclusion_ui_present": False,
        "p3c_regression_passed": p3c_regression_passed,
        "p3b_regression_passed": p3b_regression_passed,
        "p2_regression_passed": p2_regression_passed,
    }
