# Denon / Marantz AVR TCP control (`denon`)

**Source tags:** **[measured]** seen on real hardware (say which model) · **[spec]** from a vendor document (link it in Sources) · **[community]** open-source library or forum · **[assumed]** our guess, untested.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/denon.py` |
| Brands / models | [community] Denon and Marantz receivers with the port-23 ASCII control interface; documented command availability varies by model and firmware. |
| Verified on | none (simulator only) |
| Transport | [spec] TCP port 23, ASCII commands/replies terminated by CR; [community] receiver state-change events can be interleaved with command replies. |
| Discovery | [assumed] SSDP M-SEARCH for MediaRenderer and root-device advertisements; description UDN is the preferred stable ID. |
| Connections | [community] Receivers commonly allow one active Telnet client; this driver opens and closes one connection per operation. |

## Transport and framing

[spec] Send plain ASCII commands terminated by carriage return (`\r`, byte `0x0d`) to TCP port 23. Replies are CR-terminated ASCII lines; a line-feed, if present, is ignored. For example, send `PW?\r` and expect `PWON\r` or `PWSTANDBY\r`.

[community] The receiver can also push unsolicited event lines on an open Telnet connection. The driver ignores lines that do not match a requested response, and uses a fresh connection for each operation rather than keeping a subscription open.

[community] Because receivers may reject a second Telnet client, a minimal class-level lock registry serializes operations across driver instances for the same host and port. It is scoped by event loop so each `asyncio.Lock` is used only on its owning loop; weak loop keys and weak lock values release registry state when the loop or its active operations end.

[spec] Leave at least 50 ms between successive commands sent on the same connection. `status()` sends its four queries with this spacing. [community] The port-23 interface is Telnet-compatible raw TCP and does not require a login handshake.

## Discovery

[assumed] Send SSDP M-SEARCH requests for `urn:schemas-upnp-org:device:MediaRenderer:1` and `upnp:rootdevice`, then fetch each response's device description using the shared SSDP helper.

[assumed] Keep descriptions whose manufacturer or model/friendly name identifies Denon, Marantz, HEOS, or D&M; use the model name for display and the UDN for the stable receiver ID (falling back to the SSDP USN, then host).

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [community] `MV?` | [community] `MV<NN>` or `MV<NNN>` | [community] `MV<NN>` / `MV<NNN>` and commonly `MVMAX <NN>` | [community] A three-digit value ending in `5` represents a half step (for example `MV455` = 45.5); the driver converts volume to half-step raw integers and records the reported ceiling. |
| Mute | [community] `MU?` | [community] `MUON` / `MUOFF` | [community] `MUON` / `MUOFF` | [community] `ON` is muted and `OFF` is unmuted. |
| Power | [community] `PW?` | [community] `PWON` / `PWSTANDBY` | [community] `PWON` / `PWSTANDBY` | [community] Uses global receiver power (`PW`), not main-zone power (`ZM`), because `ZM` reports a zone state that can differ from overall standby. |
| Listening mode | [community] `MS?` | [community] `MS<MODE>` | [community] `MS<MODE>` | [community] The reported decoder mode can differ from the set command or include model-specific text; unknown codes are prettified for display. |

## Volume scale

[community] The driver's raw units are half steps: a protocol volume of 45.5 maps to raw 91, while 45.0 maps to raw 90. `MV?` commonly returns the current value plus `MVMAX`; that ceiling refines the scale maximum.

[community] The protocol's nominal range is 0–98, represented as raw 0–196. Volume 80 is the 0 dB reference, so the scale uses `zero=160` and `steps_per_unit=2`; display dB is `(raw - 160) / 2`.

[assumed] The initial scale maximum is raw 196 and the default cap is raw 100 (approximately -30 dB). Models may report a different `MVMAX`, and the driver's runtime maximum follows it. [community] The relative-volume display and `MV80` reference are documented for the decimal-volume protocol; older model-specific control PDFs may describe different encodings, so verify the receiver-specific sheet before assuming identical numeric behavior.

## Listening modes

[community] The common settable list is `MOVIE`, `MUSIC`, `GAME`, `DIRECT`, `PURE DIRECT`, `STEREO`, `AUTO`, `DOLBY DIGITAL`, `DOLBY SURROUND`, `DTS SURROUND`, `DTS NEURAL:X`, `MCH STEREO`, and `VIRTUAL`. Friendly names are provided for these codes.

[community] Mode availability depends on model, input, and signal. `status()` canonicalizes recognized decoder-specific reports to settable commands: Dolby report variants (such as `DOLBY D+ +DSUR`) to `DOLBY DIGITAL`, DTS variants (such as `DTS HD+DSUR`) to `DTS SURROUND`, decoder reports with Music/Movie/Game suffixes to their matching settable modes, multi-channel stereo reports to `MCH STEREO`, and `AUTO`/`NONE` to `AUTO`. The mode code returned by status can therefore be restored with `set_mode()`. Unrecognized values remain available to display and are sent without a local mode whitelist (after trimming and uppercasing); a refusal is reported only if the receiver does not confirm the command.

[community] This grouping follows `denonavr`'s `SOUND_MODE_MAPPING`: decoder-specific Dolby and DTS values are grouped under the corresponding settable sound mode, `MCH STEREO` / `MULTI CH STEREO` map to `MCH STEREO`, and `None` maps to `AUTO`. The source is MIT-licensed; see [denonavr `const.py`](https://github.com/ol-iver/denonavr/blob/master/denonavr/const.py) and [LICENSE](https://github.com/ol-iver/denonavr/blob/master/LICENSE).

## Quirks and gotchas

- [community] Many models accept only one Telnet client at a time; short-lived per-operation connections reduce conflicts but do not prevent another application from occupying the socket.
- [spec] Commands on a connection should be spaced by at least 50 ms.
- [community] Port-23 commands are not uniform across model years. The 2012 protocol PDFs are useful historical references, while HEOS-era receivers can expose newer command/report variants.
- [community] The receiver can remain physically active when another zone is on; `PWSTANDBY` is used for global standby, not `ZMOFF`.
- [assumed] Network standby must be enabled for a receiver to answer while in standby; the setting is commonly called “Network Control: Always On,” but its name and availability vary by model.
- [community] SSDP/HEOS device naming is inconsistent, so discovery checks Denon/Marantz as well as HEOS and D&M identifying strings.

## Unknowns and open questions

- [assumed] No real Denon or Marantz hardware has been tested; confirm line endings, command timing, event interleaving, and single-client behavior on target models.
- [assumed] Confirm whether `MV?` reports `MVMAX` on each supported model and whether its value is expressed in the same whole/half-step notation as `MV`.
- [assumed] Confirm the 0 dB reference, volume range, and relative-versus-absolute display for older models and each firmware family; the documented modern decimal mapping may not apply to every receiver.
- [assumed] Confirm which `MS` codes can be set for each input/signal and how unsupported modes are reported.
- [assumed] Confirm which Denon/Marantz models advertise a usable SSDP description and expose manufacturer, model, and UDN fields.

## Sources

1. [spec] [Denon AVR-1713 / AVR-1613 Control Protocol v8.6.0 (official PDF)](https://assets.denon.com/DocumentMaster/UK/AVR1713_AVR1613_PROTOCOL_V860.pdf)
2. [spec] [Denon FY23-CY2022 AVR Control Protocol (PDF mirror)](https://allonis.com/media/kunena/attachments/814/DenonFY23-CY2022_AVR_PROTOCOL_V0221.pdf)
3. [community] [Marantz / Denon AVR TCP Port 23 Control Guide](https://github.com/alsak0de/marantz-denon-mqtt/blob/main/docs/telnet/MARANTZ_DENON_TELNET_PROTOCOL.md)
4. [community] [ol-iver/denonavr sound-mode mapping (`const.py`, MIT)](https://github.com/ol-iver/denonavr/blob/master/denonavr/const.py)
5. [community] [openHAB Denon / Marantz binding](https://www.openhab.org/addons/bindings/denonmarantz/)
