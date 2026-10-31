# Contributing

## Reporting a receiver

1. Enable network control and, if you want to test standby, network standby on
   the receiver. Keep it on your trusted LAN.
2. Get a checkout of this repository and run:

   ```powershell
   python tools/avr_probe.py discover
   python tools/avr_probe.py capture <receiver-ip>
   ```

   Discovery writes `avr_probe_discover.json`; capture writes
   `avr_probe_capture.json`. Use `--out FILE` to choose another path. Capture
   is read-only by default. `--write` asks before briefly setting the current
   volume, toggling and restoring mute, and setting the current listening mode.
   `--power` additionally puts the receiver in standby and turns it back on;
   use it only with `--write`.
3. Attach the capture JSON to a **Receiver report** issue and complete the
   model, firmware, network-standby and observed-behavior fields.

   Captures include driver status and capabilities, volume scale, listening
   modes, and timestamped raw TCP request/reply bytes. IP addresses and
   receiver identifiers are redacted by default. Review the file before
   sharing, especially if you use `--no-redact`. Home Assistant capture is
   intentionally excluded. **Never share Home Assistant tokens, passwords,
   Wi-Fi credentials, or account details.**

## What happens next

We turn sanitized hardware captures into replay fixtures so the driver's
requests and replies can be checked without the receiver. We then update the
driver's protocol spec with **[measured]** facts and its **Verified on** row.
Until hardware evidence exists, model-family support remains experimental.

## Adding a driver

Follow [AGENTS.md, “Adding a driver”](AGENTS.md#adding-a-driver); include a
transcript fixture alongside the driver's fake-receiver tests.

## Tests and build

Run the commands in [AGENTS.md, “Commands”](AGENTS.md#commands). The Python
suite includes transcript replay tests:

```powershell
python -m unittest discover -s tests -t . -v
```
