# Design and feature options

Evidence is from the requested source and test files. The repository has real
receiver evidence only for a Pioneer VSX-935 using eISCP; Denon, Yamaha,
Home Assistant, and legacy Pioneer are simulator-verified (`AGENTS.md:33–35`,
`docs/adr/0013-transcript-fixtures-for-unverified-brands.md:1–10`). “Verified”
below means the existing path has hardware evidence, not that a proposed
extension has been tested. Driver facts remain tagged by source
(`docs/adr/0011-protocol-specs-next-to-drivers.md:1–5`).

## Part 1 — Deepening opportunities (design)

### 1. Shared owned TCP session for receiver drivers

**Cluster and evidence.** `py_modules/avr_control/drivers/eiscp.py:64–110,114–178,267–298`;
`py_modules/avr_control/drivers/denon.py:67–86,135–182,184–248`;
`py_modules/avr_control/drivers/pioneer_telnet.py:14–46,63–92`;
`py_modules/avr_control/drivers/base.py:108–196`.
eISCP frames packets and ignores the measured NJA unsolicited album-art
message; it is not line-framed (`py_modules/avr_control/drivers/eiscp.md:14–22,77–79`).
Denon and legacy Pioneer read CR-terminated lines
(`py_modules/avr_control/drivers/denon.md:12–22`;
`py_modules/avr_control/drivers/pioneer_telnet.md:16–18`).
Denon’s documented one-client constraint and eISCP’s measured one-client
reliability make exclusive ownership useful
(`py_modules/avr_control/drivers/denon.md:10–22,55`;
`py_modules/avr_control/drivers/eiscp.md:14,77`).

**What is shallow or leaking.** Each driver owns its own open/read/close,
timeouts, matching, and error conversion. Denon opens a fresh connection for
`status()` and for each setter; Pioneer opens one for every `_call`, and
`status()` makes three sequential calls. eISCP has separate `_query` and
`_multi` transaction loops. These implementations do not coordinate
concurrent calls or expose a receiver-event stream. Denon’s 50 ms command
spacing, eISCP’s batch replies, and Pioneer’s port fallback are protocol
rules, not rules callers should learn.

**Deeper module and seam.** Add an internal `ReceiverSession` below `Driver`,
not a new public driver contract:

```python
await session.transact(
    writes: Sequence[bytes],
    classify: Callable[[Frame], str | None],
    required: frozenset[str],
    *,
    timeout: float,
    min_interval: float = 0,
) -> Mapping[str, Frame]
session.subscribe(handler: Callable[[Frame], None]) -> Unsubscribe
await session.close()
```

Invariant: one owner serializes transactions on a receiver connection; each
driver supplies its own framing/response classifier and required reply set;
protocol-specific spacing, optional-reply grace, port fallback, and refusal
semantics remain explicit in the driver. The session owns stream lifecycle,
timeouts, cancellation cleanup, and delivery of unrelated frames to subscribers.
An EOF/timeout invalidates the stream; the next transaction reconnects once and
returns a `DriverError` if it still fails. A pushed frame is never mistaken for
a command reply.

**Moves, adapters, tests.** Move repeated stream ownership and transaction
plumbing out of the three drivers; do not merge eISCP packet framing with text
line framing or move command knowledge out of drivers. The seam belongs at
`py_modules/avr_control/drivers/receiver_session.py`. Production uses an asyncio-stream adapter;
tests can use an in-memory duplex-stream adapter (a genuine second adapter).
Keep the loopback fake receivers for framing/firmware-like integration and the
transcript replay tests for captured wire compatibility
(`tests/test_drivers.py:14–108`, `tests/test_denon.py:25–184`,
`tests/test_transcripts.py:71–110,132–162`). Add interface tests for concurrent
commands, event interleaving, timeout/cancel cleanup, close/reconnect, and
Denon spacing. **the interface is the test surface**; don't assert private
reader-task state.

