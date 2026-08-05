from digital_pulse.d3_integration import run_long_hold, run_normal_profile


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
