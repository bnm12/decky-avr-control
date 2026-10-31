# Design and feature options

Current code status as of 2026-10-31. Implemented designs are summarized for
reference; the remaining sections describe decisions that still need product
or protocol work. Only the eISCP control path has real-hardware verification,
on a Pioneer VSX-935; all other drivers are experimental. The TCP push paths,
including eISCP remote-state events, have not been verified on hardware.
Protocol facts retain the source tags described in ADR-0011.

## Part 1 — Implemented design options

1. **Shared TCP receiver session.** `drivers/receiver_session.py` serializes
   transactions and routes unsolicited frames for eISCP, Denon telnet, legacy
   Pioneer, and HEOS; framing remains in each driver. ADR-0016.
2. **One deep control application.** `application.py` owns snapshots and typed
   intents, with the RPC contract mirrored in `src/api.ts`; `main.py` is the
   Decky adapter. ADR-0018.
3. **Settings schema and migrations.** `settings.py` provides normalized
   settings through filesystem and in-memory repositories; the current schema
   is v4. Receiver-bound caps and game profiles are covered by ADR-0014 and
   ADR-0012.
4. **Canonical volume scale and cap policy.** `drivers/base.py` defines raw
   scales; `volume.py` owns display conversion and cap math. The `steps` unit
   avoids inventing dB or percent mappings. ADR-0009 and ADR-0015.
5. **Push updates with polling fallback.** The application subscribes only
   while the panel is open; `useAvr.ts` polls every 2 seconds without push and
   resynchronizes every 15 seconds with it. See `receiver_session.py` and
   ADR-0016.
6. **Listening-mode state.** Drivers report current mode, selectable options,
   and whether that list is complete through `ModeState` in `drivers/base.py`;
   codes and labels remain driver-specific. Game profiles remain
   driver-bound under ADR-0012.

The useful design constraints are recorded in ADR-0016 and ADR-0018: keep the
TCP transaction seam below protocol drivers rather than inventing a shared
receiver actor, and keep cross-cutting application policy together rather
than splitting it into managers that must coordinate the same invariants.

## Part 2 — Feature options

### Implemented

3. **Time-of-day listening/volume schedule.** `application.py` applies the
   opt-in schedule and late-night policy; `ScheduleSection.tsx` configures it.
   Boundary and manual-override precedence is recorded in ADR-0019.
8. **Power on wake / off on sleep.** `main.py` forwards Steam resume and
   suspend signals; `application.py` handles opt-in commands and retries,
   configured in `SleepWakeSection.tsx`.
11. **Home Assistant media-player setup.** The `homeassistant` driver exposes
   entity-dependent capabilities and setup options; `ReceiverSection.tsx`
   includes “Find media players.” The route is experimental and depends on
   the selected entity's features.
12. **Store-readiness groundwork.** `docs/STORE-SUBMISSION.md` contains the
   checklist; source-tagged specs, experimental-driver labeling, transcript
   fixtures (ADR-0013), and the store-compatible pnpm build (ADR-0017) are in
   place. This is readiness work, not an accepted or completed store
   submission.

### Remaining options

#### 1. Soft-start / volume ramp

**Status: not implemented.** The application currently sends volume targets
through its immediate set-volume path; `VolumePolicy` clamps each target to
the receiver's effective cap, but there is no ramp task or ramp intent.

**Value and seam.** A gradual change could soften abrupt jumps during profile
restore or another explicitly chosen transition. Keep sequencing in
`application.py`, not the UI or individual drivers, since the application
already owns receiver generations, intents, and the effective cap.

**Decisions and risks.** Define which transitions should ramp (a slider drag
should not necessarily queue many writes), step size and cadence for different
scales, how failures are reported, and how a ramp is cancelled on receiver
change, a newer volume command, or shutdown. Clamp every intermediate target;
test behavior on half-dB and integer scales before selecting a default cadence.
The existing absolute-volume setters make this feasible, but receiver command
rate and user expectations have not been validated.

#### 2. Cancellable sleep timer

**Status: not implemented.** Steam suspend/wake automation is separate from a
user-selected countdown; there is no sleep-timer intent, countdown, or panel
control. Existing `set_power(False)` support is driver- and entity-dependent.

**Value and seam.** A couch-facing timer could turn the receiver off after an
opt-in delay. Put deadline ownership, cancellation, and receiver-generation
checks in `application.py`; expose remaining time and cancel/set actions in the
snapshot and UI.

**Decisions and risks.** Decide whether the deadline survives plugin unload or
Steam restart, what receiver selection changes do to it, and how to handle
unknown power state or unavailable power-off capability. Make the deadline
visible and cancellation immediate: an unexpected power-off is worse than a
missed timer.

