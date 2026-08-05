"""Abortable in-process D3 runtime sessions for API/Web closed-loop control."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
import threading
import time
import uuid
from typing import Callable

from digital_pulse.d3_contracts import (
    ControllerConfig,
    D3Command,
    D3State,
    PlantConfig,
    SafetyConfig,
    TimingConfig,
)
from digital_pulse.d3_controller import D3PIDController
from digital_pulse.d3_plant import D3Plant, PlantObservation
from digital_pulse.d3_safety import SafetyInputs
from digital_pulse.d3_state_machine import ACTIVE_STATES, D3DeviceStateMachine, StateInputs


RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_SESSIONS = 32
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "FAULT_LATCHED", "ABORTED_IDLE"})


@dataclass
class RuntimeSnapshot:
    run_id: str
    status: str
    state: str
    tick: int
    target_force_au: float | None
    actual_force_au: float
    position_au: float
    command: float
    unload_complete: bool
    final_state: str | None
    error: str | None
    seed: int
    targets_au: list[float]
    timeline: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    report: dict | None = None
    disclaimer: str = (
        "Synthetic relative units (*_au); not human-safety or hardware validation."
    )

    def as_dict(self) -> dict:
        return asdict(self)


class D3RuntimeSession:
    """Fixed-tick plant+PID+safety+SM session; ABORT goes through the device SM."""

    def __init__(
        self,
        run_id: str,
        *,
        targets: tuple[float, ...] = (20.0, 40.0, 60.0),
        seed: int = 20260805,
        acquire_s: float = 0.5,
        max_duration_s: float = 60.0,
        hold: bool = False,
    ):
        if not targets or any(not math.isfinite(x) or x <= 0 for x in targets):
            raise ValueError("targets must be finite and positive")
        self.run_id = run_id
        self.targets = targets
        self.seed = seed
        self.acquire_s = acquire_s
        self.max_duration_s = max_duration_s
        self.hold = hold
        self.timing = TimingConfig()
        self.plant = D3Plant(PlantConfig(plant_id="d3-runtime-plant"), self.timing, seed=seed)
        self.controller = D3PIDController(
            ControllerConfig(controller_id="d3-runtime-ctrl"), self.timing
        )
        self.machine = D3DeviceStateMachine(
            SafetyConfig(safety_id="d3-runtime-safety"), self.timing
        )
        self._lock = threading.RLock()
        self._abort_requested = False
        self._abort_count = 0
        self._hold_wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self.status = "CREATED"
        self.error: str | None = None
        self.unload_complete = False
        self.final_state: str | None = None
        self.report: dict | None = None
        self.timeline: list[dict] = []
        self.events: list[dict] = []
        self._target_index = 0
        self._observation: PlantObservation | None = None
        self._previous_force = 0.0
        self._command = 0.0
        self._last_output_command = 0.0
        self._positive_after_abort = False
        self._abort_tick: int | None = None
        self._retract_tick: int | None = None

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("session already started")
            self._started = True
            self.status = "RUNNING"
            self._thread = threading.Thread(
                target=self._run_loop, name=f"d3-run-{self.run_id}", daemon=True
            )
            self._thread.start()

    def request_abort(self) -> dict:
        with self._lock:
            if self._abort_requested or self.status == "ABORTED_IDLE":
                return self.snapshot()
            if self.status in TERMINAL_STATES:
                raise ConflictError("run already finished")
            if self.machine.state not in ACTIVE_STATES:
                raise ConflictError(f"abort not allowed in state {self.machine.state.value}")
            self._abort_requested = True
            self._abort_count += 1
            self.status = "ABORTING"
            self._hold_wake.set()
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            force = 0.0
            position = 0.0
            if self._observation is not None:
                force = (
                    self._observation.force_au
                    if self._observation.force_au is not None
                    else self._observation.true_force_au
                )
                position = (
                    self._observation.position_au
                    if self._observation.position_au is not None
                    else self._observation.true_position_au
                )
            target = None
            if self.machine.state in {
                D3State.STABILIZE,
                D3State.ACQUIRE,
                D3State.STEP,
                D3State.CONTACT,
            }:
                target = self.targets[min(self._target_index, len(self.targets) - 1)]
            snap = RuntimeSnapshot(
                run_id=self.run_id,
                status=self.status,
                state=self.machine.state.value,
                tick=self.machine.tick,
                target_force_au=target,
                actual_force_au=force,
                position_au=position,
                command=self._last_output_command,
                unload_complete=self.unload_complete,
                final_state=self.final_state,
                error=self.error,
                seed=self.seed,
                targets_au=list(self.targets),
                timeline=list(self.timeline),
                events=list(self.events),
                report=self.report,
            )
            return snap.as_dict()

    def wait_until(
        self,
        predicate: Callable[[dict], bool],
        *,
        timeout_s: float = 10.0,
        poll_s: float = 0.005,
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self.snapshot()
            if predicate(snap):
                return snap
            time.sleep(poll_s)
        raise TimeoutError(f"condition not met within {timeout_s}s; last={self.snapshot()}")

    def join(self, timeout_s: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)

    def _run_loop(self) -> None:
        try:
            timing = self.timing
            integrations = timing.control_period_us // timing.integration_period_us
            max_ticks = math.ceil(self.max_duration_s * 1_000_000 / timing.control_period_us)
            acquire_ticks = math.ceil(self.acquire_s * 1_000_000 / timing.control_period_us)
            acquisition_ticks = 0
            previous_state = self.machine.state
            with self._lock:
                self.machine.step()
                self.machine.step(StateInputs(self_test_passed=True))
                self.machine.step(command=D3Command.START)
                self.timeline.append({"tick": self.machine.tick, "state": self.machine.state.value})
                previous_state = self.machine.state

            for _ in range(max_ticks):
                # In hold mode, park in ACQUIRE without burning model ticks until ABORT.
                if (
                    self.hold
                    and self.machine.state is D3State.ACQUIRE
                    and not self._abort_requested
                ):
                    self._hold_wake.wait(timeout=0.05)
                    self._hold_wake.clear()
                    if not self._abort_requested:
                        continue

                with self._lock:
                    observation = self._observation
                    force = (
                        observation.force_au
                        if observation and observation.force_au is not None
                        else (observation.true_force_au if observation else 0.0)
                    )
                    position = (
                        observation.position_au
                        if observation and observation.position_au is not None
                        else (observation.true_position_au if observation else 0.0)
                    )
                    force_rate = (force - self._previous_force) / self.controller.dt_s
                    self._previous_force = force
                    state = self.machine.state
                    control = None
                    requested = 0.4 if state is D3State.APPROACH else 0.0
                    if state in {D3State.STABILIZE, D3State.ACQUIRE, D3State.STEP}:
                        control = self.controller.update(
                            self.targets[self._target_index], force, force_rate
                        )
                        requested = control.command
                    if state is D3State.ACQUIRE:
                        acquisition_ticks += 1
                    else:
                        acquisition_ticks = 0
                    # hold=True keeps ACQUIRE until ABORT (for API/Web abort closed-loop demos).
                    complete = (
                        (not self.hold)
                        and state is D3State.ACQUIRE
                        and acquisition_ticks >= acquire_ticks
                    )
                    abort_cmd = None
                    if self._abort_requested and state in ACTIVE_STATES:
                        abort_cmd = D3Command.ABORT
                    # Heartbeat age stays fresh while running; host timeout is not injected here.
                    host_age = 0.0
                    inputs = StateInputs(
                        safety=SafetyInputs(
                            force_au=force,
                            force_rate_au_s=force_rate,
                            position_au=position,
                            lower_limit=bool(observation and observation.lower_limit),
                            upper_limit=bool(observation and observation.upper_limit),
                            host_heartbeat_age_ms=host_age,
                        ),
                        contact_detected=bool(observation and observation.contact),
                        controller_stable=bool(control and control.stable),
                        acquisition_complete=complete,
                        has_more_targets=(
                            (not self.hold) and self._target_index < len(self.targets) - 1
                        ),
                    )
                    output = self.machine.step(
                        inputs, requested_command=requested, command=abort_cmd
                    )
                    self._last_output_command = output.command
                    if abort_cmd is D3Command.ABORT and self._abort_tick is None:
                        self._abort_tick = output.tick
                        self.events.append({
                            "tick": output.tick,
                            "type": "ABORT",
                            "state": output.state.value,
                            "command": output.command,
                        })
                        if output.command > 0:
                            self._positive_after_abort = True
                    if (
                        self._abort_tick is not None
                        and output.state is D3State.RETRACT
                        and self._retract_tick is None
                    ):
                        self._retract_tick = output.tick
                    if output.safety_event is not None:
                        self.events.append({
                            "tick": output.tick,
                            "type": "SAFETY",
                            "code": output.safety_event.code.value,
                            "action": output.safety_event.action.value,
                            "state": output.state.value,
                            "command": output.command,
                        })
                    if complete and not self._abort_requested:
                        if self._target_index < len(self.targets) - 1:
                            self._target_index += 1
                            self.controller.reset(initial_target_au=force)
                    for _ in range(integrations):
                        self._observation = self.plant.step(output.command)
                    if self.machine.state is not previous_state:
                        self.timeline.append({
                            "tick": self.machine.tick,
                            "state": self.machine.state.value,
                        })
                        previous_state = self.machine.state

                    obs = self._observation
                    if obs and not all(
                        math.isfinite(x)
                        for x in (
                            obs.true_force_au,
                            obs.true_position_au,
                            obs.true_velocity_au_s,
                            output.command,
                        )
                    ):
                        self.status = "FAILED"
                        self.error = "non_finite_state"
                        self.final_state = self.machine.state.value
                        return

                    if self.machine.state is D3State.FAULT_LATCHED:
                        self.status = "FAULT_LATCHED"
                        self.final_state = self.machine.state.value
                        self._finalize_report(completed=False)
                        return

                    if self.machine.state is D3State.IDLE:
                        if self._abort_requested or self._abort_tick is not None:
                            self.unload_complete = True
                            self.status = "ABORTED_IDLE"
                        else:
                            self.unload_complete = True
                            self.status = "COMPLETED"
                        self.final_state = "IDLE"
                        self._finalize_report(completed=True)
                        return

            with self._lock:
                self.status = "FAILED"
                self.error = "max_duration_exceeded"
                self.final_state = self.machine.state.value
                self._finalize_report(completed=False)
        except Exception as exc:  # noqa: BLE001 - surface to snapshot
            with self._lock:
                self.status = "FAILED"
                self.error = f"{type(exc).__name__}: {exc}"
                self.final_state = self.machine.state.value

    def _finalize_report(self, *, completed: bool) -> None:
        body = {
            "schema_version": "1.0.0",
            "experiment_type": "d3_runtime_session",
            "run_id": self.run_id,
            "seed": self.seed,
            "targets_au": list(self.targets),
            "status": self.status,
            "final_state": self.final_state,
            "completed": completed,
            "unload_complete": self.unload_complete,
            "abort_count": self._abort_count,
            "abort_tick": self._abort_tick,
            "retract_tick": self._retract_tick,
            "positive_command_after_abort": self._positive_after_abort,
            "timeline": list(self.timeline),
            "events": list(self.events),
            "disclaimer": (
                "Synthetic relative-unit runtime evidence; not hardware or human safety validation."
            ),
        }
        body["report_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.report = body


class ConflictError(RuntimeError):
    """Raised when an operation is illegal for the current session state."""


class D3RuntimeRegistry:
    """Thread-safe bounded registry of runtime sessions."""

    def __init__(self, *, max_sessions: int = MAX_SESSIONS):
        self._max = max_sessions
        self._lock = threading.RLock()
        self._sessions: dict[str, D3RuntimeSession] = {}

    def create(
        self,
        *,
        targets: tuple[float, ...] | None = None,
        seed: int = 20260805,
        acquire_s: float = 0.5,
        max_duration_s: float = 60.0,
        hold: bool = False,
    ) -> D3RuntimeSession:
        with self._lock:
            self._evict_finished()
            if len(self._sessions) >= self._max:
                raise RuntimeError(f"too many concurrent runs (max {self._max})")
            run_id = uuid.uuid4().hex
            session = D3RuntimeSession(
                run_id,
                targets=targets or (20.0, 40.0, 60.0),
                seed=seed,
                acquire_s=acquire_s,
                max_duration_s=max_duration_s,
                hold=hold,
            )
            self._sessions[run_id] = session
            session.start()
            return session

    def get(self, run_id: str) -> D3RuntimeSession:
        validate_run_id(run_id)
        with self._lock:
            session = self._sessions.get(run_id)
            if session is None:
                raise KeyError(run_id)
            return session

    def _evict_finished(self) -> None:
        finished = [
            key
            for key, session in self._sessions.items()
            if session.status in TERMINAL_STATES
        ]
        # Keep newest half of finished sessions for query; drop oldest extras.
        if len(self._sessions) < self._max:
            return
        for key in finished[: max(0, len(finished) - self._max // 2)]:
            self._sessions.pop(key, None)


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid run_id")
    return run_id
