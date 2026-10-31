# Roadmap

Current capabilities and next steps. Decisions with lasting rationale belong in `docs/adr/`.

## State

- **Drivers** behind one seam (ADR-0009): eISCP (verified on a Pioneer VSX-935, with per-model scale overrides), Yamaha YXC, Yamaha YNC (pre-MusicCast), Sony ScalarWebAPI, Denon/Marantz (telnet with HTTP fallback), HEOS, legacy Pioneer telnet, generic UPnP/Sonos (volume and mute only) and Home Assistant (with an entity picker). Everything except eISCP is simulator-tested only and labelled experimental. Every network driver has a simulator transcript fixture (ADR-0013).
- **Architecture:**
  - TCP drivers share one `ReceiverSession` per receiver (ADR-0016).
  - Settings go through a `SettingsRepository`.
  - One `ControlApplication` sits behind `snapshot` / `dispatch(intent)` (ADR-0018), with `VolumePolicy` as the only volume math.
  - Drivers report `ModeState` (current mode vs selectable list, plus whether the list is complete).
- **Push sync** runs only while the panel is open. The eISCP, Denon telnet, legacy Pioneer and HEOS event paths come from specs and community sources and haven't been measured on hardware. Without push the panel polls every 2 s; with push it resyncs every 15 s.
- **Automation:** an opt-in late-night schedule (ADR-0019) and opt-in power on/off when the Steam device wakes or sleeps.
- **Volume scales** are dB, percent or bare steps (ADR-0015). Caps and late night are bound to the receiver via each driver's identity keys (ADR-0014).
- **Store readiness:** checklist in `docs/STORE-SUBMISSION.md`. CI builds with pnpm 12 like the store (ADR-0017).

## Next

1. **Owner hardware check on the VSX-935:**
   - install the current build and verify normal receiver connection and controls
   - push sync: change the volume with the remote while the panel is open; the slider should follow within about 1 s
   - the late-night schedule across a boundary
   - Steam sleep/wake power
   
   Record results as **[measured]** in `drivers/eiscp.md`.
2. **Collect transcripts** from owners of every experimental driver via Receiver report issues, including push behaviour. Promote a driver once a transcript replays.
3. **Features** in value-per-effort order:
   - soft-start volume ramp
   - cancellable sleep timer
   - incoming signal format (eISCP `IFA`, unverified)
   - Yamaha YXC UDP event push
4. **Store submission:** the owner's steps are in `docs/STORE-SUBMISSION.md` (Stable/Beta testing, screenshots, license provenance, database PR).
5. **More brands** from `docs/research/avr-brand-feasibility.md`, when owners ask and can send a transcript: Anthem, Arcam, BluOS.
6. **Test-suite speed:** improve backend test turnaround by injecting clocks or shortening timeouts in the fakes.

## Not planned

- Per-game input switching. Video passes through the AVR, so it is already on the right input (ADR-0001).
- Patching Steam's native volume UI. It's fragile across client updates. A supported global input hook could be revisited.
- Harman Kardon, McIntosh AV, Google Cast, TVs and host HDMI-CEC: they can't offer the full receiver feature set, or they need heavy protocols (research doc).
- LG ThinQ (cloud). The local webOS SSAP route is possible but is TV control, not receiver control (`docs/research/avr-brand-feasibility.md`).
