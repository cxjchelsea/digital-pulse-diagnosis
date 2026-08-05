import math

import pytest

from digital_pulse.d3_contracts import ControllerConfig, D3ContractError, TimingConfig
from digital_pulse.d3_controller import D3PIDController


def controller(**overrides):
    values = dict(controller_id="test", kp=0.1, ki=0.2, kd=0.0,
                  target_slew_au_s=10.0, min_stable_s=0.03)
    values.update(overrides)
    return D3PIDController(ControllerConfig(**values), TimingConfig(control_period_us=10_000))


def test_target_changes_are_slew_limited():
    c = controller()
    assert c.update(20.0, 0.0, 0.0).ramped_target_au == pytest.approx(0.1)
    assert c.update(20.0, 0.0, 0.0).ramped_target_au == pytest.approx(0.2)


def test_output_is_symmetric_and_bounded():
    c = controller(kp=10.0, target_slew_au_s=1000.0)
    assert c.update(20.0, 0.0, 0.0).command == 1.0
    c.reset(initial_target_au=0.0)
    assert c.update(0.0, 20.0, 0.0).command == -1.0


def test_integral_is_bounded_during_sustained_saturation():
    c = controller(kp=2.0, ki=4.0, integral_limit=0.25, target_slew_au_s=1000.0)
    for _ in range(500):
        last = c.update(100.0, 0.0, 0.0)
    assert abs(last.integral) <= 0.25
    assert last.saturated


def test_error_reversal_recovers_from_positive_saturation():
    c = controller(kp=1.0, ki=1.0, target_slew_au_s=1000.0)
    for _ in range(100):
        c.update(100.0, 0.0, 0.0)
    assert c.update(0.0, 100.0, 0.0).command < 0.0


def test_disabled_controller_outputs_zero_and_resets_dynamics():
    c = controller()
    c.update(20.0, 0.0, 0.0)
    stopped = c.update(20.0, 2.0, 0.0, enabled=False)
    assert stopped.command == 0.0
    assert stopped.tick == 0
    assert stopped.ramped_target_au == 2.0
    assert stopped.integral == 0.0


def test_invalid_measurement_never_enters_pid_or_stability_window():
    invalid = controller().update(1.0, 0.0, 0.0, measurement_valid=False)
    assert invalid.command == 0.0
    assert not invalid.measurement_valid
    assert not invalid.stable


def test_stable_requires_continuous_minimum_duration():
    c = controller(kp=0.0, ki=0.0)
    assert not c.update(0.0, 0.0, 0.0).stable
    assert not c.update(0.0, 0.0, 0.0).stable
    third = c.update(0.0, 0.0, 0.0)
    assert third.stable
    assert third.stable_duration_s == pytest.approx(0.03)


def test_error_outside_window_resets_stable_timer():
    c = controller(kp=0.0, ki=0.0)
    c.update(0.0, 0.0, 0.0)
    c.update(0.0, 0.0, 0.0)
    assert c.update(0.0, 10.0, 0.0).stable_duration_s == 0.0


def test_excess_force_rate_prevents_stability():
    c = controller(kp=0.0, ki=0.0)
    for _ in range(10):
        result = c.update(0.0, 0.0, c.config.tolerance_rate_au_s + 0.1)
    assert not result.stable


def test_same_input_sequence_is_deterministic():
    first, second = controller(), controller()
    sequence = [(20.0, float(i) / 10.0, 0.2) for i in range(30)]
    assert [first.update(*x) for x in sequence] == [second.update(*x) for x in sequence]


def test_reset_reproduces_initial_response():
    c = controller()
    initial = c.update(20.0, 0.0, 0.0)
    for _ in range(20):
        c.update(20.0, 0.0, 0.0)
    c.reset()
    assert c.update(20.0, 0.0, 0.0) == initial


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_inputs_are_rejected(value):
    with pytest.raises(D3ContractError, match="must be finite"):
        controller().update(value, 0.0, 0.0)


def test_negative_target_is_rejected():
    with pytest.raises(D3ContractError, match="non-negative"):
        controller().update(-1.0, 0.0, 0.0)
