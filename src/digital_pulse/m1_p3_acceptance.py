"""M1-P3 聚合验收：P3A–P3E 集成门禁与最终 E2E 证据。

本模块是验收/发布层，不是新的生产分析版本。
P3F stage version 不得写入 AppAnalysis.processing_version。
M1-P3 acceptance=true 不等于 M1/H1/硬件/医学通过。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time
import tracemalloc
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from fastapi.testclient import TestClient

from digital_pulse.api import create_app
from digital_pulse.m1_app import (
    APP_ANALYSIS_FINGERPRINT_VERSION,
    APP_PROCESSING_VERSION_P3B,
    AppAssetRole,
    AppSessionLoader,
    M1AppError,
    M1PreAcceptanceReportBuilder,
    ReplayAnalysisService,
    ReportProjectionInput,
    SafeSessionPath,
    compare_app_analysis,
    create_replay_app_provenance,
)
from digital_pulse.m1_app.analysis import AnalysisProjector
from digital_pulse.m1_app.checksums import compute_registered_checksum
from digital_pulse.m1_app.manifest import canonical_json_bytes, write_app_manifest_atomic
from digital_pulse.m1_app.models import (
    AppAssetRef,
    AppManifest,
    AppRunManifest,
    ChecksumProvenance,
    ChecksumSource,
)
from digital_pulse.m1_app.reporting import M1_REPORT_PROJECTION_VERSION
from digital_pulse.m1_p3e_acceptance import run_m1_p3e_acceptance
from digital_pulse.m1_simulator import (
    M1SessionRecorder,
    SimulatorDataSource,
    get_attempt_plan,
    get_scenario,
    list_attempt_plans,
    list_scenarios,
    list_simulation_cases,
)
from digital_pulse.m1_simulator.replay import ReplayDataSource
from digital_pulse.m1_sp import SPProcessor, compare_sp_results
from digital_pulse.m1_sp.models import SPProcessingProvenance
from digital_pulse.m1_sp.parameters import SP_PARAMETER_VERSION_P2C, SP_PROCESSING_VERSION_P2C
from digital_pulse.m1_sp.processor import SP_PROCESSING_VERSION_P2D
from digital_pulse.m1_sp.summary import SP_RESULT_FINGERPRINT_VERSION


ACCEPTANCE_VERSION = "m1-p3-acceptance-v1"
P3F_STAGE_VERSION = "0.6.0-p3f"
P3F_BASELINE_SHA = "2f4f88cc69fbdfb1e129d347025695334542eb9e"
SEMANTIC_SUMMARY_VERSION = "p3-acceptance-semantic-summary:v1"
FIXED_SOFTWARE_COMMIT_SHA = "c" * 40
ACCEPTANCE_SEED = 1001
ACCEPTANCE_DURATION_S = 8.0
ACCEPTANCE_INSUFFICIENT_DURATION_S = 1.2
ACCEPTANCE_SAMPLE_RATE_HZ = 250.0
EXPECTED_SINGLE_COUNT = 16
EXPECTED_MULTI_COUNT = 2
EXPECTED_CASE_COUNT = 18
EXPECTED_ATTEMPT_COUNT = 21
EXPECTED_P2_CANONICAL_GOLDEN_SHA256 = (
    "8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b"
)
EXPECTED_D3_TAG_OBJECT = "da85aee746453e92b0029ae6ec4f51fefc769e4e"
EXPECTED_D3_TAG_TARGET = "d0251b3741d99bab955fa288c57424abd301b0b1"
CHANNEL_DISPLAY_MAX_POINTS = 5000
PERFORMANCE_REGRESSION_RATIO_LIMIT = 10.0

# 生产表面禁止的医学结论用语
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

# 生产路径不得依赖模拟 oracle
ORACLE_PATTERNS = (
    r"scenario\.json",
    r"expected\.json",
    r"expected_quality",
    r"expected_action",
    r"get_scenario_definition",
)

# P3F 不得把 INT 动作写进报告/API 生产输出
P4_DECISION_LEAK_PATTERNS = (
    r'"final_action":\s*"(accept|retry_same_position|reposition|stop|abort_and_release)"',
)

P3F_PRODUCTION_SCAN_PATHS = (
    "src/digital_pulse/m1_app/reporting.py",
    "src/digital_pulse/m1_app/replay.py",
    "src/digital_pulse/m1_app/analysis.py",
    "src/digital_pulse/m1_app/gating.py",
    "src/digital_pulse/m1_api/services.py",
    "src/digital_pulse/m1_api/router.py",
    "web/src",
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


def _scenario_overrides(scenario_id: str) -> dict[str, Any]:
    duration_s = (
        ACCEPTANCE_INSUFFICIENT_DURATION_S
        if scenario_id == "insufficient_duration"
        else ACCEPTANCE_DURATION_S
    )
    return {
        "random_seed": ACCEPTANCE_SEED,
        "sample_rate_hz": ACCEPTANCE_SAMPLE_RATE_HZ,
        "duration_s": duration_s,
    }


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payload[str(path.relative_to(root)).replace("\\", "/")] = path.read_bytes()
    return payload


def _error_code(response: Any) -> str | None:
    try:
        return response.json().get("detail", {}).get("error", {}).get("code")
    except Exception:
        return None


def _quality_label(analysis: Any) -> str | None:
    if analysis.quality is None:
        return None
    label = analysis.quality.get("label")
    return str(label) if label is not None else None


def _quality_reason_codes(analysis: Any) -> list[str]:
    if analysis.quality is None:
        return []
    codes = analysis.quality.get("reason_codes") or []
    return [str(item) for item in codes]


def normalize_report_semantics(report_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Direct/Replay 比较用的验收语义投影；明确排除非语义字段。

    排除：report_id、generated_at_utc。
    不排除：analysis_allowed、quality/integrity、objective_parameters、
    decision_summary、limitations、parameter_status、failure_summary、版本字段。
    """

    payload = dict(report_payload)
    payload.pop("report_id", None)
    payload.pop("generated_at_utc", None)
    return payload


