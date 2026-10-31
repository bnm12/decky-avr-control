# Denon / Marantz AVR TCP and HTTP control (`denon`)

**Source tags:** **[measured]** seen on real hardware (say which model) · **[spec]** from a vendor document (link it in Sources) · **[community]** open-source library or forum · **[assumed]** our guess, untested.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/denon.py` |
| Brands / models | [community] Denon and Marantz receivers with the port-23 ASCII control interface; documented command availability varies by model and firmware. |
| Verified on | none (simulator only) |
| Transport | [spec] Telnet-compatible TCP port 23; [community] HTTP/XML status and control, normally port 80 or 8080 depending on model year. Telnet is preferred when it answers; HTTP is the fallback. |
| Discovery | [assumed] SSDP M-SEARCH for MediaRenderer and root-device advertisements; description UDN is the preferred stable ID. |
| Connections | [community] Receivers commonly allow one active Telnet client; a shared TCP session serializes drivers for the same host and port and closes after each transaction except while a push subscriber is active. |
| State updates | [spec][11, not yet measured] The Telnet protocol reports unsolicited status-change lines; this driver watches MV, MU, PW and MS lines. HTTP transport remains polling-only. |

## Transport and framing

[spec] Send plain ASCII commands terminated by carriage return (`\r`, byte `0x0d`) to TCP port 23. Replies are CR-terminated ASCII lines; a line-feed, if present, is ignored. For example, send `PW?\r` and expect `PWON\r` or `PWSTANDBY\r`.

[spec][11, not yet measured] Denon AVR control protocol documents status-change event lines on Telnet connections, including `MV`, `MU`, `PW` and `MS` reports. The driver maps these to partial status updates when using Telnet. This behavior has not been measured on a real receiver. The shared `ReceiverSession` routes unclaimed lines to subscribers; the HTTP transport has no push subscription and remains polling-only.

[community] The driver first checks Telnet TCP 23 and prefers it if it returns a Denon power reply. When Telnet is unavailable, busy, or disabled, it tries HTTP on the configured `http_port`, then ports 80 and 8080. The selected transport and HTTP port are retained in receiver config.

[community] The HTTP adapter reads `/goform/formMainZone_MainZoneXmlStatus.xml` (and falls back to `/goform/formMainZone_MainZoneXml.xml`) and parses the main-zone Power, MasterVolume, VolumeDisplay, Mute and surround-mode XML fields. The fallback page can report its mode as `selectSurround` ([AVR-1912 recording](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/tests/xml/AVR-1912-formMainZone_MainZoneXml.xml)). Setters use `/goform/formiPhoneAppDirect.xml?<command>` with the same Telnet-style command strings, then query the XML status page to confirm the new value. The library's AppCommand interface is another HTTP/XML option (`POST /goform/AppCommand.xml`) with `GetAllZonePowerStatus`, `GetAllZoneVolume`, `GetAllZoneMuteStatus` and `GetSurroundModeStatus`; this driver uses the status pages instead of AppCommand because they expose the needed state in one response and are also used as the library's fallback for receivers with incomplete AppCommand responses.

[community] Because receivers may reject a second Telnet client, the shared session keyed by host and port serializes Telnet operations across driver instances. HTTP does not hold the Telnet slot and can coexist with other apps' Telnet clients.

[spec] Leave at least 50 ms between successive commands sent on the same connection. `status()` sends its four queries with this spacing. [community] The port-23 interface is Telnet-compatible raw TCP and does not require a login handshake.

[assumed] Telnet status requires power and, while powered on, volume and mute; listening mode is collected during a short optional grace period and remains `None` when absent. Standby reports power alone. Setters only accept a state line matching the requested value as confirmation; other state lines are routed to subscribers while the command waits to its bounded deadline. If a value setter times out after observing a different value, it returns that last observed value. Connection and transport failures remain driver errors, not mode refusals.

## Discovery

[assumed] Send SSDP M-SEARCH requests for `urn:schemas-upnp-org:device:MediaRenderer:1` and `upnp:rootdevice`, then fetch each response's device description using the shared SSDP helper. Probe identified devices and retain only those answering through Telnet or HTTP.

[assumed] Keep descriptions whose manufacturer or model/friendly name identifies Denon, Marantz, HEOS, or D&M; use the model name for display and the UDN for the stable receiver ID (falling back to the SSDP USN, then host).

## Commands

| Capability | Telnet query / set | HTTP query / set | Notes |
|---|---|---|---|
| Volume | [community] `MV?` / `MV<NN>` or `MV<NNN>` | [community] XML `MasterVolume` / `/goform/formiPhoneAppDirect.xml?MV<NNN>` | [community] Telnet replies commonly include `MVMAX <NN>`; a three-digit value ending in `5` is a half step (`MV455` = 45.5). HTTP setters are confirmed by reading status XML back. |
| Mute | [community] `MU?` / `MUON` / `MUOFF` | [community] XML `Mute` / `/goform/formiPhoneAppDirect.xml?MUON` or `MUOFF` | [community] `ON` is muted; `OFF` is unmuted. HTTP setters are confirmed by reading status XML back. |
| Power | [community] `PW?` / `PWON` / `PWSTANDBY` | [community] XML `Power` / `/goform/formiPhoneAppDirect.xml?PWON` or `PWSTANDBY` | [community] Uses global receiver power (`PW`), not zone power (`ZM`). HTTP setters are confirmed by reading status XML back. |
| Listening mode | [community] `MS?` / `MS<MODE>` | [community] XML `SurrMode` or fallback `selectSurround` / `/goform/formiPhoneAppDirect.xml?MS<MODE>` | [community] Reuses the same canonical mode mapping for either transport; HTTP setters are confirmed by reading status XML back. |

## Volume scale

[community] The driver's raw units are half steps: a protocol volume of 45.5 maps to raw 91, while 45.0 maps to raw 90. `MV?` commonly returns the current value plus `MVMAX`; that ceiling refines the scale maximum. HTTP XML reports the main-zone dB value in `MasterVolume`.

[community] The HTTP XML `MasterVolume` value is in dB even when `VolumeDisplay` says `Absolute`: always map it to raw half steps with `raw = round((dB + 80) * 2)`, clamped to the scale. The recorded AVR-X2100W status XML reports `Absolute` and `-28.0` dB (raw 104, equivalent to `MV52`); `--` is the minimum `-80` dB (raw 0), as handled by the MIT-licensed library ([volume conversion](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/denonavr/volume.py), [AVR-X2100W recording](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/tests/xml/AVR-X2100W-2-formMainZone_MainZoneXmlStatus.xml)).

[community] Volume 80 is the 0 dB reference, so the scale uses `zero=160` and `steps_per_unit=2`; display dB is `(raw - 160) / 2`.

[assumed] The initial scale maximum is raw 196 and the default cap is raw 100 (approximately -30 dB). Models may report a different `MVMAX`, and the driver's runtime maximum follows it. [community] The relative-volume display and `MV80` reference are documented for the decimal-volume protocol; older model-specific control PDFs may describe different encodings, so verify the receiver-specific sheet before assuming identical numeric behavior.

## Listening modes

[community] The common settable list is `MOVIE`, `MUSIC`, `GAME`, `DIRECT`, `PURE DIRECT`, `STEREO`, `AUTO`, `DOLBY DIGITAL`, `DOLBY SURROUND`, `DTS SURROUND`, `DTS NEURAL:X`, `MCH STEREO`, and `VIRTUAL`. Friendly names are provided for these codes.

[community] Mode availability depends on model, input, and signal. `status()` canonicalizes recognized decoder-specific reports to settable commands: Dolby report variants (such as `DOLBY D+ +DSUR`) to `DOLBY DIGITAL`, DTS variants (such as `DTS HD+DSUR`) to `DTS SURROUND`, decoder reports with Music/Movie/Game suffixes to their matching settable modes, multi-channel stereo reports to `MCH STEREO`, and `AUTO`/`NONE` to `AUTO`. The mode code returned by status can therefore be restored with `set_mode()`. Unrecognized values remain available to display and are sent without a local mode whitelist (after trimming and uppercasing); a refusal is reported only if the receiver does not confirm the command.

The common static set is not guaranteed to enumerate every model's selectable modes, so `ModeState.complete` is always `false` [community]. A current decoder report is canonicalized when a known settable equivalent exists; other unknown current values remain visible but are not added to the selectable list. Standby returns no current or selectable modes [assumed]. Labels use the canonical catalog or prettify unknown codes without a configured receiver [community].

[community] This grouping follows `denonavr`'s `SOUND_MODE_MAPPING`: decoder-specific Dolby and DTS values are grouped under the corresponding settable sound mode, `MCH STEREO` / `MULTI CH STEREO` map to `MCH STEREO`, and `None` maps to `AUTO`. The source is MIT-licensed; see [denonavr `const.py`](https://github.com/ol-iver/denonavr/blob/master/denonavr/const.py) and [LICENSE](https://github.com/ol-iver/denonavr/blob/master/LICENSE).

## Quirks and gotchas

- [community] Many models accept only one Telnet client at a time; short-lived per-operation connections reduce conflicts but do not prevent another application from occupying the socket. HTTP does not use that Telnet slot, so it remains available alongside other apps' Telnet connections.
- [community] Older models commonly expose the HTTP control interface on port 80; AVR-X 2016-and-later models commonly use port 8080. Model detection and ports vary, so the driver tries both when no `http_port` is remembered. The `denonavr` source identifies AVR / AVR-X on 80 and AVR-X 2016 on 8080 (`denonavr/const.py`, `denonavr/foundation.py`).
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
- [spec, not yet measured] Tester checklist: change volume with the remote while the panel is open; does the slider follow within 1 s?

## Sources

1. [spec] [Denon AVR-1713 / AVR-1613 Control Protocol v8.6.0 (official PDF)](https://assets.denon.com/DocumentMaster/UK/AVR1713_AVR1613_PROTOCOL_V860.pdf)
2. [spec] [Denon FY23-CY2022 AVR Control Protocol (PDF mirror)](https://allonis.com/media/kunena/attachments/814/DenonFY23-CY2022_AVR_PROTOCOL_V0221.pdf)
3. [community] [Marantz / Denon AVR TCP Port 23 Control Guide](https://github.com/alsak0de/marantz-denon-mqtt/blob/main/docs/telnet/MARANTZ_DENON_TELNET_PROTOCOL.md)
4. [community] [ol-iver/denonavr sound-mode mapping (`denonavr/const.py`, MIT)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/denonavr/const.py)
5. [community] [openHAB Denon / Marantz binding](https://www.openhab.org/addons/bindings/denonmarantz/)
6. [community] [ol-iver/denonavr HTTP volume/status (`denonavr/volume.py`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/denonavr/volume.py), [AppCommand (`denonavr/appcommand.py`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/denonavr/appcommand.py), [HTTP transport (`denonavr/api.py`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/denonavr/api.py); MIT, see [LICENSE](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/LICENSE).
7. [community] [ol-iver/denonavr model ports and endpoint paths (`denonavr/const.py`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/denonavr/const.py), [model detection (`denonavr/foundation.py`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/denonavr/foundation.py).
8. [community] [ol-iver/denonavr XML status example (`tests/xml/AVR-3311CI-formMainZone_MainZoneXml.xml`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/tests/xml/AVR-3311CI-formMainZone_MainZoneXml.xml); MIT.
9. [community] [ol-iver/denonavr AVR-X2100W status XML recording (`tests/xml/AVR-X2100W-2-formMainZone_MainZoneXmlStatus.xml`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/tests/xml/AVR-X2100W-2-formMainZone_MainZoneXmlStatus.xml); MIT.
10. [community] [ol-iver/denonavr AVR-1912 fallback XML recording (`tests/xml/AVR-1912-formMainZone_MainZoneXml.xml`)](https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/tests/xml/AVR-1912-formMainZone_MainZoneXml.xml); MIT.
11. [spec] [Denon 2018 AVR Control Protocol](https://assets.denon.com/documentmaster/us/denon-avr-2018-avr-control-protocol-v01.pdf): Telnet status-change event reports; not measured for this project.
