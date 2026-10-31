# Roadmap

Current state and next steps. Update this file at the end of every slice. Decisions go in `docs/adr/`, not here.

## State (v0.4.0)

- Drivers behind one seam (ADR-0009): eISCP (verified on a Pioneer VSX-935, with per-model scale overrides), Yamaha YXC, Yamaha YNC (pre-MusicCast), Sony ScalarWebAPI, Denon/Marantz (telnet with HTTP fallback), HEOS, legacy Pioneer telnet, generic UPnP/Sonos (volume and mute only) and Home Assistant. All drivers except eISCP are simulator-tested only and labelled experimental, and every network driver has a simulator transcript fixture.
- Volume scales are dB, percent or bare steps (ADR-0015). Receiver-bound state uses each driver's declared identity keys (ADR-0014).
- Discovery lists each host once, under the most specific driver (registry order).
- Renamed to AVR Control (ADR-0010). Settings migrate from the old `pioneer-volume` folder.
- The panel follows each driver's scale and capabilities, and has power, listening modes, opt-in game profiles and late night mode.
- Probe kit and transcript replay tests let owners of other receivers verify drivers (ADR-0013, `CONTRIBUTING.md`).
- Reviewed in four Sol rounds. One finding was deliberately not fixed: caps saved by intermediate, never-released branch builds may reset once. Released v0.2.x settings migrate correctly.

## Next

1. **Owner hardware check on the VSX-935:** upgrade from Pioneer Volume (settings migration), discovery, power, and that profiles and caps survive. Record anything surprising as **[measured]** in `drivers/eiscp.md`.
2. **Collect transcripts** from owners of every experimental driver via Receiver report issues. Promote each driver from experimental once a transcript replays.
3. **Deepening** (`docs/research/deepening-options.md`, top picks):
   - a shared, owned TCP session module for eISCP, Denon and Pioneer telnet (connection ownership, serialisation, push events);
   - one deep application module with a state snapshot and typed intents, shrinking the ~20-method RPC surface.
4. **Features** in value-per-effort order: soft-start volume ramp; a cancellable sleep timer; checking whether receivers push state changes before replacing the 2 s poll; showing the incoming signal format (eISCP `IFA`, unverified).
5. **Store submission:** test on SteamOS Stable and Beta, add screenshots, and make sure README claims stay within hardware evidence.
6. **More brands** from `docs/research/avr-brand-feasibility.md`, when owners ask and can send a transcript: Anthem, Arcam, BluOS.

## Not planned

- Per-game input switching. Video passes through the AVR, so it is already on the right input (ADR-0001).
- Patching Steam's native volume UI. It's fragile across client updates. A supported global input hook could be revisited.
- Harman Kardon, McIntosh AV, Google Cast, TVs and host HDMI-CEC: they can't offer the full receiver feature set, or they need heavy protocols (research doc).
- LG ThinQ (cloud). The local webOS SSAP route is possible but is TV control, not receiver control (`docs/research/avr-brand-feasibility.md`).