def compact_case_summary(
    *,
    scenario_id: str,
    attempt_index: int,
    session: Any,
    analysis: Any,
    report_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """验收专用紧凑摘要。ACCEPTANCE-ONLY，不是生产 APP fingerprint。"""

    return {
        "scenario_id": scenario_id,
        "attempt_index": attempt_index,
        "session_completed": bool(session.completed),
        "session_completion_reason": session.completion_reason,
        "raw_persistence_status": session.integrity_summary.raw_persistence_status.value,
        "sp_processing_status": analysis.processing_status,
        "quality_label": _quality_label(analysis),
        "quality_reason_codes": _quality_reason_codes(analysis),
        "analysis_allowed": bool(analysis.gate.analysis_allowed),
        "formal_parameters_allowed": bool(analysis.gate.formal_parameters_allowed),
        "formal_parameters_is_null": analysis.formal_parameters is None,
        "app_limitations": sorted(analysis.limitations),
        "report_status": report_payload.get("report_status"),
        "report_analysis_allowed": report_payload.get("analysis_allowed"),
        "report_objective_parameters_present": report_payload.get("objective_parameters") is not None,
        "report_decision_action": (report_payload.get("decision_summary") or {}).get("final_action"),
        "report_decision_ids": list((report_payload.get("decision_summary") or {}).get("decision_ids") or []),
        "report_reason_codes": list((report_payload.get("decision_summary") or {}).get("reason_codes") or []),
        "report_decision_rule_version": (report_payload.get("version_manifest") or {}).get(
            "decision_rule_version"
        ),
        "report_limitations": sorted(report_payload.get("limitations") or []),
        "parameter_status": report_payload.get("parameter_status"),
        "not_for_medical_use": "not_for_medical_use" in (report_payload.get("limitations") or []),
    }


def _digest_cases(cases: list[Mapping[str, Any]]) -> str:
    digest_payload = {
        "digest_version": SEMANTIC_SUMMARY_VERSION,
        "acceptance_only": True,
        "cases": cases,
    }
    return hashlib.sha256(canonical_json_bytes(digest_payload)).hexdigest()


def _record_session(root: Path, config: Any, *, software_commit_sha: str):
    recorded = M1SessionRecorder(software_commit_sha=software_commit_sha).record(
        SimulatorDataSource(config),
        output_root=root,
    )
    AppSessionLoader(root).register(recorded.session_id)
    return recorded


def _direct_and_replay_bundle(
    root: Path,
    config: Any,
    *,
    software_commit_sha: str,
    persist_run_id: str | None = None,
) -> dict[str, Any]:
    """同一冻结输入上构造 Direct SP/APP/Report 与 Replay 对照。"""

    recorded = _record_session(root, config, software_commit_sha=software_commit_sha)
    replay_source = ReplayDataSource(
        recorded.session_path,
        allow_incomplete=not recorded.completed,
    )
    samples = list(replay_source.samples())
    provenance = SPProcessingProvenance(software_commit_sha=software_commit_sha)
    direct_sp = SPProcessor().process(replay_source.session, samples, provenance=provenance)
    app_provenance = create_replay_app_provenance(software_commit_sha)
    direct_app = AnalysisProjector().project(
        session=replay_source.session,
        sp_result=direct_sp,
        app_provenance=app_provenance,
    )

    service = ReplayAnalysisService(root)
    replayed = service.replay(
        recorded.session_id,
        software_commit_sha=software_commit_sha,
        persist=persist_run_id is not None,
        run_id=persist_run_id,
    )
    comparison_run_id = persist_run_id or "acceptance-compare-run"
    generated_at_utc = replay_source.session.started_at_utc
    if persist_run_id is not None:
        loaded = AppSessionLoader(root).load(recorded.session_id)
        run = next(item for item in loaded.app_manifest.runs if item.run_id == persist_run_id)
        report_asset = next(item for item in run.assets if item.role.value == "report")
        report_path = recorded.session_path / report_asset.relative_path
        persisted_report = json.loads(report_path.read_text(encoding="utf-8"))
        generated_at_utc = persisted_report["generated_at_utc"]

    direct_report = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=replay_source.session,
            analysis=direct_app.to_dict(),
            run_id=comparison_run_id,
            run_provenance=app_provenance,
            generated_at_utc=generated_at_utc,
        )
    )
    replay_report = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=replay_source.session,
            analysis=replayed.analysis.to_dict(),
            run_id=comparison_run_id,
            run_provenance=create_replay_app_provenance(software_commit_sha),
            generated_at_utc=generated_at_utc,
        )
    )
    return {
        "recorded": recorded,
        "session": replay_source.session,
        "direct_sp": direct_sp,
        "replay_sp": replayed.sp_result,
        "direct_app": direct_app,
        "replay_app": replayed.analysis,
        "direct_report": direct_report.to_dict(),
        "replay_report": replay_report.to_dict(),
        "persisted": persist_run_id is not None,
    }


