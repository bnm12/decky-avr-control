# <Protocol name> (`<driver key>`)

> One protocol spec per driver, saved next to it as `drivers/<key>.md`, all in this layout. Fill every section; write
> "Unknown" rather than deleting a heading. Mark each fact with its source tag
> so readers can tell what was measured from what was read or assumed.

**Source tags:** **[measured]** seen on real hardware (say which model) ·
**[spec]** from a vendor document (link it in Sources) · **[community]** from an
open-source library or forum (link it) · **[assumed]** our guess, untested.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/<file>.py` |
| Brands / models | Which brands and model years speak this protocol |
| Verified on | Models tested on real hardware, or "none (simulator only)" |
| Transport | TCP/UDP/HTTP, port(s), framing, encoding |
| Discovery | How receivers are found, and the stable ID used to re-find them |
| Connections | How many clients the receiver accepts at once |

## Transport and framing

Byte-level message format, line endings, a worked example request/response,
and connection behaviour (keep-alive, unsolicited pushes, timeouts).

## Discovery

The probe sent, the reply format with an example, and which field is the stable ID.

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | | | | |
| Mute | | | | |
| Power | | | | |
| Listening mode | | | | |

## Volume scale

Raw range, step size, the raw value for 0 dB, the default slider cap, and how
the scale differs between models.

## Listening modes

Code table (or how the list is fetched), plus known per-model differences.

## Quirks and gotchas

Anything surprising: inverted flags, commands ignored in standby, single-client
limits, values that differ by firmware.

## Unknowns and open questions

What we couldn't verify and what a tester with this hardware should check.

## Sources

Numbered links to specs, libraries and forum threads used above.
