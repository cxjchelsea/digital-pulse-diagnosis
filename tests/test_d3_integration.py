from digital_pulse.d3_integration import (
    run_full_chain_long_hold,
    run_long_hold,
    run_normal_profile,
)


def test_normal_multi_target_profile_meets_model_thresholds():
    report = run_normal_profile()
    assert report["completed"]
    assert report["final_state"] == "IDLE"
    assert report["all_metrics_passed"]
    assert len(report["metrics"]) == 3


def test_normal_profile_is_deterministic():
    assert run_normal_profile() == run_normal_profile()


def test_profile_records_full_state_path():
    states = [item["state"] for item in run_normal_profile()["timeline"]]
    for required in ("APPROACH", "CONTACT", "STABILIZE", "ACQUIRE", "STEP", "RETRACT", "IDLE"):
        assert required in states


def test_short_long_hold_is_finite_and_bounded():
    result = run_long_hold(duration_s=30)
    assert result["finite"]
    assert result["integral_bounded"]
    assert result["final_force_error_au"] <= 2.0


def test_full_30_minute_model_hold():
    result = run_long_hold(duration_s=1800)
    assert result["duration_s"] == 1800
    assert result["finite"]
    assert result["integral_bounded"]
    assert result["final_force_error_au"] <= 2.0


def test_short_full_chain_is_finite_and_safe():
    result = run_full_chain_long_hold(duration_s=30.0, seed=20260805)
    assert result["finite"]
    assert result["integral_bounded"]
    assert result["command_in_range"]
    assert result["limits_respected"]
    assert result["no_false_host_timeout"]
    assert result["no_false_watchdog"]
    assert result["events_bounded"]
    assert result["final_state"] in {"ACQUIRE", "STABILIZE", "IDLE"}
    assert set(result["modules"]) >= {"plant", "pid", "safety", "device_state_machine"}


def test_full_chain_30_minute_model_time():
    result = run_full_chain_long_hold(duration_s=1800.0, seed=20260805)
    assert result["duration_s"] == 1800.0
    assert result["requested_duration_s"] == 1800.0
    assert result["finite"]
    assert result["integral_bounded"]
    assert result["command_in_range"]
    assert result["limits_respected"]
    assert result["no_false_host_timeout"]
    assert result["no_false_watchdog"]
    assert result["no_illegal_transition"]
    assert result["events_bounded"]
    assert result["timeline_bounded"]
    assert result["final_force_error_au"] <= 2.0
    assert result["final_state"] in {"ACQUIRE", "STABILIZE"}
    assert "device_state_machine" in result["modules"]


def test_full_chain_long_hold_is_deterministic():
    a = run_full_chain_long_hold(duration_s=5.0, seed=42)
    b = run_full_chain_long_hold(duration_s=5.0, seed=42)
    assert a["report_sha256"] == b["report_sha256"]
    assert a == b