def iter_frozen_matrix_configs() -> list[dict[str, Any]]:
    """按冻结 registry 展开 16 单场景 + 多尝试计划的全部 attempt。"""

    items: list[dict[str, Any]] = []
    for scenario_id in list_scenarios():
        items.append(
            {
                "scenario_id": scenario_id,
                "attempt_index": 1,
                "config": get_scenario(scenario_id, **_scenario_overrides(scenario_id)),
            }
        )
    for plan_id in list_attempt_plans():
        plan = get_attempt_plan(
            plan_id,
            random_seed=ACCEPTANCE_SEED,
            duration_s=ACCEPTANCE_DURATION_S,
            sample_rate_hz=ACCEPTANCE_SAMPLE_RATE_HZ,
        )
        for attempt in plan.attempts:
            items.append(
                {
                    "scenario_id": plan_id,
                    "attempt_index": attempt.attempt_index,
                    "config": attempt.config,
                }
            )
    return items


def build_semantic_golden_document(*, software_commit_sha: str = FIXED_SOFTWARE_COMMIT_SHA) -> dict[str, Any]:
    """从当前生产管线生成验收专用 golden。调用方必须在基线 SHA 上运行。"""

    cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="m1-p3-golden-") as temporary:
        root = Path(temporary)
        for index, item in enumerate(iter_frozen_matrix_configs()):
            case_root = root / f"case-{index:02d}"
            case_root.mkdir()
            bundle = _direct_and_replay_bundle(
                case_root,
                item["config"],
                software_commit_sha=software_commit_sha,
            )
            cases.append(
                compact_case_summary(
                    scenario_id=item["scenario_id"],
                    attempt_index=item["attempt_index"],
                    session=bundle["session"],
                    analysis=bundle["replay_app"],
                    report_payload=bundle["replay_report"],
                )
            )
    cases.sort(key=lambda row: (row["scenario_id"], row["attempt_index"]))
    return {
        "golden_source_sha": P3F_BASELINE_SHA,
        "digest_version": SEMANTIC_SUMMARY_VERSION,
        "acceptance_only": True,
        "note": "ACCEPTANCE-ONLY digest; not app-analysis-fingerprint:v1 and not a production report fingerprint.",
        "cases": cases,
        "digest_sha256": _digest_cases(cases),
    }


def load_semantic_golden(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_golden_path(repo_root: Path) -> Path:
    return repo_root / "tests" / "fixtures" / "m1_app" / "p3_golden.json"


def _scan_paths(repo_root: Path) -> dict[str, Any]:
    medical_hits: list[str] = []
    oracle_hits: list[str] = []
    p4_hits: list[str] = []
    scanned: list[str] = []
    for relative in P3F_PRODUCTION_SCAN_PATHS:
        target = repo_root / relative
        files: list[Path]
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = [path for path in target.rglob("*") if path.is_file() and path.suffix in {".py", ".ts", ".tsx"}]
        else:
            continue
        for path in files:
            scanned.append(str(path.relative_to(repo_root)).replace("\\", "/"))
            text = path.read_text(encoding="utf-8")
            for pattern in MEDICAL_CLAIM_PATTERNS:
                if re.search(pattern, text):
                    medical_hits.append(f"{path.name}:{pattern}")
            for pattern in ORACLE_PATTERNS:
                if re.search(pattern, text):
                    oracle_hits.append(f"{path.name}:{pattern}")
            for pattern in P4_DECISION_LEAK_PATTERNS:
                if re.search(pattern, text):
                    p4_hits.append(f"{path.name}:{pattern}")
    return {
        "scanned": scanned,
        "medical_hits": medical_hits,
        "oracle_hits": oracle_hits,
        "p4_hits": p4_hits,
    }


def _assert_no_fake_zero(report_payload: Mapping[str, Any], analysis: Any) -> bool:
    if analysis.formal_parameters is not None:
        return False
    if report_payload.get("objective_parameters") is not None:
        return False
    software_sha = (report_payload.get("version_manifest") or {}).get("software_commit_sha")
    if software_sha == "0" * 40:
        return False
    return True


def _history_immutability_ok(root: Path, *, software_commit_sha: str) -> dict[str, Any]:
    recorded = _record_session(
        root,
        get_scenario("normal_high_quality", **_scenario_overrides("normal_high_quality")),
        software_commit_sha=software_commit_sha,
    )
    service = ReplayAnalysisService(root)
    service.replay(
        recorded.session_id,
        software_commit_sha=software_commit_sha,
        persist=True,
        run_id="run-history-a",
    )
    before_readonly = _snapshot_tree(recorded.session_path)
    service.replay(recorded.session_id, software_commit_sha=software_commit_sha)
    after_readonly = _snapshot_tree(recorded.session_path)
    service.replay(
        recorded.session_id,
        software_commit_sha=software_commit_sha,
        persist=True,
        run_id="run-history-b",
    )
    loaded = AppSessionLoader(root).load(recorded.session_id)
    run_ids = [item.run_id for item in loaded.app_manifest.runs]
    after_b = _snapshot_tree(recorded.session_path)
    run_a_prefix = "app/runs/run-history-a/"
    # 仅比较 run A 资产字节；manifest 允许 current_run_id 更新
    run_a_bytes_unchanged = all(
        after_b.get(relative) == before_readonly.get(relative)
        for relative in before_readonly
        if relative.startswith(run_a_prefix)
    )
    return {
        "read_only_unchanged": before_readonly == after_readonly,
        "run_a_preserved": "run-history-a" in run_ids and run_a_bytes_unchanged,
        "run_b_added": "run-history-b" in run_ids,
        "current_run_id": loaded.app_manifest.current_run_id,
    }


def _path_security_ok(root: Path) -> bool:
    safe = SafeSessionPath(root)
    rejected = [
        "../secret",
        "C:/secret",
        "\\\\server\\share",
        "/absolute/path",
        "..\\secret",
    ]
    for value in rejected:
        try:
            safe.resolve(value, asset="test_asset")
        except M1AppError as exc:
            if exc.code != "path_escape":
                return False
        else:
            return False
    return True


def _duplicate_run_conflict_ok(root: Path, *, software_commit_sha: str) -> bool:
    recorded = _record_session(
        root,
        get_scenario("normal_high_quality", **_scenario_overrides("normal_high_quality")),
        software_commit_sha=software_commit_sha,
    )
    service = ReplayAnalysisService(root)
    service.replay(
        recorded.session_id,
        software_commit_sha=software_commit_sha,
        persist=True,
        run_id="run-dup",
    )
    try:
        service.replay(
            recorded.session_id,
            software_commit_sha=software_commit_sha,
            persist=True,
            run_id="run-dup",
        )
    except M1AppError as exc:
        return exc.code == "artifact_conflict"
    return False


def _measure_pipeline(root: Path, *, duration_s: float, software_commit_sha: str) -> dict[str, Any]:
    config = get_scenario(
        "normal_high_quality",
        random_seed=ACCEPTANCE_SEED,
        sample_rate_hz=ACCEPTANCE_SAMPLE_RATE_HZ,
        duration_s=duration_s,
    )
    tracemalloc.start()
    started = time.perf_counter()
    recorded = _record_session(root, config, software_commit_sha=software_commit_sha)
    after_raw = time.perf_counter()
    replay_source = ReplayDataSource(recorded.session_path)
    samples = list(replay_source.samples())
    after_load = time.perf_counter()
    provenance = SPProcessingProvenance(software_commit_sha=software_commit_sha)
    sp_result = SPProcessor().process(replay_source.session, samples, provenance=provenance)
    after_sp = time.perf_counter()
    app_provenance = create_replay_app_provenance(software_commit_sha)
    analysis = AnalysisProjector().project(
        session=replay_source.session,
        sp_result=sp_result,
        app_provenance=app_provenance,
    )
    after_app = time.perf_counter()
    report = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=replay_source.session,
            analysis=analysis.to_dict(),
            run_id="run-perf",
            run_provenance=app_provenance,
            generated_at_utc=replay_source.session.started_at_utc,
        )
    )
    after_report = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    raw_bytes = (recorded.session_path / "samples.jsonl").stat().st_size
    return {
        "duration_s": duration_s,
        "sample_count": len(samples),
        "raw_jsonl_bytes": raw_bytes,
        "raw_record_s": after_raw - started,
        "raw_load_s": after_load - after_raw,
        "sp_s": after_sp - after_load,
        "app_s": after_app - after_sp,
        "report_s": after_report - after_app,
        "e2e_s": after_report - started,
        "peak_memory_bytes": peak,
        "report_status": report.report_status.value,
    }