**Risks / effort / payoff.** **L / high.** Keeping a persistent connection can
go stale, compete with the receiver app, or change firmware behavior; retaining
fresh-per-transaction operation is a viable first version. eISCP is framed,
Denon/Pioneer are line-based, and Yamaha/HA use HTTP, so this module must stay
TCP-specific and not become an all-transport abstraction. **the deletion test**:
without it, serialization, reconnect, event routing, and cancellation handling
reappear in three drivers. This is the strongest locality win and the enabling
seam for push sync.

#### Design it twice: session interface

**Option A — transaction channel (recommended).** A driver submits a group of
wire writes plus a response classifier and required reply keys; `transact`
returns the matched frames. `subscribe` is optional and `close` is idempotent.
Ordering, deadlines, and single-owner rules live in one module, while each
driver retains its command grammar. Depth is high for shared I/O behavior;
locality is good because protocol semantics stay next to each driver. The seam
is a byte-stream factory with production asyncio and in-memory adapters.

**Option B — receiver actor.** The driver creates a long-lived actor with a
small command-intent surface:

```python
await receiver.execute(Command("set_volume", raw=72))
snapshot = await receiver.snapshot()
async for change in receiver.changes():
    ...
```

The actor owns connection, commands, state cache, retries, and push/poll
reconciliation. This has the smallest caller interface and potentially the most
leverage, but each protocol must map a broad shared command vocabulary and
define cache freshness, partial state, and unsupported operations. eISCP/Denon
can send unmatched events; the current Yamaha/HA paths are request/response.
The seam moves above protocol drivers, requiring protocol actor adapters and an
in-memory actor adapter. That seam is not justified until multiple drivers
actually expose useful state pushes.

**Comparison.** A has sufficient depth without claiming the wire protocols are
the same; B centralizes more behavior but leaks less protocol-specific
ordering only by introducing more shared concepts. A puts the seam at stream
ownership, B at receiver semantics. Start with A; add a `changes()` facade only
after a real state-event adapter exists. A single adapter would make either
seam hypothetical; production plus in-memory adapters make A real.

### 2. One deep control-application module, with a smaller RPC interface

**Cluster and evidence.** `py_modules/avr_control/controller.py:27–69,92–124,
125–375,381–483,484–632,634–656`; `main.py:23–103`;
`src/index.tsx:67–85,154–234`.
The 19 frontend callables mirror `Plugin` methods. `get_state()` already
returns a broad snapshot, but the UI separately fetches host, driver metadata,
profiles, discovery, and command results. The controller handles caps,
receiver detection/selection/rediscovery, game profiles, late-night behavior,
and driver errors. These behaviors are coupled: profile restore is clamped by
the active cap, bound to a driver, and followed by a delayed mode reapply
(`py_modules/avr_control/controller.py:508–558`; ADR-0004 and ADR-0012). The controller is large, but
its current interface is meaningful rather than a pass-through.

**What is shallow or leaking.** `main.py` repeats named delegators and
`index.tsx` repeats their typed callables. UI code must coordinate individual
calls and refreshes. Yet replacing them with `rpc(name, dict)` would only hide
names, not behavior, and would erase useful type checking. Internally, the
controller’s cap math, persistent settings, rediscovery, and profile rules
share state and ordering. Splitting one module per noun risks moving that
coordination across more interfaces.

**Deeper module and seam.** Keep one application module responsible for
receiver selection, cap and late-night invariants, game-profile precedence,
driver errors, and snapshots. Make its external interface a snapshot and a
typed intent dispatcher, with discovery as a read operation:

```python
snapshot() -> Snapshot
dispatch(intent: ControlIntent) -> Outcome
discover() -> list[Receiver]
```

`ControlIntent` is a discriminated set of volume/mute/power/mode, late-night,
game lifecycle/profile, receiver configure/select, and profile-forget intents;
it is not a string command plus arbitrary dictionary. A completed state-changing
intent returns a consistent snapshot or a typed error. `game_started` may
schedule a delayed reapply, but the application owns/cancels that work when the
game or receiver changes. Keep `VolumeScale` conversion and settings persistence
as internal implementation modules, not caller-visible policy modules.

