# One control application behind a snapshot-and-intent interface

Caps and late night, game-profile restore, receiver identity and rediscovery, and stale-result guards share state and ordering. One deep module, `application.py`, owns those rules and exposes a narrow interface: `snapshot()` returns everything the panel renders, `dispatch(intent)` takes a typed intent (a discriminated union mirrored in TypeScript) and returns a consistent snapshot or an error, and `discover()`, `list_drivers()` and `setup_options()` stay as plain reads. Volume conversion and cap math live in one Python module, `volume.py`, so the panel sends and renders display values and never re-derives receiver math.

## Considered options

- **Several domain modules** (receiver directory, volume policy, game profiles) composed by a thin controller. Rejected: profile restore needs receiver identity and caps, and late night changes what profiles may save, so the composition layer would still own all the sequencing, behind more interfaces.
- **A generic `rpc(name, args)`.** Rejected: it hides method names without reducing what callers must know, and it loses type checking.