#### 4. Push-based live sync: validate the implemented path

**Status: implemented for supported TCP drivers; hardware validation
pending.** eISCP, Denon telnet, legacy Pioneer, and HEOS have watch paths
through `ReceiverSession` and `application.py`. They run only while the panel
is open; the UI retains the 15-second full-status resync. HTTP drivers,
including Denon's HTTP fallback, remain polling-only.

**Evidence and next decision.** The eISCP VSX-935 connection has a measured
unsolicited album-art frame, not a measured remote volume/mode event. Denon,
Pioneer telnet, and HEOS event behavior comes from specs or community
evidence. Before treating push as dependable, capture a real remote-control
change while the panel is open, then check reconnects, dropped events, and
panel close. Keep polling fallback and the resync until those behaviors are
measured. This is validation of existing code, not a proposal to add a push
architecture.

#### 5. Dolby/DTS signal information (`IFA` candidate)

**Status: not implemented.** The current eISCP driver and `Status` contract do
not expose an incoming-format field. Other drivers likewise have no common
signal-info capability.

**Value and seam.** Displaying the detected codec or input format could help
explain unexpected game audio. First establish the eISCP `IFA` query/reply
and field semantics on the VSX-935; then add an optional, read-only field to
the driver/application snapshot while preserving unknown receiver strings.

**Decisions and risks.** Confirm availability in standby and across input
types, and avoid presenting an assumed signal label as measured fact. Keep
the initial scope eISCP-specific unless another driver has equivalent
evidence; do not make a speculative cross-brand abstraction.

#### 6. Dynamic-range / dialogue enhancement

**Status: not implemented.** The plugin's existing “late night” behavior is a
volume cap and restore policy, not receiver DSP control. There is no
`LTN`, Denon `PSDRC`, or Yamaha dynamic-range command in the current drivers.

**Value and seam.** A distinct receiver capability could help quiet-dialogue
listening, but it should not be conflated with scheduled or manual late-night
volume limits. Add an explicit capability and intent only after a driver's
read/write state is verified on named models.

**Decisions and risks.** Determine whether the control is a boolean, a set of
named modes, or input/signal-dependent; verify round-trip behavior and refusal
semantics. A mistaken DSP change can alter sound unexpectedly, so keep it
opt-in and separate from game-profile restore until its behavior is clear.

#### 7. Per-game center / subwoofer trims

**Status: not implemented.** No driver exposes channel trims, and
`GameProfile` has no trim fields.

**Value and seam.** Per-game trim could tailor dialogue and bass, but receiver
commands and channel names vary. Start with one target model and decide
whether trims should be global receiver settings or profile data before
changing `settings.py` or the intent contract.

**Decisions and risks.** Establish readable current values, ranges, input
restrictions, and safe restore behavior on hardware. Preserve
receiver/driver-bound profile rules, and do not write per-game values where
the receiver cannot confirm them.

#### 9. Steam volume buttons / global hotkey

**Status: not implemented.** The plugin exposes the Quick Access panel; it
does not intercept Steam volume buttons or provide a global input hook.

**Value and seam.** Direct controls could improve couch use, but depend on a
supported Decky/Steam input seam that is not established by this repository.
Revisit only if such a seam and a concrete interaction design are available.

**Decisions and risks.** Do not patch Steam's native volume UI: that is brittle
across client updates and may interfere with system volume, HDMI-CEC, focus,
or accessibility. Prefer an official input hook if one becomes available.

#### 10. Zone 2

**Status: not implemented.** Yamaha YXC advertises multiple zones, but
`yamaha.py` currently selects and controls only `main`. Denon's current
volume/status path is main-zone and its power command is global. The common
driver contract has no zone selection.

**Value and seam.** Multi-room control could help users with a second listening
area, but first determine whether a target driver and model expose useful
zone controls. Any implementation should make zone selection explicit in
receiver setup rather than silently changing the existing main-zone route.

**Decisions and risks.** Zones may have independent power, volume scales, and
capabilities. Bind saved caps and other zone-specific settings to both
receiver and zone, and verify that standby or a zone switch cannot redirect a
command to the wrong output.

## Prioritised recommendation — remaining work

1. Validate the existing experimental TCP event paths with owners, beginning
   with a VSX-935 push check; keep the safety resync and polling fallback.
2. Add soft-start volume changes and a cancellable sleep timer only after
   defining cap, cancellation, receiver-change, and persistence behavior.
3. Investigate eISCP `IFA` on real hardware before adding signal information.
4. Research Yamaha YXC UDP notifications as a separate protocol question;
   the current YXC driver is HTTP polling-only.
5. Defer DRC/dialogue controls, channel trims, hotkeys, and Zone 2 until a
   named model and user need justify their protocol and UI scope.