**Moves, adapters, tests.** Decky remains the sole adapter translating RPC
calls to the application interface; React sends typed intents and renders
snapshots. Keep an explicit `discover()` because it returns a result list, not
just a state mutation. Use the existing injected `MemoryDriver` adapter and a
memory settings adapter in application tests; retain a small Decky smoke test
for RPC registration (`tests/test_controller.py:41–111,146–548`;
`tests/test_main_smoke.py:25–65`). Replace piles of method-specific frontend
stubs with behavior tests at the snapshot/intent seam. **the interface is the
test surface**: assert cap clamping, driver mismatch, stable-ID rediscovery,
game opt-in, and late-night restoration through intents and snapshots.

**Risks / effort / payoff.** **M / high.** A tagged intent can still become a
large switch; do not route every simple query through a generic dispatcher.
Preserve `DriverError` user messages and ensure command results correspond to
the receiver selected when the command began (the existing generation check
guards this at `py_modules/avr_control/controller.py:174–188,233–253`). Avoid extracting
`ReceiverManager`, `ProfileManager`, `LateNightManager`, and `CapManager` merely
to shrink the 656-line file: those interfaces would have to know each other’s
ordering and state.

#### Design it twice: application interface and controller shape

**Option A — one deep `ControlApplication` (recommended).** `snapshot()`,
`dispatch(ControlIntent)`, and `discover()` are the only caller-facing
operations. It owns sequencing and policy; small pure calculations and the
settings store are internal. The seam is at the application, with Decky as
production adapter and in-memory driver/store adapters for tests. High depth
and leverage for UI callers; locality stays with cross-cutting game/receiver
rules. **the deletion test**: deleting it makes cap enforcement, game restoration,
rediscovery, and late-night safety reappear in both `main.py` and React.

**Option B — several domain modules.** Expose `ReceiverDirectory.resolve /
discover / select`, `VolumePolicy.set / cap / late_night`, and
`GameProfiles.started / stopped / remember / forget`, composed by a thin
controller. Each interface is locally small, but profile restore calls into
receiver identity and volume-cap logic; late night changes what a game profile
may save. The composition layer must still know sequencing, error handling,
and snapshot assembly. The seams are in-process and need separate adapters
only where dependencies genuinely vary; otherwise these are extra interfaces
with one adapter and lower locality.

**Comparison.** Option B is more modular in file layout but can be shallower
for callers because the same cross-module invariants remain theirs to
coordinate. Option A has the smaller public interface and stronger depth.
Prefer A, extracting only pure, independently useful computations internally.
If later a profile policy gets a second caller (for example a scheduler), then
give that behavior a real seam and a second adapter.

### 3. Settings schema and migration contract

**Cluster and evidence.** `py_modules/avr_control/settings.py:11–65,67–138`;
`py_modules/avr_control/controller.py:57–72`;
`main.py:11–21`; `tests/test_settings.py:12–73`;
ADR-0010 and ADR-0012. `migrate()` converts the old flat schema and returns
version 2 data unchanged. The controller separately fills defaults in a
mutable dict. Receiver secrets, raw-volume caps, late-night previous volume,
and driver-bound game profiles all have invariants.

**What is shallow or leaking.** The JSON store owns disk atomicity and legacy
file adoption, but the application still knows schema defaults and mutates
arbitrary dictionaries. There is no validation/normalization pass for malformed
version-2 nested values, and schema evolution becomes scattered if more
features add settings.

**Deeper module and seam.** Give a `SettingsRepository` the interface
`load() -> Settings` / `save(Settings) -> None`, where `Settings` is a
normalized domain value. Invariants: load always returns the current schema;
migrations are ordered and idempotent; unknown optional fields survive where
safe; invalid values are clamped or rejected consistently; saves remain atomic;
tokens never appear in errors/logs. Keep legacy paths injected at construction.
Do not introduce a general migration framework for only one conversion.

