from dataclasses import replace
import unittest

from digital_pulse.d3_contracts import D3ContractError, PlantConfig, TimingConfig
from digital_pulse.d3_plant import D3Plant, ObservationConfig, ObservationFaults, PlantState


def plant(**changes):
    config = PlantConfig("test-plant", **changes)
    return D3Plant(config, TimingConfig(integration_period_us=10_000, control_period_us=10_000, telemetry_period_us=10_000))


class D3PlantTests(unittest.TestCase):
    def test_free_motion_before_contact_has_zero_force(self):
        model = plant(contact_position_au=10.0)
        observations = model.run([0.5] * 20)
        self.assertGreater(observations[-1].true_position_au, 0)
        self.assertFalse(observations[-1].contact)
        self.assertEqual(observations[-1].true_force_au, 0)

    def test_contact_produces_positive_force(self):
        model = plant(contact_position_au=1.0)
        observations = model.run([1.0] * 30)
        self.assertTrue(observations[-1].contact)
        self.assertGreater(observations[-1].true_force_au, 0)

    def test_unloading_returns_to_lower_limit_and_zero_force(self):
        model = plant(contact_position_au=1.0)
        model.run([1.0] * 40)
        observations = model.run([-1.0] * 100)
        self.assertTrue(observations[-1].lower_limit)
        self.assertEqual(observations[-1].true_position_au, 0)
        self.assertEqual(observations[-1].true_force_au, 0)

    def test_position_and_velocity_never_exceed_limits(self):
        model = plant(contact_position_au=1.0, upper_position_au=2.0, max_velocity_au_s=3.0)
        observations = model.run([5.0] * 200)
        self.assertTrue(observations[-1].upper_limit)
        self.assertTrue(all(0 <= item.true_position_au <= 2.0 for item in observations))
        self.assertTrue(all(abs(item.true_velocity_au_s) <= 3.0 for item in observations))

    def test_command_is_clipped_to_normalized_range(self):
        model = plant()
        self.assertEqual(model.step(8.0).command, 1.0)
        self.assertEqual(model.step(-8.0).command, -1.0)

    def test_higher_stiffness_produces_higher_force_at_same_position(self):
        state = PlantState(position_au=2.0)
        low = D3Plant(PlantConfig("low", contact_position_au=1.0, stiffness_linear=2.0), initial_state=state)
        high = D3Plant(PlantConfig("high", contact_position_au=1.0, stiffness_linear=8.0), initial_state=state)
        self.assertGreater(high.step(0).true_force_au, low.step(0).true_force_au)

    def test_same_seed_produces_identical_noisy_observations(self):
        observation = ObservationConfig(position_noise_std_au=0.1, force_noise_std_au=0.2)
        first = D3Plant(PlantConfig("one"), observation=observation, seed=17)
        second = D3Plant(PlantConfig("two"), observation=observation, seed=17)
        self.assertEqual(first.run([0.5] * 20), second.run([0.5] * 20))

    def test_different_seed_changes_sensor_noise_not_true_state(self):
        observation = ObservationConfig(position_noise_std_au=0.1)
        first = D3Plant(PlantConfig("one"), observation=observation, seed=1).run([0.5] * 5)
        second = D3Plant(PlantConfig("two"), observation=observation, seed=2).run([0.5] * 5)
        self.assertEqual([x.true_position_au for x in first], [x.true_position_au for x in second])
        self.assertNotEqual([x.position_au for x in first], [x.position_au for x in second])

    def test_sensor_disconnect_removes_value_and_preserves_truth(self):
        observation = plant(contact_position_au=0.1).step(
            1.0, ObservationFaults(position_valid=False, force_valid=False)
        )
        self.assertIsNone(observation.position_au)
        self.assertIsNone(observation.force_au)
        self.assertFalse(observation.position_valid)
        self.assertTrue(observation.true_position_au >= 0)

    def test_freeze_holds_observation_while_true_state_moves(self):
        model = plant()
        first = model.step(1.0, ObservationFaults(freeze_position=True))
        second = model.step(1.0, ObservationFaults(freeze_position=True))
        self.assertEqual(first.position_au, second.position_au)
        self.assertGreater(second.true_position_au, first.true_position_au)

    def test_quantization_delay_bias_and_saturation_are_explicit(self):
        config = ObservationConfig(position_quantization_au=0.5, force_delay_steps=2)
        model = D3Plant(PlantConfig("observed", contact_position_au=0.0), observation=config)
        item = model.step(1.0, ObservationFaults(position_bias_au=0.2, force_saturation_au=0.01))
        self.assertEqual((item.position_au or 0) % 0.5, 0)
        self.assertLessEqual(abs(item.force_au or 0), 0.01)

    def test_reset_restores_state_and_random_sequence(self):
        model = D3Plant(
            PlantConfig("reset"),
            observation=ObservationConfig(position_noise_std_au=0.1),
            seed=9,
        )
        first = model.step(0.5)
        model.run([0.5] * 10)
        model.reset()
        self.assertEqual(model.step(0.5), first)

    def test_invalid_observation_and_command_are_rejected(self):
        with self.assertRaises(D3ContractError):
            ObservationConfig(force_delay_steps=-1).validate()
        with self.assertRaises(D3ContractError):
            plant().step(float("nan"))


if __name__ == "__main__":
    unittest.main()
