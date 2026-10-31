# Roadmap

Current state and next steps. Update this file at the end of every slice. Decisions go in `docs/adr/`, not here.

## State (v0.4.0)

- Drivers behind one seam (ADR-0009): eISCP (verified on a Pioneer VSX-935, with per-model scale overrides), Yamaha YXC, Yamaha YNC (pre-MusicCast), Sony ScalarWebAPI, Denon/Marantz (telnet with HTTP fallback), HEOS, legacy Pioneer telnet, generic UPnP/Sonos (volume and mute only) and Home Assistant. All drivers except eISCP are simulator-tested only and labelled experimental, and every network driver has a simulator transcript fixture.
- Volume scales are dB, percent or bare steps (ADR-0015). Receiver-bound state uses each driver's declared identity keys (ADR-0014).
- One `ControlApplication` owns receiver selection, caps, late night, profiles, rediscovery and snapshots behind typed intents. `VolumePolicy` is the canonical raw/display and cap calculation module (ADR-0018).
- The panel can subscribe to receiver push updates from TCP drivers while visible; eISCP, Denon Telnet, legacy Pioneer and HEOS support the initial event path, with 2-second polling fallback and 15-second safety resync. Event behavior remains unmeasured on hardware.
- Optional local-time quiet-hours schedules and Steam resume/suspend power automation are settings-backed application intents; schedule/manual overrides and Steam power-state guards are enforced in the application.
- Listening-mode snapshots distinguish the current code from selectable options and mark lists complete only when their receiver source is authoritative; static eISCP/Denon catalogs remain incomplete.
- Discovery lists each host once, under the most specific driver (registry order).
- Renamed to AVR Control (ADR-0010). Settings migrate from the old `pioneer-volume` folder.
- The panel follows each driver's scale and capabilities, and has power, listening modes, opt-in game profiles and late night mode.
- Probe kit and transcript replay tests let owners of other receivers verify drivers (ADR-0013, `CONTRIBUTING.md`).
- Reviewed in four Sol rounds. One finding was deliberately not fixed: caps saved by intermediate, never-released branch builds may reset once. Released v0.2.x settings migrate correctly.

## Next

1. **Owner hardware check on the VSX-935:** upgrade from Pioneer Volume (settings migration), discovery, power, and that profiles and caps survive. Record anything surprising as **[measured]** in `drivers/eiscp.md`.
2. **Collect transcripts** from owners of every experimental driver via Receiver report issues. Promote each driver from experimental once a transcript replays.
3. **Deepening** (`docs/research/deepening-options.md`):
   - a shared, owned TCP session module for eISCP, Denon and Pioneer telnet (connection ownership, serialisation, push events) is implemented; validate push event behavior with receiver owners.
4. **Features** in value-per-effort order: soft-start volume ramp; a cancellable sleep timer; measure push event coverage on hardware before reducing the 2 s polling fallback for unsupported drivers; showing the incoming signal format (eISCP `IFA`, unverified). Time-of-day profiles and Steam sleep/wake automation are implemented, but their behavior still needs owner testing on hardware.
5. **Store submission:** test on SteamOS Stable and Beta, add screenshots, and make sure README claims stay within hardware evidence.
6. **More brands** from `docs/research/avr-brand-feasibility.md`, when owners ask and can send a transcript: Anthem, Arcam, BluOS.

## Not planned

- Per-game input switching. Video passes through the AVR, so it is already on the right input (ADR-0001).
- Patching Steam's native volume UI. It's fragile across client updates. A supported global input hook could be revisited.
- Harman Kardon, McIntosh AV, Google Cast, TVs and host HDMI-CEC: they can't offer the full receiver feature set, or they need heavy protocols (research doc).
- LG ThinQ (cloud). The local webOS SSAP route is possible but is TV control, not receiver control (`docs/research/avr-brand-feasibility.md`).
