"""Fixed multi-attempt simulation plans for M1-P1C (no INT decision execution)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from digital_pulse.m1_contracts import DecisionAction, QualityLabel

from .config import M1SimulatorConfigError, ScenarioConfig
from .scenario_ids import (
    NORMAL_HIGH_QUALITY,
    RETRY_IMPROVES,
    RETRY_STILL_FAILS,
    WEAK_SIGNAL,
)

MAX_ATTEMPTS_LIMIT = 8

AttemptPlanBuilder = Callable[..., "MultiAttemptPlan"]


@dataclass(frozen=True, slots=True)
class AttemptSpec:
    attempt_index: int
    scenario_id: str
    config: ScenarioConfig
    expected_quality_label: QualityLabel
    expected_reason_codes: tuple[str, ...]
    expected_int_action: DecisionAction
    analysis_allowed: bool


@dataclass(frozen=True, slots=True)
class MultiAttemptPlan:
    plan_id: str
    plan_version: str
    random_seed: int
    max_attempts: int
    attempts: tuple[AttemptSpec, ...]
    description: str
    expected_quality_label: QualityLabel
    expected_reason_codes: tuple[str, ...]
    expected_int_action: DecisionAction
    analysis_allowed: bool
    expected_completion: bool

    def validate(self) -> None:
        if not self.plan_id:
            raise M1SimulatorConfigError("invalid_attempt_plan", "plan_id is required")
        if not self.attempts:
            raise M1SimulatorConfigError("invalid_attempt_plan", "attempts must not be empty")
        if self.max_attempts < 1 or self.max_attempts > MAX_ATTEMPTS_LIMIT:
            raise M1SimulatorConfigError(
                "invalid_attempt_plan",
                f"max_attempts must be within 1..{MAX_ATTEMPTS_LIMIT}",
            )
        if len(self.attempts) > self.max_attempts:
            raise M1SimulatorConfigError("invalid_attempt_plan", "attempts exceed max_attempts")
        indices = [item.attempt_index for item in self.attempts]
        if indices != list(range(1, len(self.attempts) + 1)):
            raise M1SimulatorConfigError("invalid_attempt_plan", "attempt_index must start at 1 and be contiguous")


@dataclass(frozen=True, slots=True)
class AttemptPlanDefinition:
    plan_id: str
    plan_version: str
    description: str
    builder: AttemptPlanBuilder
    expected_quality_label: QualityLabel
    expected_reason_codes: tuple[str, ...]
    expected_int_action: DecisionAction
    analysis_allowed: bool
    expected_completion: bool


def build_retry_improves(
    *,
    random_seed: int = 1001,
    duration_s: float = 4.0,
    sample_rate_hz: float = 250.0,
) -> MultiAttemptPlan:
    from .scenarios import get_scenario

    attempt1 = get_scenario(
        WEAK_SIGNAL,
        random_seed=int(random_seed),
        duration_s=float(duration_s),
        sample_rate_hz=float(sample_rate_hz),
    )
    attempt2 = get_scenario(
        NORMAL_HIGH_QUALITY,
        random_seed=int(random_seed),
        duration_s=float(duration_s),
        sample_rate_hz=float(sample_rate_hz),
    )
    plan = MultiAttemptPlan(
        plan_id=RETRY_IMPROVES,
        plan_version="1.0.0",
        random_seed=int(random_seed),
        max_attempts=2,
        description="Fixed plan: weak_signal then normal_high_quality (improvement is expected, not computed).",
        expected_quality_label=QualityLabel.ACCEPTABLE,
        expected_reason_codes=(),
        expected_int_action=DecisionAction.ACCEPT,
        analysis_allowed=True,
        expected_completion=True,
        attempts=(
            AttemptSpec(
                attempt_index=1,
                scenario_id=WEAK_SIGNAL,
                config=attempt1,
                expected_quality_label=QualityLabel.WEAK_SIGNAL,
                expected_reason_codes=("LOW_PULSE_AMPLITUDE",),
                expected_int_action=DecisionAction.RETRY_SAME_POSITION,
                analysis_allowed=False,
            ),
            AttemptSpec(
                attempt_index=2,
                scenario_id=NORMAL_HIGH_QUALITY,
                config=attempt2,
                expected_quality_label=QualityLabel.ACCEPTABLE,
                expected_reason_codes=(),
                expected_int_action=DecisionAction.ACCEPT,
                analysis_allowed=True,
            ),
        ),
    )
    plan.validate()
    return plan


def build_retry_still_fails(
    *,
    random_seed: int = 1001,
    duration_s: float = 4.0,
    sample_rate_hz: float = 250.0,
) -> MultiAttemptPlan:
    from .scenarios import get_scenario

    attempts: list[AttemptSpec] = []
    for index in range(1, 4):
        # Derived seeds change noise detail while preserving weak_signal semantics.
        config = get_scenario(
            WEAK_SIGNAL,
            random_seed=int(random_seed) + (index - 1) * 17,
            duration_s=float(duration_s),
            sample_rate_hz=float(sample_rate_hz),
        )
        attempts.append(
            AttemptSpec(
                attempt_index=index,
                scenario_id=WEAK_SIGNAL,
                config=config,
                expected_quality_label=QualityLabel.WEAK_SIGNAL,
                expected_reason_codes=("LOW_PULSE_AMPLITUDE",),
                expected_int_action=DecisionAction.RETRY_SAME_POSITION,
                analysis_allowed=False,
            )
        )
    plan = MultiAttemptPlan(
        plan_id=RETRY_STILL_FAILS,
        plan_version="1.0.0",
        random_seed=int(random_seed),
        max_attempts=3,
        description=(
            "Fixed plan: three weak_signal attempts. Each attempt session may complete, "
            "but the plan-level outcome is failure with reposition expected for INT."
        ),
        expected_quality_label=QualityLabel.WEAK_SIGNAL,
        expected_reason_codes=("RETRY_LIMIT_REACHED",),
        expected_int_action=DecisionAction.REPOSITION,
        analysis_allowed=False,
        # Plan-level target failed; individual attempt datasources still end normally.
        expected_completion=False,
        attempts=tuple(attempts),
    )
    plan.validate()
    return plan


ATTEMPT_PLAN_DEFINITIONS: dict[str, AttemptPlanDefinition] = {
    RETRY_IMPROVES: AttemptPlanDefinition(
        plan_id=RETRY_IMPROVES,
        plan_version="1.0.0",
        description="weak_signal followed by normal_high_quality",
        builder=build_retry_improves,
        expected_quality_label=QualityLabel.ACCEPTABLE,
        expected_reason_codes=(),
        expected_int_action=DecisionAction.ACCEPT,
        analysis_allowed=True,
        expected_completion=True,
    ),
    RETRY_STILL_FAILS: AttemptPlanDefinition(
        plan_id=RETRY_STILL_FAILS,
        plan_version="1.0.0",
        description="three weak_signal attempts reaching retry limit",
        builder=build_retry_still_fails,
        expected_quality_label=QualityLabel.WEAK_SIGNAL,
        expected_reason_codes=("RETRY_LIMIT_REACHED",),
        expected_int_action=DecisionAction.REPOSITION,
        analysis_allowed=False,
        expected_completion=False,
    ),
}


def list_attempt_plans() -> tuple[str, ...]:
    return tuple(sorted(ATTEMPT_PLAN_DEFINITIONS))


def get_attempt_plan_definition(plan_id: str) -> AttemptPlanDefinition:
    try:
        return ATTEMPT_PLAN_DEFINITIONS[plan_id]
    except KeyError as exc:
        raise M1SimulatorConfigError("unknown_attempt_plan", f"unknown attempt plan: {plan_id}") from exc


def get_attempt_plan(plan_id: str, **overrides: Any) -> MultiAttemptPlan:
    definition = get_attempt_plan_definition(plan_id)
    return definition.builder(**overrides)
