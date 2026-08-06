"""Central scenario registry for the M1 simulator."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .config import (
    M1SimulatorConfigError,
    NORMAL_HIGH_QUALITY,
    ScenarioConfig,
    build_normal_high_quality,
)

ScenarioBuilder = Callable[..., ScenarioConfig]

SCENARIO_REGISTRY: dict[str, ScenarioBuilder] = {
    NORMAL_HIGH_QUALITY: build_normal_high_quality,
}


def list_scenarios() -> tuple[str, ...]:
    return tuple(sorted(SCENARIO_REGISTRY))


def get_scenario(scenario_id: str, **overrides: Any) -> ScenarioConfig:
    try:
        builder = SCENARIO_REGISTRY[scenario_id]
    except KeyError as exc:
        raise M1SimulatorConfigError("unknown_scenario", f"unknown scenario_id: {scenario_id}") from exc
    return builder(**overrides)
