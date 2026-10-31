import unittest

from tests import PY_MODULES  # noqa: F401
from avr_control.drivers.base import VolumeScale
from avr_control.volume import VolumePolicy


class VolumePolicyTests(unittest.TestCase):
    def test_round_trips_raw_steps_and_negative_decibels(self):
        scale = VolumeScale(min=0, max=185, unit="dB", zero=164, steps_per_unit=2)
        for raw in range(scale.min, scale.max + 1):
            display = VolumePolicy.to_display(raw, scale)
            self.assertEqual(VolumePolicy.to_raw(display, scale), raw)
        self.assertEqual(VolumePolicy.to_display(100, scale), -32)

    def test_half_step_ties_round_away_from_display_zero(self):
        scale = VolumeScale(min=0, max=185, unit="dB", zero=164, steps_per_unit=2)
        self.assertEqual(VolumePolicy.to_raw(-32.25, scale), 99)
        self.assertEqual(VolumePolicy.to_raw(0.25, scale), 165)
        self.assertEqual(VolumePolicy.to_raw(-0.25, scale), 163)

    def test_display_and_raw_values_are_bounded_by_scale(self):
        scale = VolumeScale(min=10, max=90, unit="steps", zero=10)
        self.assertEqual(VolumePolicy.to_raw(-20, scale), 10)
        self.assertEqual(VolumePolicy.to_raw(200, scale), 90)
        self.assertEqual(VolumePolicy.clamp(95, 70, scale), 70)
        self.assertEqual(VolumePolicy.clamp(0, 70, scale), 10)

    def test_percent_and_steps_defaults_keep_existing_ranges(self):
        percent = VolumeScale(min=0, max=100, unit="%", zero=0)
        steps = VolumeScale(min=10, max=90, unit="steps", zero=10)
        self.assertEqual(VolumePolicy.default_cap(percent), 40)
        self.assertEqual(VolumePolicy.default_late_cap(percent), 20)
        self.assertEqual(VolumePolicy.default_cap(steps), 42)
        self.assertEqual(VolumePolicy.default_late_cap(steps), 26)
        declared_cap = VolumeScale(min=0, max=100, unit="%", default_cap=60)
        self.assertEqual(VolumePolicy.default_cap(declared_cap), 60)

    def test_decibel_defaults_and_late_cap_precedence(self):
        scale = VolumeScale(
            min=0, max=185, unit="dB", zero=164, steps_per_unit=2
        )
        self.assertEqual(VolumePolicy.default_cap(scale), 104)
        self.assertEqual(VolumePolicy.default_late_cap(scale), 64)
        self.assertEqual(
            VolumePolicy.effective_cap(120, 70, True, scale),
            70,
        )
        self.assertEqual(
            VolumePolicy.effective_cap(120, 70, False, scale),
            120,
        )
        self.assertEqual(
            VolumePolicy.effective_cap(None, None, True, scale),
            64,
        )


if __name__ == "__main__":
    unittest.main()
