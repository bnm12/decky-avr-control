# Unverified brands are tested from user-recorded transcripts

We only own one receiver (a Pioneer VSX-935), so most drivers are written from public specs (`docs/research/avr-brand-feasibility.md`). Owners of other receivers run `tools/avr_probe.py` on their LAN. It drives the real driver through a local recording TCP proxy (read-only unless `--write` is given) and writes a redacted JSON transcript, which they attach to a "Receiver report" issue. Each transcript becomes a fixture in `tests/transcripts/` that a replay server serves back to the driver in CI, so the receiver's real bytes guard against regressions.

Recording at a proxy, not inside drivers, keeps the driver seam (ADR-0009) free of test hooks and means every TCP or HTTP driver is recordable as long as it honours `config["port"]`.

## Considered options

- **A generic "custom command" driver** users configure themselves. Deferred: it is powerful but unsafe (unbounded volume commands) and doesn't improve the real drivers.
- **Asking testers to run the plugin and send logs.** Rejected: plugin logs lack raw bytes and need a full install on the Steam device.