**Moves, adapters, tests.** Move defaults, validation, version dispatch, and
the old-plugin adoption rule behind the repository; the application stops
editing nested dictionaries. The filesystem store and an in-memory store are
two real adapters at the persistence seam. Replace migration-only assertions
with repository contract tests that run against both adapters, retaining
legacy adoption and save-failure coverage (`tests/test_settings.py:13–73`,
`tests/test_main_smoke.py:67–97`). **the interface is the test surface**:
load/save round trips preserve profiles, secrets, receiver IDs, and late-night
restore state.

**Risks / effort / payoff.** **M / medium.** Stronger validation can discard
real user data if unknown fields are silently stripped; migration must be
conservative and preserve raw volume/profile driver binding. Useful before
adding time profiles or channel trims, but not a reason to redesign storage
before the next schema change.

### 4. One canonical volume scale and cap calculation

**Cluster and evidence.** `py_modules/avr_control/drivers/base.py:34–64`;
`py_modules/avr_control/controller.py:92–123,381–423,425–459`;
`src/index.tsx:16–34,87–105,280–333`.
ADR-0009 deliberately keeps raw volume at the driver seam and the raw scale in
settings/profiles. Python `VolumeScale` has `display()` and `to_raw()`, while
TypeScript independently implements display and text-to-raw conversion.
Controller owns base-cap, late-cap, effective-cap and clamp policy.

**What is shallow or leaking.** There are two implementations of
`(raw-zero)/steps_per_unit` and the inverse; frontend `Math.round` and Python
`round` need not agree at ties. The UI also owns display-unit parsing and
clamping, while the backend owns authoritative cap clamping. A refactor that
normalizes drivers to dB would break percentage-only Home Assistant and the
raw-profile round trip rejected by ADR-0009.

**Deeper module and seam.** Keep raw volume inside the driver/application
contract, but make one backend `VolumePolicy` authoritative:
`to_raw(display_value, scale)`, `to_display(raw, scale)`,
`effective_cap(settings, scale)`, and `clamp(raw, cap, scale)`. Prefer a
small value object whose interface includes bounds, unit, step, and rounding
rules; return display-unit caps/snapshots to the UI so React formats labels
rather than re-deriving receiver math. Preserve integer raw profiles and
driver-specific scale discovery.

**Moves, adapters, tests.** Move cap and conversion rules to Python; keep
React’s locale-aware text parsing only if needed, passing a display number
through the interface. This is pure in-process behavior: no adapter or new
seam is warranted. Replace duplicated example tests with interface tests for
round trips, half-step ties, negative dB, min/max, percentage scales, and
late-cap precedence. Existing scale tests live in
`tests/test_controller.py:146–169`, `tests/test_denon.py:190–210`,
`tests/test_yamaha.py:173–234`, and `tests/test_homeassistant.py:152–176`.

**Risks / effort / payoff.** **M / high.** A UI contract change touches every
volume slider/cap and saved profile display; preserve raw wire values and make
the new conversion behavior explicit. **the deletion test**: delete either copy
of the math and the other currently has to absorb it; after deepening,
conversion and policy change in one place.

### 5. Push updates versus the 2-second poll

**Cluster and evidence.** `src/index.tsx:154–156,219–234`;
`py_modules/avr_control/controller.py:381–423`;
`py_modules/avr_control/drivers/eiscp.py:56,83–110,114–178`;
`py_modules/avr_control/drivers/denon.md:12–22`;
`tests/test_drivers.py:23–38`; `tests/test_denon.py:283–288`.
React polls `get_state()` every two seconds unless a local command just ran.
eISCP currently discards NJA album-art packets while awaiting replies; Denon
may interleave unsolicited lines, but its driver opens short-lived
connections. Neither is currently a state subscription.

**What is shallow or leaking.** Polling, debounce, last-local-write suppression,
and UI refresh scheduling are frontend knowledge. Polling still makes each
snapshot perform receiver I/O and provides at best two-second remote-change
latency. An event channel would have to define initial snapshot, event ordering,
lost-event recovery, receiver changes, and unload.

**Deeper module and seam.** Extend the application interface only if a driver
can provide genuine state events: `subscribe() -> AsyncIterator[Snapshot]`,
with an immediate snapshot, monotonic revisions, serialized command/event
ordering, and a documented resync after disconnect. Drivers without push
support return “no subscription”; the application retains bounded polling.
Do not equate NJA album-art pushes with volume/mode changes.