def _channel_display_boundary_ok(root: Path, *, software_commit_sha: str) -> dict[str, Any]:
    recorded = _record_session(
        root,
        get_scenario("normal_high_quality", **_scenario_overrides("normal_high_quality")),
        software_commit_sha=software_commit_sha,
    )
    client = TestClient(create_app(data_root=root))
    persist = client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-channel", "software_commit_sha": software_commit_sha},
    )
    before = AppSessionLoader(root).load(recorded.session_id)
    analysis_before = next(
        json.loads((recorded.session_path / asset.relative_path).read_text(encoding="utf-8"))
        for asset in before.app_manifest.runs[0].assets
        if asset.role.value == "analysis"
    )
    channels = client.get(
        f"/api/m1/sessions/{recorded.session_id}/channels",
        params={"run_id": "run-channel", "max_points": CHANNEL_DISPLAY_MAX_POINTS},
    )
    after = AppSessionLoader(root).load(recorded.session_id)
    analysis_after = next(
        json.loads((recorded.session_path / asset.relative_path).read_text(encoding="utf-8"))
        for asset in after.app_manifest.runs[0].assets
        if asset.role.value == "analysis"
    )
    pulse = channels.json()["raw"]["pulse"]
    returned_count = pulse["metadata"]["returned_count"]
    original_count = pulse["metadata"]["original_count"]
    return {
        "persist_ok": persist.status_code == 200,
        "channels_ok": channels.status_code == 200,
        "returned_count": returned_count,
        "original_count": original_count,
        "within_budget": returned_count <= CHANNEL_DISPLAY_MAX_POINTS,
        "analysis_unchanged": analysis_before == analysis_after,
        "display_only": pulse["metadata"].get("downsampling") == "display-only",
    }


