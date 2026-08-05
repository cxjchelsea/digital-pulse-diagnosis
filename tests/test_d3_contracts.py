import unittest

from digital_pulse.d3_contracts import (
    ALLOWED_TRANSITIONS,
    ControllerConfig,
    D3ContractError,
    D3State,
    FAULT_PRIORITY_INDEX,
    FaultCode,
    FaultInjection,
    PlantConfig,
    SafetyAction,
    SafetyConfig,
    SafetyEvent,
    ScenarioConfig,
    TimingConfig,
    assert_transition,
    highest_priority_fault,
)


class D3ContractTests(unittest.TestCase):
    def test_default_contracts_validate(self):
        TimingConfig().validate()
        PlantConfig("plant-default").validate()
        ControllerConfig("pid-default").validate()
        SafetyConfig("safety-default").validate()
        ScenarioConfig("normal", (40.0, 80.0, 120.0), seed=7).validate()

    def test_timing_requires_integer_tick_multiples(self):
        with self.assertRaisesRegex(D3ContractError, "multiple"):
            TimingConfig(control_period_us=10_500).validate()
        with self.assertRaisesRegex(D3ContractError, "exceed"):
            TimingConfig(host_timeout_ms=100).validate()

    def test_plant_rejects_non_finite_and_invalid_geometry(self):
        with self.assertRaises(D3ContractError):
            PlantConfig("bad", stiffness_linear=float("nan")).validate()
        with self.assertRaisesRegex(D3ContractError, "contact"):
            PlantConfig("bad", contact_position_au=60).validate()

    def test_controller_and_safety_limits_are_ordered(self):
        with self.assertRaisesRegex(D3ContractError, "normalized"):
            ControllerConfig("bad", output_limit=1.1).validate()
        with self.assertRaisesRegex(D3ContractError, "below"):
            SafetyConfig("bad", soft_force_limit_au=160, hard_force_limit_au=160).validate()

    def test_scenario_is_deterministically_hashed(self):
        first = ScenarioConfig("fault", (40.0, 80.0), 11, (FaultInjection(FaultCode.HOST_TIMEOUT, 2.0),))
        second = ScenarioConfig("fault", (40.0, 80.0), 11, (FaultInjection(FaultCode.HOST_TIMEOUT, 2.0),))
        first.validate()
        self.assertEqual(first.canonical(), second.canonical())
        self.assertEqual(first.checksum(), second.checksum())
        self.assertEqual(len(first.checksum()), 64)

    def test_profile_and_fault_time_validation(self):
        with self.assertRaises(D3ContractError):
            ScenarioConfig("empty", (), 1).validate()
        with self.assertRaises(D3ContractError):
            FaultInjection(FaultCode.DATA_QUALITY, -1).validate()

    def test_fault_priority_is_frozen(self):
        self.assertLess(FAULT_PRIORITY_INDEX[FaultCode.EMERGENCY_STOP], FAULT_PRIORITY_INDEX[FaultCode.HOST_TIMEOUT])
        self.assertEqual(
            highest_priority_fault([FaultCode.DATA_QUALITY, FaultCode.HARD_OVERLOAD, FaultCode.HOST_TIMEOUT]),
            FaultCode.HARD_OVERLOAD,
        )
        self.assertIsNone(highest_priority_fault([]))

    def test_transition_contract_blocks_shortcuts(self):
        assert_transition(D3State.IDLE, D3State.APPROACH)
        assert_transition(D3State.RETRACT, D3State.IDLE)
        with self.assertRaisesRegex(D3ContractError, "forbidden"):
            assert_transition(D3State.IDLE, D3State.ACQUIRE)
        self.assertNotIn(D3State.IDLE, ALLOWED_TRANSITIONS[D3State.FAULT_LATCHED])

    def test_latched_fault_requires_self_test(self):
        self.assertEqual(ALLOWED_TRANSITIONS[D3State.FAULT_LATCHED], frozenset({D3State.SELF_TEST}))

    def test_event_priority_and_finite_snapshot_are_validated(self):
        event = SafetyEvent(
            tick=10,
            device_time_us=10_000,
            code=FaultCode.EMERGENCY_STOP,
            priority=FAULT_PRIORITY_INDEX[FaultCode.EMERGENCY_STOP],
            source="estop_input",
            previous_state=D3State.ACQUIRE,
            action=SafetyAction.ZERO_OUTPUT,
            target_state=D3State.FAULT_LATCHED,
            latched=True,
            snapshot={"force_au": 80.0, "estop": True},
        )
        event.validate()
        with self.assertRaisesRegex(D3ContractError, "priority"):
            SafetyEvent(
                tick=10,
                device_time_us=10_000,
                code=FaultCode.EMERGENCY_STOP,
                priority=99,
                source="estop_input",
                previous_state=D3State.ACQUIRE,
                action=SafetyAction.ZERO_OUTPUT,
                target_state=D3State.FAULT_LATCHED,
                latched=True,
                snapshot={},
            ).validate()


if __name__ == "__main__":
    unittest.main()