**Moves, adapters, tests.** Move remote-change reconciliation from React into
the application; React renders snapshots and sends intents. The receiver
protocol adapter and an in-memory event adapter are two adapters only after at
least one driver can produce real state events. Test ordering, reconnect,
missed updates, and fallback polling through the subscription interface; keep
transcript replay for wire claims. At present this is a **conditional M/L**
effort, not an implementation shortcut.

**Risks / payoff.** High responsiveness and fewer repeated status queries if
the receiver really pushes state. eISCP’s measured push is album metadata,
not state; Denon event behavior is community-sourced, and Yamaha YXC/HA paths
currently use HTTP polling. First capture state changes from real receivers.
**the deletion test**: a good subscription module makes event ordering disappear
from React and drivers; a wrapper around a timer just adds a shallow interface.

### 6. Listening-mode tables and selectable-mode semantics

**Cluster and evidence.** `py_modules/avr_control/drivers/base.py:68–71,167–173`;
`py_modules/avr_control/drivers/eiscp.py:30–54,324–328`;
`py_modules/avr_control/drivers/denon.py:30–50,287–320`;
`py_modules/avr_control/drivers/yamaha.py:130–157,342–379,471–476`;
`py_modules/avr_control/controller.py:381–423,606–627`; `src/index.tsx:370–391`;
ADR-0005. eISCP uses a measured fixed name table, Denon has a static common
settable list, Yamaha fetches per-zone programs, and Home Assistant fetches an
entity’s mode list.

**What is shallow or leaking.** “Mode” covers receiver-specific opaque codes,
display labels, queryable-but-not-settable current modes, and mode lists that
may be incomplete. `Driver.list_modes()` and `mode_name()` already centralize
some of this; `list_games()` constructs a driver with empty config just to
resolve a stored code. The frontend also synthesizes a fallback entry for an
unknown current code.

**Deeper module and seam.** Keep codes opaque and driver-owned (ADR-0005).
Clarify the interface as `ModeState(current: ModeCode | None,
selectable: Sequence[ModeOption], complete: bool)`, where the driver says
whether the list is authoritative; a current unknown code remains displayable
but is not automatically settable. Resolve labels from the driver catalog
without constructing a configured receiver. Avoid a global mode-name table:
codes collide across drivers.

**Moves, adapters, tests.** Move fallback/current-vs-selectable presentation
rules behind the driver/application seam; retain each driver’s static or
runtime source. Production adapters remain each driver; tests use driver
fixtures/fakes, and protocol replay remains useful. Test unknown current codes,
partial lists, refused selection, and driver-bound saved profiles at the
interface (`tests/test_drivers.py:23–38`, `tests/test_denon.py:221–228`,
`tests/test_yamaha.py:173–208`, ADR-0012). This is mostly in-process; no new
adapter seam is needed.

**Risks / effort / payoff.** **S/M / medium.** A “complete list” flag can
mislead where vendor docs do not guarantee supported-mode enumeration.
Preserve raw codes, unknown fallback display, and mode refusal semantics.
Locality improves modestly; do this alongside a feature that needs richer mode
information rather than as an isolated framework.

## Part 2 — Feature options that fit

Hardware evidence is constrained: current eISCP volume/discovery behavior has
VSX-935 evidence, while Denon, Yamaha, legacy Pioneer, and Home Assistant are
simulator-only (`AGENTS.md:33–35`). “eISCP path” means the current driver can
carry the relevant existing command where noted; it does not mean a new command
or user-facing feature has been validated. Ratings are value / effort; rows
are ordered approximately by value per effort.

