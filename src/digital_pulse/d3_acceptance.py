"""Pure D3 acceptance gate, evidence-source, and T01–T24 traceability helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


REQUIRED_TRACE_IDS = tuple(f"D3-T{i:02d}" for i in range(1, 25))


@dataclass(frozen=True, slots=True)
class TraceItem:
    id: str
    title: str
    node_ids: tuple[str, ...]
    requires_web_build: bool = False
    uses_full_pytest_suite: bool = False


# Concrete pytest node IDs. Titles must not over-claim coverage.
TRACEABILITY_MATRIX: tuple[TraceItem, ...] = (
    TraceItem(
        "D3-T01",
        "合法/非法模型配置",
        ("tests/test_d3_contracts.py::D3ContractTests::test_plant_rejects_non_finite_and_invalid_geometry",),
    ),
    TraceItem(
        "D3-T02",
        "无接触开环运动",
        ("tests/test_d3_plant.py::D3PlantTests::test_free_motion_before_contact_has_zero_force",),
    ),
    TraceItem(
        "D3-T03",
        "接触和卸载",
        ("tests/test_d3_plant.py::D3PlantTests::test_unloading_returns_to_lower_limit_and_zero_force",),
    ),
    TraceItem(
        "D3-T04",
        "顺应性参数组",
        ("tests/test_d3_plant.py::D3PlantTests::test_higher_stiffness_produces_higher_force_at_same_position",),
    ),
    TraceItem(
        "D3-T05",
        "正常单阶跃稳态判据",
        ("tests/test_d3_controller.py::test_stable_requires_continuous_minimum_duration",),
    ),
    TraceItem(
        "D3-T06",
        "多目标Profile",
        ("tests/test_d3_integration.py::test_normal_multi_target_profile_meets_model_thresholds",),
    ),
    TraceItem(
        "D3-T07",
        "输出饱和",
        ("tests/test_d3_controller.py::test_output_is_symmetric_and_bounded",),
    ),
    TraceItem(
        "D3-T08",
        "饱和后反向目标",
        ("tests/test_d3_controller.py::test_error_reversal_recovers_from_positive_saturation",),
    ),
    TraceItem(
        "D3-T09",
        "稳态失效",
        ("tests/test_d3_state_machine.py::test_unstable_acquisition_returns_to_stabilize",),
    ),
    TraceItem(
        "D3-T10",
        "ABORT完整卸载",
        (
            "tests/test_d3_closed_loop.py::test_abort_unload_does_not_depend_on_web_or_api",
            "tests/test_d3_api.py::test_d3_runtime_abort_enters_retract_and_unloads",
        ),
    ),
    TraceItem(
        "D3-T11",
        "急停零输出锁存",
        ("tests/test_d3_closed_loop.py::test_emergency_stop_zero_output_and_latches",),
    ),
    TraceItem(
        "D3-T12",
        "载荷硬超限卸载",
        (
            "tests/test_d3_safety.py::test_hard_overload_retracts_when_position_is_valid",
            "tests/test_d3_closed_loop.py::test_retractable_faults_finish_unload_or_timeout_is_failure",
        ),
    ),
    TraceItem(
        "D3-T13",
        "上/下限位",
        (
            "tests/test_d3_closed_loop.py::test_upper_limit_blocks_further_compression",
            "tests/test_d3_safety.py::test_lower_limit_blocks_retraction",
        ),
    ),
    TraceItem(
        "D3-T14",
        "限位冲突",
        ("tests/test_d3_safety.py::test_limit_conflict_latches",),
    ),
    TraceItem(
        "D3-T15",
        "载荷传感器无效与观测冻结",
        (
            "tests/test_d3_safety.py::test_force_sensor_invalid_never_continues_compression",
            "tests/test_d3_plant.py::D3PlantTests::test_freeze_holds_observation_while_true_state_moves",
        ),
    ),
    TraceItem(
        "D3-T16",
        "位置传感器断线",
        ("tests/test_d3_safety.py::test_position_sensor_invalid_zeroes_and_latches",),
    ),
    TraceItem(
        "D3-T17",
        "电机卡滞锁存",
        ("tests/test_d3_closed_loop.py::test_latch_faults_stay_latched",),
    ),
    TraceItem(
        "D3-T18",
        "上位机失联退回",
        (
            "tests/test_d3_safety.py::test_host_timeout_is_device_side_retract",
            "tests/test_d3_closed_loop.py::test_retractable_faults_finish_unload_or_timeout_is_failure",
        ),
    ),
    TraceItem(
        "D3-T19",
        "看门狗超时锁存",
        (
            "tests/test_d3_safety.py::test_watchdog_failure_zeroes_and_latches",
            "tests/test_d3_closed_loop.py::test_latch_faults_stay_latched",
        ),
    ),
    TraceItem(
        "D3-T20",
        "同tick多故障优先级",
        ("tests/test_d3_fault_matrix.py::test_same_tick_multi_fault_uses_frozen_priority_and_records_all",),
    ),
    TraceItem(
        "D3-T21",
        "相同输入重放",
        ("tests/test_d3_closed_loop.py::test_same_seed_and_schedule_is_deterministic",),
    ),
    TraceItem(
        "D3-T22",
        "30分钟完整链路模型时间",
        ("tests/test_d3_integration.py::test_full_chain_30_minute_model_time",),
    ),
    TraceItem(
        "D3-T23",
        "D0–D2回归（全量pytest）",
        (),
        uses_full_pytest_suite=True,
    ),
    TraceItem(
        "D3-T24",
        "API/Web",
        (
            "tests/test_d3_api.py::test_d3_runtime_create_and_query",
            "tests/test_d3_api.py::test_d3_runtime_abort_enters_retract_and_unloads",
        ),
        requires_web_build=True,
    ),
)


def mapped_node_ids(matrix: tuple[TraceItem, ...] = TRACEABILITY_MATRIX) -> list[str]:
    nodes: list[str] = []
    seen: set[str] = set()
    for item in matrix:
        for node in item.node_ids:
            if node not in seen:
                seen.add(node)
                nodes.append(node)
    return nodes


def resolve_evidence_source(
    env: Mapping[str, str],
    *,
    requested: str | None = None,
) -> str:
    """Auto-detect evidence source. Local cannot forge github-actions."""
    is_gha = env.get("GITHUB_ACTIONS") == "true"
    if requested == "github-actions" and not is_gha:
        raise ValueError("cannot set evidence_source=github-actions outside GitHub Actions")
    if requested is not None and requested not in {"local", "github-actions"}:
        raise ValueError(f"unsupported evidence_source: {requested}")
    if is_gha:
        return "github-actions"
    return "local"


def github_actions_metadata(env: Mapping[str, str]) -> dict[str, str]:
    keys = (
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_WORKFLOW",
        "GITHUB_JOB",
        "GITHUB_SHA",
        "GITHUB_REF",
        "GITHUB_EVENT_NAME",
    )
    return {key: env[key] for key in keys if env.get(key)}


def ci_sha_matches_head(env: Mapping[str, str], head_sha: str) -> bool:
    if env.get("GITHUB_ACTIONS") != "true":
        return True
    github_sha = env.get("GITHUB_SHA", "")
    if not github_sha or not head_sha:
        return False
    return github_sha == head_sha


def evaluate_acceptance_gates(
    *,
    workspace_clean: bool,
    skip_web: bool,
    skip_unittest: bool,
    pytest_passed: bool,
    unittest_passed: bool,
    web_build_passed: bool,
    normal_profile_passed: bool,
    closed_loop_matrix_passed: bool,
    abort_runtime_passed: bool,
    full_chain_1800s_passed: bool,
    traceability_passed: bool,
    config_hashes_present: bool,
    evidence_source_valid: bool = True,
    ci_sha_matches: bool = True,
) -> tuple[dict[str, bool], list[str], bool]:
    """Compute formal acceptance only after all gates are known."""
    gates = {
        "workspace_clean": bool(workspace_clean),
        "pytest_passed": bool(pytest_passed),
        "unittest_passed": (not skip_unittest) and bool(unittest_passed),
        "web_build_passed": (not skip_web) and bool(web_build_passed),
        "normal_profile_passed": bool(normal_profile_passed),
        "closed_loop_matrix_passed": bool(closed_loop_matrix_passed),
        "abort_runtime_passed": bool(abort_runtime_passed),
        "full_chain_1800s_passed": bool(full_chain_1800s_passed),
        "traceability_passed": bool(traceability_passed),
        "config_hashes_present": bool(config_hashes_present),
        "evidence_source_valid": bool(evidence_source_valid),
        "ci_sha_matches": bool(ci_sha_matches),
        "no_skip_web": not skip_web,
        "no_skip_unittest": not skip_unittest,
    }
    failed = [name for name, ok in gates.items() if not ok]
    formal = all(gates.values())
    return gates, failed, formal


def evaluate_trace_item(
    item: TraceItem,
    *,
    node_results: Mapping[str, dict[str, Any]],
    full_pytest_passed: bool,
    web_build_passed: bool,
) -> dict[str, Any]:
    """Evaluate one T-item from concrete node results (not suite-level alone)."""
    if item.uses_full_pytest_suite:
        passed = bool(full_pytest_passed)
        return {
            "id": item.id,
            "title": item.title,
            "node_ids": [],
            "collected": True,
            "executed": True,
            "passed": passed,
            "result": "passed" if passed else "failed",
            "evidence": {"kind": "full_pytest_suite", "passed": passed},
        }

    details = []
    collected = True
    executed = True
    passed = True
    for node in item.node_ids:
        info = node_results.get(node)
        if info is None:
            collected = False
            executed = False
            passed = False
            details.append({"node_id": node, "collected": False, "executed": False, "passed": False})
            continue
        node_collected = bool(info.get("collected", False))
        node_executed = bool(info.get("executed", False))
        node_passed = bool(info.get("passed", False))
        collected = collected and node_collected
        executed = executed and node_executed
        passed = passed and node_collected and node_executed and node_passed
        details.append({
            "node_id": node,
            "collected": node_collected,
            "executed": node_executed,
            "passed": node_passed,
        })

    if item.requires_web_build:
        passed = passed and bool(web_build_passed)
        details.append({"web_build_passed": bool(web_build_passed)})

    if not item.node_ids:
        passed = False
        collected = False
        executed = False

    return {
        "id": item.id,
        "title": item.title,
        "node_ids": list(item.node_ids),
        "collected": collected,
        "executed": executed,
        "passed": passed,
        "result": "passed" if passed else "failed",
        "evidence": {"nodes": details},
    }


def evaluate_traceability(
    matrix: tuple[TraceItem, ...] = TRACEABILITY_MATRIX,
    *,
    node_results: Mapping[str, dict[str, Any]],
    full_pytest_passed: bool,
    web_build_passed: bool,
) -> tuple[list[dict[str, Any]], bool]:
    ids = [item.id for item in matrix]
    if tuple(ids) != REQUIRED_TRACE_IDS:
        rows = [
            {
                "id": tid,
                "title": "missing",
                "node_ids": [],
                "collected": False,
                "executed": False,
                "passed": False,
                "result": "failed",
                "evidence": {"reason": "trace matrix incomplete or misordered"},
            }
            for tid in REQUIRED_TRACE_IDS
        ]
        return rows, False
    rows = [
        evaluate_trace_item(
            item,
            node_results=node_results,
            full_pytest_passed=full_pytest_passed,
            web_build_passed=web_build_passed,
        )
        for item in matrix
    ]
    return rows, all(row["passed"] for row in rows)


def config_hashes_are_present(section: Mapping[str, Any] | None) -> bool:
    if not section:
        return False
    hashes = section.get("config_hashes") if isinstance(section, Mapping) else None
    if not isinstance(hashes, Mapping):
        return False
    required = (
        "plant_sha256",
        "controller_sha256",
        "safety_sha256",
        "timing_sha256",
        "profile_acceptance_sha256",
        "combined_sha256",
    )
    return all(isinstance(hashes.get(key), str) and len(hashes[key]) == 64 for key in required)


def abort_runtime_passed(snapshot: Mapping[str, Any] | None) -> bool:
    if not snapshot:
        return False
    report = snapshot.get("report") if isinstance(snapshot.get("report"), Mapping) else {}
    return bool(
        snapshot.get("status") == "ABORTED_IDLE"
        and snapshot.get("state") == "IDLE"
        and snapshot.get("unload_complete") is True
        and report.get("positive_command_after_abort") is False
        and float(report.get("max_command_after_abort") or 0.0) <= 1e-12
    )