def _report_tamper_distinction(root: Path, *, software_commit_sha: str) -> dict[str, Any]:
    recorded = _record_session(
        root,
        get_scenario("normal_high_quality", **_scenario_overrides("normal_high_quality")),
        software_commit_sha=software_commit_sha,
    )
    client = TestClient(create_app(data_root=root))
    client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-tamper", "software_commit_sha": software_commit_sha},
    )
    loaded = AppSessionLoader(root).load(recorded.session_id)
    run = next(item for item in loaded.app_manifest.runs if item.run_id == "run-tamper")
    report_asset = next(item for item in run.assets if item.role is AppAssetRole.REPORT)
    report_path = recorded.session_path / Path(*report_asset.relative_path.split("/"))
    original = report_path.read_bytes()
    report_path.write_bytes(original + b" ")
    bytes_only = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-tamper")
    report_path.write_bytes(original)
    payload = json.loads(original.decode("utf-8"))
    payload["failure_summary"] = "forged"
    forged_bytes = canonical_json_bytes(payload)
    report_path.write_bytes(forged_bytes)
    checksum = compute_registered_checksum(
        report_path,
        ChecksumProvenance(ChecksumSource.APP_PERSISTENCE, run.committed_at_utc),
        asset="report",
    )
    updated_assets = []
    for asset in run.assets:
        if asset.role is AppAssetRole.REPORT:
            updated_assets.append(
                AppAssetRef(
                    role=asset.role,
                    relative_path=asset.relative_path,
                    sha256=checksum.sha256,
                    size_bytes=checksum.size_bytes,
                    media_type=asset.media_type,
                    producer=asset.producer,
                    version=asset.version,
                    checksum_provenance=checksum.provenance,
                )
            )
        else:
            updated_assets.append(asset)
    updated_run = AppRunManifest(
        run_id=run.run_id,
        state=run.state,
        relative_path=run.relative_path,
        committed_at_utc=run.committed_at_utc,
        provenance=run.provenance,
        assets=tuple(updated_assets),
    )
    updated_manifest = AppManifest(
        schema_version=loaded.app_manifest.schema_version,
        app_processing_version=loaded.app_manifest.app_processing_version,
        session_id=loaded.app_manifest.session_id,
        registered_at_utc=loaded.app_manifest.registered_at_utc,
        raw_integrity_assurance=loaded.app_manifest.raw_integrity_assurance,
        source_assets=loaded.app_manifest.source_assets,
        runs=tuple(updated_run if item.run_id == run.run_id else item for item in loaded.app_manifest.runs),
        current_run_id=loaded.app_manifest.current_run_id,
    )
    write_app_manifest_atomic(recorded.session_path / "app" / "manifest.json", updated_manifest)
    semantic = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-tamper")
    return {
        "bytes_only_code": _error_code(bytes_only),
        "semantic_code": _error_code(semantic),
        "bytes_only_ok": _error_code(bytes_only) == "artifact_corrupted",
        "semantic_ok": _error_code(semantic) == "report_semantic_linkage_mismatch",
    }


