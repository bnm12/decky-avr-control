# AGENTS.md

AVR Control is a Decky Loader plugin: a Quick Access panel on a Steam Deck / Steam Machine controlling a network AV receiver. Read `CONTEXT.md` for the domain vocabulary and use its terms in code, docs and UI text. `docs/ROADMAP.md` holds the current state and the next steps; read it before starting work and update it when you finish a slice.

## Layout

- `main.py`: thin Decky adapter exposing `snapshot`, `dispatch`, `discover`, `list_drivers` and `setup_options`, and publishing pushed snapshots with `decky.emit`. It is the only Python file that imports `decky`.
- `py_modules/avr_control/`: the backend, on `sys.path` because Decky adds `<plugin>/py_modules` (the plugin folder itself is not on it).
  - `application.py`: one deep `ControlApplication`; its interface is `snapshot`, typed `dispatch`, `discover`, `drivers`, and `setup_options`.
  - `volume.py`: the canonical raw/display conversion and cap policy used by the application.
  - `drivers/base.py`: the driver seam (ADR-0009). `drivers/__init__.py` holds `REGISTRY`, which also sets the probe order.
  - `drivers/<key>.py` + `drivers/<key>.md`: one driver and its protocol spec (ADR-0011).
  - `settings.py`: `SettingsRepository` (filesystem and in-memory stores) for persisted user settings.
  - `drivers/receiver_session.py`: the shared TCP transaction channel for eISCP, Denon, Pioneer telnet and HEOS (ADR-0016). Framing and protocol rules stay in each driver.
  - `ssdp.py`: SSDP search and UPnP description fetching, shared by drivers.
- `src/index.tsx`: the React panel (`@decky/ui`, `@decky/api`).
- `tests/`: stdlib `unittest` against fake receivers on 127.0.0.1.
- `tools/`: stand-alone stdlib scripts users run on their LAN.
- `docs/adr/`: decisions and why. `docs/research/`: protocol and brand research.

## Commands

```
python -m unittest discover -s tests -t . -v   # backend tests
corepack pnpm install --frozen-lockfile         # pnpm 12 from the lockfile
corepack pnpm build                             # type-checks and bundles the UI
```

CI (`.github/workflows/build.yml`) runs the tests on Python 3.10 and builds with pnpm 12.16.1, the same pnpm the Decky store uses (ADR-0017). Keep `pnpm-lock.yaml` unchanged unless dependencies or the pnpm version change.

## Constraints that aren't visible in the code

- **Stdlib only** in Python, including drivers and `tools/` (ADR-0008). Hand-write protocols.
- **Python 3.10 compatible**: Decky bundles an older Python than a dev machine. Use `from __future__ import annotations`. 3.11+ APIs such as `asyncio.timeout`, `TaskGroup` and `except*` break on device.
- **Drivers never import `decky`** and keep no module-level state, so tests run them against fakes. One exception: TCP drivers share one `ReceiverSession` per receiver through a small keyed registry, so overlapping polls and commands never open two connections to a single-client receiver. Every driver error is a `DriverError` subclass with a user-facing message.
- **Receiver changes invalidate in-flight work.** The application tags operations with a selection generation and drops stale results; the panel does the same for its debounced writes. Keep that when adding operations.
- **RPC contract:** the intents and snapshot in `application.py` are mirrored field by field in `src/api.ts`. Change both in the same commit, and add a new state change as an intent, not a new RPC (ADR-0018).
- **Hardware:** only a Pioneer VSX-935 (eISCP) has been tested on real hardware, by the owner (bnm12). Agents can't reach a receiver or Steam, so everything else is simulator-verified. Say so in protocol specs (source tags) and in summaries.

## Adding a driver

1. Copy the shape of `drivers/eiscp.py`: subclass `Driver`, honour `config["port"]` so tests can use ephemeral ports, implement `probe` (quick, read-only, returns `None` for other protocols), and implement `discover` if the protocol allows it.
2. Write `drivers/<key>.md` from `PROTOCOL_TEMPLATE.md`, tagging every fact with its source.
3. Add `tests/test_<key>.py` with its own fake receiver.
4. Register it in `drivers/__init__.py`. Order matters: probes run in list order, so put cheap, specific probes first.

## Decisions

Record a decision in `docs/adr/NNNN-slug.md` (next free number, a short paragraph or two) when it is hard to reverse, surprising without context, and the result of a real trade-off. Read the existing ADRs before changing anything they cover.

## Version control and releases

- GitButler (`but`) for all writes. One branch per slice. Merge to `main` through a PR.
- Release with `gh workflow run release.yml -f version=X.Y.Z` (ADR-0007). Never hand-edit the version or push tags yourself.
- Release only slices whose tests and build pass. Note untested-on-hardware drivers as experimental in the release notes.
