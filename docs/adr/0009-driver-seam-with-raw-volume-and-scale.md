# Drivers behind one seam, with raw volume units and a per-driver scale

Supporting more than Pioneer/Onkyo requires protocol code outside `main.py`. Each protocol is a `Driver` (`py_modules/avr_control/drivers/base.py`) offering get/set volume, mute, power and listening mode, a mode list, discovery and a probe for manual IPs. Unsupported capabilities raise `Unsupported`, and the UI hides what `capabilities` doesn't list. Code lives in `py_modules/` because Decky puts that folder on `sys.path`, and the plugin folder itself isn't on it.

Volumes cross the seam as raw receiver integers. Each driver also publishes a `VolumeScale` (`unit` dB or %, `zero`, `steps_per_unit`, `default_cap`) that the UI uses to render dB or percent. We chose this over normalising to dB because some protocols have no dB mapping (Home Assistant is 0–1), and because raw values round-trip exactly (no 0.5 dB rounding drift in saved profiles or caps).

## Considered options

- **Normalise to dB at the seam.** Rejected because percent-only receivers have no dB value.
- **Normalise to 0–100 %.** Rejected because dB is what the receiver's display shows, and users set caps in dB.

## Consequences

Caps and per-game volumes are only meaningful for the receiver/driver that produced them. See ADR-0012.