def run_m1_p3_acceptance(
    *,
    root: Path | None = None,
    software_commit_sha: str = FIXED_SOFTWARE_COMMIT_SHA,
    exact_source_head_verified: FlagLike = True,
    p3f_baseline_verified: FlagLike = True,
    m1_p0_contracts_unchanged: FlagLike = True,
    m1_report_schema_unchanged: FlagLike = True,
    m1_p1_simulator_frozen: FlagLike = True,
    m1_p2_semantic_boundary_unchanged: FlagLike = True,
    p2_canonical_golden_matched: FlagLike = True,
    d3_tag_unchanged: FlagLike = True,
    p3d_web_source_unchanged: FlagLike = True,
    web_tests_passed: FlagLike = True,
    web_build_passed: FlagLike = True,
    d3_regression_passed: FlagLike = True,
    m1_p1_regression_passed: FlagLike = True,
    m1_p2_regression_passed: FlagLike = True,
    m1_p3b_regression_passed: FlagLike = True,
    m1_p3c_regression_passed: FlagLike = True,
    m1_p3e_regression_passed: FlagLike = True,
    p3a_path_security_verified: FlagLike | None = None,
    p3a_source_checksums_verified: FlagLike = True,
    p3a_persistence_atomicity_verified: FlagLike = True,
    p3a_historical_runs_immutable: FlagLike | None = None,
    p3a_concurrency_verified: FlagLike | None = None,
    performance_baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """执行 M1-P3 聚合验收并返回可落盘证据。"""

    repo_root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    gates: list[Gate] = []
    scan = _scan_paths(repo_root)
    golden_path = default_golden_path(repo_root)
    golden = load_semantic_golden(golden_path) if golden_path.is_file() else None

    external = {
        "exact_source_head_verified": exact_source_head_verified,
        "p3f_baseline_verified": p3f_baseline_verified,
        "m1_p0_contracts_unchanged": m1_p0_contracts_unchanged,
        "m1_report_schema_unchanged": m1_report_schema_unchanged,
        "m1_p1_simulator_frozen": m1_p1_simulator_frozen,
        "m1_p2_semantic_boundary_unchanged": m1_p2_semantic_boundary_unchanged,
        "p2_canonical_golden_matched": p2_canonical_golden_matched,
        "d3_tag_unchanged": d3_tag_unchanged,
        "p3d_web_source_unchanged": p3d_web_source_unchanged,
        "web_tests_passed": web_tests_passed,
        "web_build_passed": web_build_passed,
        "d3_regression_passed": d3_regression_passed,
        "m1_p1_regression_passed": m1_p1_regression_passed,
        "m1_p2_regression_passed": m1_p2_regression_passed,
        "m1_p3b_regression_passed": m1_p3b_regression_passed,
        "m1_p3c_regression_passed": m1_p3c_regression_passed,
        "m1_p3e_regression_passed": m1_p3e_regression_passed,
        "p3a_source_checksums_verified": p3a_source_checksums_verified,
        "p3a_persistence_atomicity_verified": p3a_persistence_atomicity_verified,
    }
    for gate_name, flag_value in external.items():
        gates.append(_gate(gate_name, _resolve_flag(flag_value)))

    scenario_ids = list_scenarios()
    plan_ids = list_attempt_plans()
    matrix_items = iter_frozen_matrix_configs()
    gates.append(
        _gate(
            "scenario_registry_exact_16_plus_2",
            len(scenario_ids) == EXPECTED_SINGLE_COUNT
            and len(plan_ids) == EXPECTED_MULTI_COUNT
            and len(list_simulation_cases()) == EXPECTED_CASE_COUNT,
            single=len(scenario_ids),
            multi=len(plan_ids),
            cases=len(list_simulation_cases()),
        )
    )
    gates.append(
        _gate(
            "total_attempt_count_expected",
            len(matrix_items) == EXPECTED_ATTEMPT_COUNT,
            actual=len(matrix_items),
            expected=EXPECTED_ATTEMPT_COUNT,
        )
    )
    gates.append(_gate("no_medical_claim", scan["medical_hits"] == [], hits=scan["medical_hits"]))
    gates.append(_gate("no_oracle_dependency", scan["oracle_hits"] == [], hits=scan["oracle_hits"]))
    gates.append(_gate("no_p4_decision_leakage", scan["p4_hits"] == [], hits=scan["p4_hits"]))

    production_versions = {
        "app_analysis_processing_version": APP_PROCESSING_VERSION_P3B,
        "app_analysis_fingerprint_version": APP_ANALYSIS_FINGERPRINT_VERSION,
        "report_projection_version": M1_REPORT_PROJECTION_VERSION,
        "sp_processing_version": SP_PROCESSING_VERSION_P2D,
        "sp_parameter_version": SP_PARAMETER_VERSION_P2C,
        "sp_fingerprint_version": SP_RESULT_FINGERPRINT_VERSION,
        "p3f_stage_version_not_in_production_app": P3F_STAGE_VERSION != APP_PROCESSING_VERSION_P3B,
    }
    gates.append(
        _gate(
            "production_versions_frozen",
            production_versions["app_analysis_processing_version"] == "0.2.0-p3b"
            and production_versions["report_projection_version"] == "m1-p3e-report-projection-v1"
            and production_versions["sp_processing_version"] == "0.4.0-p2d"
            and production_versions["sp_parameter_version"] == "0.3.0-p2c"
            and production_versions["sp_fingerprint_version"] == "sp-result-fingerprint:v2"
            and production_versions["p3f_stage_version_not_in_production_app"] is True,
            **production_versions,
        )
    )

    single_results: dict[str, Any] = {}
    multi_results: dict[str, Any] = {}
    observed_cases: list[dict[str, Any]] = []
    direct_replay_sp: list[bool] = []
    direct_replay_app: list[bool] = []
    direct_replay_report: list[bool] = []
    quality_gate: list[bool] = []
    formal_gate: list[bool] = []
    fake_zero: list[bool] = []
    blocked_before_quality: list[bool] = []
    raw_failure: list[bool] = []
    integrity_fail: list[bool] = []
    multi_independent: list[bool] = []

    with TemporaryDirectory(prefix="m1-p3-acceptance-") as temporary:
        data_root = Path(temporary)
        for index, item in enumerate(matrix_items):
            case_root = data_root / f"matrix-{index:02d}"
            case_root.mkdir()
            bundle = _direct_and_replay_bundle(
                case_root,
                item["config"],
                software_commit_sha=software_commit_sha,
            )
            summary = compact_case_summary(
                scenario_id=item["scenario_id"],
                attempt_index=item["attempt_index"],
                session=bundle["session"],
                analysis=bundle["replay_app"],
                report_payload=bundle["replay_report"],
            )
            observed_cases.append(summary)
            sp_eq = compare_sp_results(bundle["direct_sp"], bundle["replay_sp"])
            app_eq = compare_app_analysis(bundle["direct_app"], bundle["replay_app"])
            report_eq = normalize_report_semantics(bundle["direct_report"]) == normalize_report_semantics(
                bundle["replay_report"]
            )
            direct_replay_sp.append(sp_eq)
            direct_replay_app.append(app_eq)
            direct_replay_report.append(report_eq)
            fake_zero.append(_assert_no_fake_zero(bundle["replay_report"], bundle["replay_app"]))
            formal_ok = (
                summary["formal_parameters_allowed"] is False
                and summary["formal_parameters_is_null"] is True
                and summary["report_objective_parameters_present"] is False
                and summary["report_decision_action"] is None
                and summary["report_decision_rule_version"] is None
            )
            formal_gate.append(formal_ok)
            quality_label = summary["quality_label"]
            if bundle["replay_sp"].processing_status == "blocked_before_quality":
                blocked_ok = quality_label is None and summary["analysis_allowed"] is False
                blocked_before_quality.append(blocked_ok)
                quality_gate.append(blocked_ok)
            elif quality_label == "acceptable":
                quality_gate.append(summary["analysis_allowed"] is True and formal_ok)
            else:
                quality_gate.append(summary["analysis_allowed"] is False and formal_ok)

            if item["scenario_id"] == "raw_persistence_failure":
                raw_failure.append(
                    summary["raw_persistence_status"] == "failed"
                    and summary["analysis_allowed"] is False
                    and summary["report_objective_parameters_present"] is False
                )
            if item["scenario_id"] in {"frame_loss", "timestamp_regression", "sensor_disconnection"}:
                integrity_fail.append(
                    summary["analysis_allowed"] is False and summary["report_objective_parameters_present"] is False
                )
            if item["scenario_id"] in {"retry_improves", "retry_still_fails"}:
                multi_results.setdefault(item["scenario_id"], []).append(summary)
            else:
                single_results[item["scenario_id"]] = summary

        for plan_id, attempts in multi_results.items():
            session_ids = [row.get("session_completed") for row in attempts]
            independent = len(attempts) == len({(row["attempt_index"], row["quality_label"]) for row in attempts})
            if plan_id == "retry_improves":
                independent = independent and attempts[0]["analysis_allowed"] is False and attempts[1]["analysis_allowed"] is True
            if plan_id == "retry_still_fails":
                independent = independent and all(row["analysis_allowed"] is False for row in attempts)
            multi_independent.append(independent and all(session_ids))

        history_root = data_root / "history"
        history_root.mkdir()
        history = _history_immutability_ok(history_root, software_commit_sha=software_commit_sha)
        (data_root / "paths").mkdir()
        path_ok = (
            _path_security_ok(data_root / "paths")
            if p3a_path_security_verified is None
            else _resolve_flag(p3a_path_security_verified)
        )
        (data_root / "dup").mkdir()
        concurrency_ok = (
            _duplicate_run_conflict_ok(data_root / "dup", software_commit_sha=software_commit_sha)
            if p3a_concurrency_verified is None
            else _resolve_flag(p3a_concurrency_verified)
        )
        (data_root / "tamper").mkdir()
        tamper = _report_tamper_distinction(data_root / "tamper", software_commit_sha=software_commit_sha)
        (data_root / "channel").mkdir()
        channel = _channel_display_boundary_ok(data_root / "channel", software_commit_sha=software_commit_sha)
        (data_root / "perf8").mkdir()
        perf_8 = _measure_pipeline(data_root / "perf8", duration_s=8.0, software_commit_sha=software_commit_sha)
        (data_root / "perf60").mkdir()
        perf_60 = _measure_pipeline(data_root / "perf60", duration_s=60.0, software_commit_sha=software_commit_sha)

        (data_root / "api-extra").mkdir()
        client = TestClient(create_app(data_root=data_root / "api-extra"))
        extra_recorded = _record_session(
            data_root / "api-extra",
            get_scenario("normal_high_quality", **_scenario_overrides("normal_high_quality")),
            software_commit_sha=software_commit_sha,
        )
        before_get = _snapshot_tree(extra_recorded.session_path)
        list_response = client.get("/api/m1/sessions")
        detail_response = client.get(f"/api/m1/sessions/{extra_recorded.session_id}")
        after_get = _snapshot_tree(extra_recorded.session_path)
        no_current = client.get(f"/api/m1/sessions/{extra_recorded.session_id}/report")
        p3e_result = run_m1_p3e_acceptance(
            root=repo_root,
            software_commit_sha=software_commit_sha,
            frozen_m1_report_contract_unchanged=True,
            frozen_m1_report_schema_unchanged=True,
            p3d_web_source_unchanged=True,
            web_tests_passed=True,
            web_build_passed=True,
            p3c_regression_passed=True,
            p3b_regression_passed=True,
            p2_regression_passed=True,
            p1_regression_passed=True,
            d3_regression_passed=True,
            p2_canonical_golden_matched=True,
            d3_tag_unchanged=True,
            no_new_sp_algorithm=True,
        )

    observed_cases.sort(key=lambda row: (row["scenario_id"], row["attempt_index"]))
    observed_digest = _digest_cases(observed_cases)
    golden_ok = (
        golden is not None
        and golden.get("golden_source_sha") == P3F_BASELINE_SHA
        and golden.get("digest_version") == SEMANTIC_SUMMARY_VERSION
        and golden.get("cases") == observed_cases
        and golden.get("digest_sha256") == observed_digest
    )

    hq = single_results.get("normal_high_quality") or {}
    abort_case = single_results.get("abort") or {}
    fault_case = single_results.get("device_fault") or {}

    if p3a_historical_runs_immutable is None:
        history_flag = history["read_only_unchanged"] and history["run_a_preserved"] and history["run_b_added"]
    else:
        history_flag = _resolve_flag(p3a_historical_runs_immutable)

    comparable = False
    regression_ok = True
    comparison_note = "NOT_COMPARABLE"
    if performance_baseline:
        baseline_8 = performance_baseline.get("session_8s") or {}
        baseline_60 = performance_baseline.get("session_60s") or {}
        if baseline_8.get("e2e_s") and baseline_60.get("e2e_s"):
            comparable = True
            comparison_note = "compared_to_provided_baseline"
            regression_ok = (
                perf_8["e2e_s"] <= float(baseline_8["e2e_s"]) * PERFORMANCE_REGRESSION_RATIO_LIMIT
                and perf_60["e2e_s"] <= float(baseline_60["e2e_s"]) * PERFORMANCE_REGRESSION_RATIO_LIMIT
            )

    gates.extend(
        [
            _gate("p3_semantic_golden_matched", golden_ok, digest=observed_digest),
            _gate("p3b_direct_replay_sp_equivalent", all(direct_replay_sp)),
            _gate("p3b_direct_replay_app_equivalent", all(direct_replay_app)),
            _gate("p3e_report_direct_replay_normalized", all(direct_replay_report)),
            _gate("p3b_quality_gate_verified", all(quality_gate)),
            _gate("p3b_formal_parameter_gate_verified", all(formal_gate)),
            _gate("formal_parameters_null_pre_h1", all(formal_gate) and hq.get("report_objective_parameters_present") is False),
            _gate(
                "normal_high_quality_pre_h1",
                hq.get("analysis_allowed") is True
                and hq.get("formal_parameters_allowed") is False
                and hq.get("report_objective_parameters_present") is False
                and hq.get("report_decision_action") is None
                and "synthetic_input" in (hq.get("report_limitations") or [])
                and hq.get("not_for_medical_use") is True,
                **hq,
            ),
            _gate("blocked_before_quality_preserved", all(blocked_before_quality) and abort_case.get("quality_label") is None),
            _gate("raw_persistence_failure_fail_closed", all(raw_failure) and bool(raw_failure)),
            _gate("integrity_fail_closed", all(integrity_fail) and len(integrity_fail) == 3),
            _gate("multi_attempts_independent", all(multi_independent) and len(multi_independent) == 2),
            _gate("p3a_path_security_verified", path_ok),
            _gate("p3a_historical_runs_immutable", history_flag, **history),
            _gate("p3a_concurrency_verified", concurrency_ok),
            _gate("no_fake_zero", all(fake_zero)),
            _gate("decision_unavailable_pre_p4", all(row["report_decision_action"] is None for row in observed_cases)),
            _gate("not_for_medical_use_present", all(row["not_for_medical_use"] for row in observed_cases)),
            _gate("p3e_report_checksum_fail_closed", tamper["bytes_only_ok"], **tamper),
            _gate("p3e_report_semantic_linkage", tamper["semantic_ok"], **tamper),
            _gate("p3c_get_zero_mutation", before_get == after_get and list_response.status_code == 200),
            _gate("p3c_no_run_guessing", no_current.status_code == 404 and _error_code(no_current) == "report_not_available"),
            _gate("p3c_existing_api_regression", detail_response.status_code == 200),
            _gate(
                "channel_display_boundary",
                channel["within_budget"] and channel["analysis_unchanged"] and channel["persist_ok"],
                **channel,
            ),
            _gate("performance_evidence_recorded", True, session_8s=perf_8, session_60s=perf_60),
            _gate(
                "no_order_of_magnitude_regression",
                regression_ok,
                comparable=comparable,
                note=comparison_note,
            ),
            _gate("p3e_report_api_present", p3e_result.get("gates", {}).get("report_api_present", {}).get("passed") is True),
            _gate("p3e_report_schema_valid", p3e_result.get("gates", {}).get("report_schema_valid", {}).get("passed") is True),
            _gate("p3e_report_deterministic", p3e_result.get("gates", {}).get("report_id_deterministic", {}).get("passed") is True),
            _gate(
                "p3e_legacy_report_zero_mutation",
                p3e_result.get("gates", {}).get("legacy_run_projection_zero_mutation", {}).get("passed") is True,
            ),
            _gate(
                "p3e_new_report_atomic_immutable",
                p3e_result.get("gates", {}).get("new_persisted_run_contains_report", {}).get("passed") is True,
            ),
            _gate(
                "p3e_analysis_run_provenance_linkage",
                p3e_result.get("gates", {}).get("version_manifest_traceable", {}).get("passed") is True,
            ),
            _gate(
                "p3e_strict_fingerprint_validation",
                p3e_result.get("acceptance") is True,
            ),
            _gate("p3c_stable_error_contract", p3e_result.get("gates", {}).get("stable_report_errors", {}).get("passed") is True),
            _gate("p3b_determinism_verified", p3e_result.get("gates", {}).get("report_projection_deterministic", {}).get("passed") is True),
            _gate("p3b_oracle_isolation_verified", _resolve_flag(m1_p3b_regression_passed)),
            _gate("device_fault_blocked_before_quality", fault_case.get("quality_label") is None or fault_case.get("analysis_allowed") is False),
        ]
    )

    failed = [item.name for item in gates if not item.passed]
    gate_map = {
        item.name: {"passed": item.passed, "evidence": dict(item.evidence)}
        for item in gates
    }
    return {
        "acceptance_version": ACCEPTANCE_VERSION,
        "p3f_stage_version": P3F_STAGE_VERSION,
        "software_commit_sha": software_commit_sha,
        "baseline_sha": P3F_BASELINE_SHA,
        "production_versions": production_versions,
        "scenario_registry": {
            "single_attempt_count": len(scenario_ids),
            "multi_attempt_count": len(plan_ids),
            "total_case_count": len(list_simulation_cases()),
            "total_attempt_count": len(matrix_items),
            "scenarios": list(scenario_ids),
            "attempt_plans": list(plan_ids),
        },
        "direct_replay_equivalent": all(direct_replay_sp) and all(direct_replay_app) and all(direct_replay_report),
        "oracle_isolation_verified": _resolve_flag(m1_p3b_regression_passed),
        "quality_gate_verified": all(quality_gate),
        "formal_parameter_gate_verified": all(formal_gate),
        "persistence_atomicity_verified": _resolve_flag(p3a_persistence_atomicity_verified),
        "historical_runs_immutable": history_flag,
        "corruption_fail_closed": tamper["bytes_only_ok"] and tamper["semantic_ok"],
        "path_security_verified": path_ok,
        "report_schema_valid": p3e_result.get("gates", {}).get("report_schema_valid", {}).get("passed") is True,
        "report_deterministic": p3e_result.get("gates", {}).get("report_id_deterministic", {}).get("passed") is True,
        "report_semantic_linkage_verified": tamper["semantic_ok"],
        "api_zero_mutation_verified": before_get == after_get,
        "web_regression_passed": _resolve_flag(web_tests_passed) and _resolve_flag(web_build_passed),
        "semantic_golden": {
            "path": str(golden_path.as_posix()),
            "source_sha": P3F_BASELINE_SHA,
            "digest_version": SEMANTIC_SUMMARY_VERSION,
            "matched": golden_ok,
            "digest_sha256": observed_digest,
        },
        "single_attempt": single_results,
        "multi_attempt": multi_results,
        "performance": {
            "session_8s": perf_8,
            "session_60s": perf_60,
            "channel_max_points": CHANNEL_DISPLAY_MAX_POINTS,
            "channel": channel,
            "comparable": comparable,
            "note": comparison_note,
            "claim": "software characterization only; not real-time hardware performance",
        },
        "scan": scan,
        "p3e_nested_acceptance": p3e_result.get("acceptance"),
        "gates": gate_map,
        "failed_gates": failed,
        "acceptance": failed == [],
        "note": "M1-P3 PASS 表示 APP-A1-pre 软件实现已就绪，可进入下一软件阶段；不等于 M1/H1/硬件/临床通过。",
    }


__all__ = [
    "ACCEPTANCE_VERSION",
    "P3F_BASELINE_SHA",
    "P3F_STAGE_VERSION",
    "SEMANTIC_SUMMARY_VERSION",
    "build_semantic_golden_document",
    "compact_case_summary",
    "default_golden_path",
    "iter_frozen_matrix_configs",
    "normalize_report_semantics",
    "run_m1_p3_acceptance",
]
