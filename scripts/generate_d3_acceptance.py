#!/usr/bin/env python3
"""Generate D3 closeout acceptance evidence from real executions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "d3-acceptance"


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    raw = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        capture_output=True,
        check=False,
    )
    stdout = raw.stdout.decode("utf-8", errors="replace")
    stderr = raw.stderr.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(raw.args, raw.returncode, stdout, stderr)


def _git(*args: str) -> str:
    result = _run(["git", *args])
    out = result.stdout or ""
    return out.strip() if result.returncode == 0 else ""


def _parse_pytest(output: str) -> dict:
    # e.g. "150 passed in 12.34s" or "2 failed, 148 passed in ..."
    summary = {
        "passed": None,
        "failed": None,
        "skipped": None,
        "raw": None,
        "ok": False,
    }
    match = re.search(
        r"(?:(\d+) failed,\s*)?(?:(\d+) passed)(?:,\s*(\d+) skipped)?(?: in [0-9.]+s)?",
        output,
    )
    if not match:
        # also handle "===== 150 passed in 1.23s ====="
        match = re.search(r"(\d+) passed(?:,\s*(\d+) skipped)?", output)
        if match:
            summary["passed"] = int(match.group(1))
            summary["failed"] = 0
            summary["skipped"] = int(match.group(2) or 0)
            summary["raw"] = match.group(0)
            summary["ok"] = True
            return summary
        summary["raw"] = output[-500:]
        return summary
    summary["failed"] = int(match.group(1) or 0)
    summary["passed"] = int(match.group(2) or 0)
    summary["skipped"] = int(match.group(3) or 0)
    summary["raw"] = match.group(0)
    summary["ok"] = summary["failed"] == 0 and summary["passed"] is not None
    return summary


def _parse_unittest(output: str) -> dict:
    match = re.search(r"Ran (\d+) tests? in", output)
    ok = "OK" in output.splitlines()[-5:]
    failures = re.search(r"FAILED \(.*failures=(\d+)", output)
    errors = re.search(r"errors=(\d+)", output)
    return {
        "ran": int(match.group(1)) if match else None,
        "ok": ok and failures is None and errors is None,
        "failures": int(failures.group(1)) if failures else 0,
        "errors": int(errors.group(1)) if errors else 0,
        "raw_tail": "\n".join(output.splitlines()[-8:]),
    }


T_MATRIX = [
    ("D3-T01", "合法/非法模型配置", "tests/test_d3_contracts.py", "test_plant_rejects_non_finite_and_invalid_geometry"),
    ("D3-T02", "无接触开环运动", "tests/test_d3_plant.py", "test_free_motion_before_contact_has_zero_force"),
    ("D3-T03", "接触和卸载", "tests/test_d3_plant.py", "test_unloading_returns_to_lower_limit_and_zero_force"),
    ("D3-T04", "顺应性参数组", "tests/test_d3_plant.py", "test_higher_stiffness_produces_higher_force_at_same_position"),
    ("D3-T05", "正常单阶跃", "tests/test_d3_controller.py", "test_stable_requires_continuous_minimum_duration"),
    ("D3-T06", "多目标Profile", "tests/test_d3_integration.py", "test_normal_multi_target_profile_meets_model_thresholds"),
    ("D3-T07", "输出饱和", "tests/test_d3_controller.py", "test_output_is_symmetric_and_bounded"),
    ("D3-T08", "饱和后反向目标", "tests/test_d3_controller.py", "test_error_reversal_recovers_from_positive_saturation"),
    ("D3-T09", "稳态失效", "tests/test_d3_state_machine.py", "test_unstable_acquisition_returns_to_stabilize"),
    ("D3-T10", "ABORT", "tests/test_d3_closed_loop.py", "test_abort_unload_does_not_depend_on_web_or_api"),
    ("D3-T11", "急停", "tests/test_d3_closed_loop.py", "test_emergency_stop_zero_output_and_latches"),
    ("D3-T12", "载荷硬超限", "tests/test_d3_closed_loop.py", "test_retractable_faults_finish_unload_or_timeout_is_failure"),
    ("D3-T13", "上/下限位", "tests/test_d3_closed_loop.py", "test_upper_limit_blocks_further_compression"),
    ("D3-T14", "限位冲突", "tests/test_d3_safety.py", "test_limit_conflict_latches"),
    ("D3-T15", "载荷传感器断线/冻结", "tests/test_d3_closed_loop.py", "test_retractable_faults_finish_unload_or_timeout_is_failure"),
    ("D3-T16", "位置传感器断线", "tests/test_d3_safety.py", "test_position_sensor_invalid_zeroes_and_latches"),
    ("D3-T17", "电机卡滞", "tests/test_d3_closed_loop.py", "test_latch_faults_stay_latched"),
    ("D3-T18", "上位机失联", "tests/test_d3_closed_loop.py", "test_retractable_faults_finish_unload_or_timeout_is_failure"),
    ("D3-T19", "看门狗超时", "tests/test_d3_closed_loop.py", "test_latch_faults_stay_latched"),
    ("D3-T20", "同tick多故障", "tests/test_d3_fault_matrix.py", "test_same_tick_multi_fault_uses_frozen_priority_and_records_all"),
    ("D3-T21", "相同输入重放", "tests/test_d3_closed_loop.py", "test_same_seed_and_schedule_is_deterministic"),
    ("D3-T22", "30分钟模型时间", "tests/test_d3_integration.py", "test_full_chain_30_minute_model_time"),
    ("D3-T23", "D0–D2回归", "tests/", "full pytest suite"),
    ("D3-T24", "API/Web", "tests/test_d3_api.py + web build", "runtime abort + npm run build"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-source",
        choices=("local", "github-actions"),
        default="local",
    )
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-unittest", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    # Formal acceptance requires a clean workspace (draft allowed when dirty).
    formal = not dirty

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.d3_closed_loop import run_closed_loop_matrix
    from digital_pulse.d3_integration import run_full_chain_long_hold, run_normal_profile
    from digital_pulse.d3_runtime import D3RuntimeRegistry

    normal = run_normal_profile()
    closed = run_closed_loop_matrix()
    long_hold = run_full_chain_long_hold(duration_s=1800.0, seed=20260805)

    # ABORT closed-loop via runtime (device SM), not Web-local state.
    registry = D3RuntimeRegistry()
    session = registry.create(
        targets=(40.0,),
        seed=20260805,
        hold=True,
        max_duration_s=40.0,
    )
    session.wait_until(lambda s: s["state"] == "ACQUIRE", timeout_s=10.0)
    session.request_abort()
    abort_final = session.wait_until(
        lambda s: s["status"] == "ABORTED_IDLE" or s["state"] == "IDLE",
        timeout_s=10.0,
    )
    session.join(timeout_s=5.0)

    pytest_proc = _run([sys.executable, "-m", "pytest", "-q"])
    pytest_summary = _parse_pytest(pytest_proc.stdout + "\n" + pytest_proc.stderr)
    pytest_summary["returncode"] = pytest_proc.returncode
    pytest_summary["command"] = "python -m pytest -q"

    unittest_summary = {"skipped": True}
    if not args.skip_unittest:
        ut = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        unittest_summary = _parse_unittest(ut.stdout + "\n" + ut.stderr)
        unittest_summary["returncode"] = ut.returncode
        unittest_summary["command"] = "python -m unittest discover -s tests -v"

    web_summary = {"skipped": True}
    if not args.skip_web:
        npm_ci = _run(["npm", "ci"], cwd=ROOT / "web")
        npm_build = _run(["npm", "run", "build"], cwd=ROOT / "web")
        web_summary = {
            "skipped": False,
            "npm_ci_returncode": npm_ci.returncode,
            "npm_build_returncode": npm_build.returncode,
            "ok": npm_ci.returncode == 0 and npm_build.returncode == 0,
            "command": "cd web && npm ci && npm run build",
            "build_tail": "\n".join((npm_build.stdout + npm_build.stderr).splitlines()[-20:]),
        }

    try:
        node = _run(["node", "--version"])
    except FileNotFoundError:
        node = subprocess.CompletedProcess(["node", "--version"], 127, "", "node not found")
    try:
        npm = _run(["npm", "--version"])
    except FileNotFoundError:
        npm = subprocess.CompletedProcess(["npm", "--version"], 127, "", "npm not found")

    closed_by_id = {item["case_id"]: item for item in closed["results"]}
    t_results = []
    for tid, title, path, fn in T_MATRIX:
        status = "mapped"
        evidence = path
        if tid == "D3-T22":
            ok = (
                long_hold.get("finite")
                and long_hold.get("integral_bounded")
                and long_hold.get("no_false_host_timeout")
                and long_hold.get("duration_s") == 1800.0
            )
            status = "passed" if ok else "failed"
            evidence = "run_full_chain_long_hold + test_full_chain_30_minute_model_time"
        elif tid == "D3-T10":
            status = "passed" if abort_final.get("unload_complete") and abort_final.get("state") == "IDLE" else "failed"
            evidence = "D3RuntimeSession abort + test_d3_api/test_d3_closed_loop"
        elif tid == "D3-T23":
            status = "passed" if pytest_summary.get("ok") else "failed"
        elif tid == "D3-T24":
            api_ok = abort_final.get("status") == "ABORTED_IDLE"
            web_ok = web_summary.get("ok", False) if not web_summary.get("skipped") else None
            if web_ok is None:
                status = "passed_api_web_skipped" if api_ok else "failed"
            else:
                status = "passed" if api_ok and web_ok else "failed"
        elif tid in {"D3-T12", "D3-T15", "D3-T18"}:
            key = {"D3-T12": "hard-overload", "D3-T15": "force-sensor", "D3-T18": "host-timeout"}[tid]
            status = "passed" if closed_by_id[key]["passed"] else "failed"
            evidence = f"closed_loop:{key}"
        else:
            # Covered by named pytest function; overall suite result gates pass.
            status = "passed" if pytest_summary.get("ok") else "unverified_suite_failed"
        t_results.append({
            "id": tid,
            "title": title,
            "test_file": path,
            "test_function": fn,
            "result": status,
            "evidence": evidence,
        })

    payload = {
        "schema_version": "1.0.0",
        "generated_at_utc": generated_at,
        "git_commit_sha": commit,
        "workspace_clean": not dirty,
        "formal_acceptance": formal,
        "evidence_source": args.evidence_source,
        "python_version": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "node_version": node.stdout.strip() if node.returncode == 0 else None,
        "npm_version": npm.stdout.strip() if npm.returncode == 0 else None,
        "d3_config": {
            "seed": 20260805,
            "units": "*_au synthetic relative units",
            "control_period_us": 10_000,
        },
        "normal_profile": {
            "completed": normal.get("completed"),
            "final_state": normal.get("final_state"),
            "metrics": normal.get("metrics"),
            "sha256": normal.get("sha256"),
            "all_metrics_passed": normal.get("all_metrics_passed"),
        },
        "closed_loop_unload": {
            "summary": closed.get("summary"),
            "report_sha256": closed.get("report_sha256"),
            "results": closed.get("results"),
        },
        "abort_runtime_closed_loop": {
            "status": abort_final.get("status"),
            "state": abort_final.get("state"),
            "unload_complete": abort_final.get("unload_complete"),
            "report": abort_final.get("report"),
        },
        "full_chain_30min": {
            "duration_s": long_hold.get("duration_s"),
            "modules": long_hold.get("modules"),
            "finite": long_hold.get("finite"),
            "integral_bounded": long_hold.get("integral_bounded"),
            "final_force_error_au": long_hold.get("final_force_error_au"),
            "final_state": long_hold.get("final_state"),
            "no_false_host_timeout": long_hold.get("no_false_host_timeout"),
            "no_false_watchdog": long_hold.get("no_false_watchdog"),
            "report_sha256": long_hold.get("report_sha256"),
        },
        "pytest": pytest_summary,
        "unittest": unittest_summary,
        "web_build": web_summary,
        "traceability_D3_T01_T24": t_results,
        "limitations": [
            "All D3 values use synthetic relative units (*_au).",
            "This evidence is not hardware performance, human safety, or clinical validity.",
            "Emergency stop does not claim active retract capability.",
            "Real sensor/actuator unknowns remain for H1.",
        ],
        "disclaimer": (
            "Generated only from commands executed in this process. "
            "Local evidence is not GitHub Actions CI unless evidence_source=github-actions."
        ),
    }

    json_path = OUT_DIR / "acceptance.json"
    md_path = OUT_DIR / "acceptance.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# D3 Acceptance Evidence",
        "",
        f"- Generated (UTC): `{generated_at}`",
        f"- Git commit: `{commit}`",
        f"- Workspace clean: `{not dirty}`",
        f"- Formal acceptance: `{formal}`",
        f"- Evidence source: `{args.evidence_source}`",
        f"- Python: `{platform.python_version()}`",
        f"- Node: `{payload['node_version']}`",
        f"- npm: `{payload['npm_version']}`",
        "",
        "## Test commands",
        "",
        f"- `{pytest_summary.get('command')}` → `{pytest_summary.get('raw')}` (rc={pytest_summary.get('returncode')})",
        f"- unittest rc={unittest_summary.get('returncode')} ran={unittest_summary.get('ran')}",
        f"- web ok={web_summary.get('ok')} skipped={web_summary.get('skipped')}",
        "",
        "## Key results",
        "",
        f"- Normal profile completed: `{normal.get('completed')}` sha=`{normal.get('sha256')}`",
        f"- Closed-loop unload all_passed: `{closed['summary']['all_passed']}` sha=`{closed.get('report_sha256')}`",
        f"- ABORT runtime: status=`{abort_final.get('status')}` unload=`{abort_final.get('unload_complete')}`",
        f"- 1800s full chain: duration=`{long_hold.get('duration_s')}` state=`{long_hold.get('final_state')}` sha=`{long_hold.get('report_sha256')}`",
        "",
        "## D3-T01 … D3-T24",
        "",
        "| ID | Title | Result | Test |",
        "|---|---|---|---|",
    ]
    for item in t_results:
        lines.append(
            f"| {item['id']} | {item['title']} | {item['result']} | `{item['test_file']}::{item['test_function']}` |"
        )
    lines.extend([
        "",
        "## Limitations",
        "",
        *[f"- {x}" for x in payload["limitations"]],
        "",
        payload["disclaimer"],
        "",
    ])
    if dirty and not args.allow_dirty:
        lines.insert(2, "> Workspace is dirty: this is **draft** evidence, not formal acceptance.")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"formal_acceptance={formal} workspace_clean={not dirty}")
    print(f"pytest: {pytest_summary}")

    if pytest_proc.returncode != 0:
        return pytest_proc.returncode
    if not args.skip_unittest and unittest_summary.get("returncode", 1) != 0:
        return int(unittest_summary.get("returncode") or 1)
    if not args.skip_web and not web_summary.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    # Ensure UTF-8 for Windows consoles when possible.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
