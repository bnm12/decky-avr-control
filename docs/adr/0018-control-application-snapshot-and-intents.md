# One control application behind a snapshot-and-intent interface

The frontend used to call about 20 named backend methods and coordinate refreshes itself, while the controller held the cross-cutting rules: caps and late night, game-profile restore, receiver identity and rediscovery, stale-result guards. Those rules share state and ordering, so we kept them in one deep module, `application.py`, and shrank its interface: `snapshot()` returns everything the panel renders, `dispatch(intent)` takes a typed intent (a discriminated union mirrored in TypeScript) and returns a consistent snapshot or an error, and `discover()`, `list_drivers()` and `setup_options()` stay as plain reads. Volume conversion and cap math live in one Python module, `volume.py`, so the panel sends and renders display values and never re-derives receiver math.

## Considered options

- **Several domain modules** (receiver directory, volume policy, game profiles) composed by a thin controller. Rejected: profile restore needs receiver identity and caps, and late night changes what profiles may save, so the composition layer would still own all the sequencing, behind more interfaces.
- **A generic `rpc(name, args)`.** Rejected: it hides method names without reducing what callers must know, and it loses type checking.