| Rank and feature | User value; seam; effort and risk | eISCP | Denon | Pioneer telnet | Yamaha | Home Assistant |
|---|---|---|---|---|---|---|
| **1. Soft-start / volume ramp** | Avoids abrupt volume jumps after resume, profile restore, or a slider drag. Application `set_volume` path; ramp bounded by effective cap, cancellable on receiver/game change. **High / M.** Risk: receiver accepts commands more slowly than cadence; rate-limit and confirm only final value. | Existing absolute setter; scale/discovery evidence on VSX-935, ramp itself untested. | Existing setter; simulator only. | Existing setter; simulator only. | Existing setter and runtime scale; simulator only. | Existing HA volume service and confirmation; simulator only. |
| **2. Sleep timer** | Simple couch feature: turn the receiver off after a user-selected delay. Application owns a cancellable timer and calls driver `set_power(False)`; survive plugin unload only if deadline is persisted. **Medium / S–M.** Risk: surprising shutoff; visible countdown/cancel and opt-in required. | Power setter exists; power command semantics are still assumed in `py_modules/avr_control/drivers/eiscp.md:36,85–88`. | Power setter; simulator only. | Power setter; simulator only. | Power setter; simulator only. | Only if the HA entity advertises turn-off; simulator only. |
| **3. Time-of-day listening/volume profiles** | Quiet-hours cap or evening sound mode fits a receiver used in a shared living room. Extend late-night/profile policy, with explicit precedence against opt-in game profiles. **Medium / M.** Risk: local time/DST, profile conflicts, and changing mode during gameplay. | Existing cap/mode paths; schedule feature untested. | Existing cap/mode paths; simulator only. | No mode capability; volume only; simulator only. | Dynamic mode list; simulator only. | Entity-dependent modes; simulator only. |
| **4. Push-based live sync** | Show remote/app changes faster and stop asking the AVR for a full status every two seconds. Use `ReceiverSession` and application snapshots, retaining poll fallback. **High if supported / L.** Risk: current eISCP NJA is album art, not state; event ordering, keep-alive, and reconnect need proof. | Measured unsolicited NJA only; no verified pushed volume/mode state. | Community reports interleaved event lines; driver currently closes each connection; simulator only. | No push path in current implementation; simulator only. | YXC implementation is HTTP request/response; no push path established here; simulator only. | Current REST state reads; no push path established here; simulator only. |
| **5. Dolby/DTS signal-info display (`IFA` candidate)** | Tell players what the AVR is decoding (codec/input signal), useful when a game’s audio format is unexpected. Add optional driver `audio_info` to snapshot; probe read-only and preserve unknown strings. **Medium / M.** Risk: protocol/model-specific response and sensitive input labels. | `IFA` is not implemented in `py_modules/avr_control/drivers/eiscp.py` or the current driver interface; verify query/reply on VSX-935 before calling it supported. | Current status has no input-format field; simulator-only research. | No signal-info capability. | Current YXC status only reports volume/mute/power/program; simulator-only. | Only if entity attributes expose it; no current mapping. |
| **6. AVR dynamic-range/dialogue enhancement** (`LTN`, Denon `PSDRC`, Yamaha candidate) | Real receiver DSP control for quiet dialogue/night viewing; distinct from the existing plugin “late night mode,” which only caps and restores volume (`py_modules/avr_control/controller.py:634–656`, `CONTEXT.md:54`). Add a separate capability and profile toggle only after read/write semantics are verified. **Medium / M–L.** Risk: model/input-specific settings and unexpectedly altered sound. | `LTN` is not implemented or hardware-verified in this driver. | `PSDRC` not implemented; simulator-only. | No known implementation. | No corresponding setting in this driver; YXC support must be established per model. | Could expose a sound mode, not a standardized DRC control; simulator-only. |
| **7. Per-game center/subwoofer trims** | Match dialogue and bass to game mix. Extend opt-in `GameProfile` only after a driver can report and set each trim; keep driver binding and restore safety. **Medium / L.** Risk: channel names/ranges differ, many receivers reject channel writes for current input, and per-game loudness can surprise. | Current capabilities omit channel trims; no verified command in the repo. | Not in current driver; simulator-only. | Not supported. | Not in current driver; simulator-only. | Entity-specific; no generic media-player trim. |
| **8. Wake receiver on Steam resume** | AVR is ready when the Deck wakes. Hook a real SteamOS resume signal, then issue `set_power(True)` only if configured and currently off. **Medium / M–L.** Risk: no host-resume hook in current plugin; avoid waking on every game notification or when receiver power state is unknown. | Existing `set_power`; protocol’s flag semantics are assumed, so validate before enabling by default. | Existing setter, simulator only. | Existing setter, simulator only. | Existing setter, simulator only. | Entity feature-dependent, simulator only. |
| **9. Steam volume buttons / global hotkey** | Direct couch control without opening Quick Access. Requires an actual global input seam; a patch to Steam’s native volume UI is a brittle client-internal dependency. **High potential / L, not recommended first.** Risk: Steam updates, hijacked system volume/CEC, game focus and accessibility. Prefer a supported Decky input hook if one exists; don’t ship a native UI patch. | Existing absolute volume command path; key interception is unverified. | Existing setter, simulator only. | Existing setter, simulator only. | Existing setter, simulator only. | Existing HA volume path, simulator only. |
| **10. Zone 2** | Control a second room from the couch, but it is less central to gaming. Add zone as an explicit receiver configuration/selection, not an implicit driver-specific branch. **Low–medium / L.** Risk: zones have independent power/caps/volume scales; existing Yamaha driver hardcodes `main` (`py_modules/avr_control/drivers/yamaha.py:342–370`). | No Zone 2 capability in current driver. | Current `PW` is global, not zone power (`py_modules/avr_control/drivers/denon.md:34–37`). | No Zone 2 implementation. | Vendor API has zones, but current driver only addresses `main`; simulator-only. | A separate configured entity may work; simulator-only. |
| **11. HA “anything with volume” setup** | Reuse HA to control non-AVR media players without writing a protocol driver. This is largely already implemented: one `media_player` entity, with capabilities from `supported_features` (`py_modules/avr_control/drivers/homeassistant.py:49–94,169–218`). Improve onboarding and call it a partial “HA volume device” route, not arbitrary receiver support. **High coverage / S** for onboarding; broader generic service mapping is **L**. Risk: tokens and integrations with different capability/state behavior. | Not applicable as a driver. | Not applicable. | Not applicable. | Not applicable. | Existing implementation; only a local fake HA server has been tested (`tests/test_homeassistant.py:35–149`). |
| **12. Store-submission readiness** | Make support expectations legible: capability matrix by model/firmware, source-tagged protocol facts, first-run setup, diagnostics/privacy guidance, screenshots, and experimental labels for unverified drivers. Preserve release version source and workflow (`docs/adr/0007-package-json-version-source-of-truth.md:1–9`; `AGENTS.md:35,51–52`). **High release value / S–M.** Risk: claims outrun hardware evidence; no new protocol feature required. | Mark VSX-935 as the one hardware-tested receiver; distinguish feature-specific verification. | Explicitly experimental/simulator-only. | Explicitly experimental/simulator-only. | Explicitly experimental/simulator-only. | Explicitly simulator-only. |

**Not recommending game input selection:** ADR-0001 records that the Steam
device’s video path makes CEC input selection irrelevant to the original use
case (`docs/adr/0001-control-receiver-over-lan-not-cec.md:3–7`). An audio-only
source-selection use case is possible, but there is no evidence it solves a
current user problem; do not add per-game input profiles without that distinct
need.

## Prioritised recommendation — top five overall

1. **Deepen the TCP session seam.** One owned connection/transaction module
   removes repeated lifecycle and concurrency rules and is the prerequisite
   for safe event handling.
2. **Keep one deep application module with snapshot + typed intents.** It
   centralizes profile, cap, receiver, and late-night ordering without
   scattering those invariants across shallow managers or React.
3. **Add soft-start volume ramping.** It improves safe, comfortable couch use
   across existing driver volume setters without inventing a new protocol.
4. **Add a cancellable sleep timer.** It is a small, recognizable living-room
   feature that reuses the existing power interface, with explicit opt-in.
5. **Validate receiver push support before implementing live sync.** It could
   remove the two-second poll, but the current eISCP push is album metadata
   and other driver claims are not hardware-verified.
