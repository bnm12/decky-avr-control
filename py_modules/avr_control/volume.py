"""Canonical receiver-volume conversion and cap policy."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .drivers.base import VolumeScale


class VolumePolicy:
    """Convert between display units and raw receiver steps.

    Half-step ties round away from display zero (for example, -32.25 dB on a
    half-dB receiver becomes -32.5 dB). All results are bounded by the scale.
    """

    @staticmethod
    def to_raw(display: float, scale: VolumeScale) -> int:
        try:
            steps = (
                Decimal(str(display)) * Decimal(scale.steps_per_unit)
            ).to_integral_value(rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            raise ValueError("Volume must be a finite number") from None
        raw = scale.zero + int(steps)
        return max(scale.min, min(scale.max, raw))

    @staticmethod
    def to_display(raw: int, scale: VolumeScale) -> float:
        return (int(raw) - scale.zero) / scale.steps_per_unit

    @staticmethod
    def default_cap(scale: VolumeScale) -> int:
        if scale.unit == "dB":
            raw = scale.zero - 30 * scale.steps_per_unit
        elif scale.default_cap is not None:
            raw = scale.default_cap
        else:
            raw = scale.min + int(
                (Decimal(scale.max - scale.min) * Decimal("0.4")).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )
        return max(scale.min, min(scale.max, raw))

    @staticmethod
    def default_late_cap(scale: VolumeScale) -> int:
        if scale.unit == "dB":
            raw = scale.zero - 50 * scale.steps_per_unit
        else:
            raw = scale.min + int(
                (Decimal(scale.max - scale.min) * Decimal("0.2")).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )
        return max(scale.min, min(scale.max, raw))

    @classmethod
    def base_cap(cls, cap: int | None, scale: VolumeScale) -> int:
        raw = cls.default_cap(scale) if cap is None or cap == 0 else cap
        return cls.clamp(raw, raw, scale)

    @classmethod
    def late_cap(cls, cap: int | None, scale: VolumeScale) -> int:
        raw = cls.default_late_cap(scale) if cap is None or cap == 0 else cap
        return cls.clamp(raw, raw, scale)

    @classmethod
    def effective_cap(
        cls,
        cap: int | None,
        late_cap: int | None,
        late_night: bool,
        scale: VolumeScale,
    ) -> int:
        normal = cls.base_cap(cap, scale)
        active_late = cls.late_cap(late_cap, scale)
        selected = min(normal, active_late) if late_night else normal
        return max(scale.min, min(scale.max, int(selected)))

    @staticmethod
    def clamp(raw: int, cap: int, scale: VolumeScale) -> int:
        return max(scale.min, min(scale.max, min(int(cap), int(raw))))

    @staticmethod
    def display_scale(scale: VolumeScale) -> dict[str, float | str]:
        return {
            "unit": scale.unit,
            "min_display": VolumePolicy.to_display(scale.min, scale),
            "max_display": VolumePolicy.to_display(scale.max, scale),
            "step_display": 1 / scale.steps_per_unit,
        }
