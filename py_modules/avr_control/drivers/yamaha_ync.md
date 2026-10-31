# Yamaha Remote Control XML (`yamaha_ync`)

**Source tags:** **[measured]** seen on real hardware · **[spec]** from a vendor document · **[community]** open-source library or forum · **[assumed]** unverified implementation detail.

## Summary

| | |
|---|---|
| Driver | [assumed] `py_modules/avr_control/drivers/yamaha_ync.py` |
| Brands / models | [community][1][2] Yamaha network receivers exposing the Yamaha Remote Control XML API, often older RX-V models from roughly 2010–2016; model and firmware coverage varies. |
| Verified on | [assumed] none (simulator only) |
| Transport | [community][1][2] HTTP POST with XML to `/YamahaRemoteControl/ctrl`, normally port 80; the driver honours `config["port"]` for alternate endpoints and tests. |
| Discovery | [community][1][3] SSDP `MediaRenderer` and `upnp:rootdevice`, then the Yamaha UPnP `X_device` block. Legacy XML needs `X_controlURL` and must not advertise `X_yxcControlURL`; UDN is the receiver ID. |
| Connections | [assumed] Independent, bounded HTTP requests; no concurrent-client limit has been verified. |

## Transport and framing

[community][1][2] The receiver accepts an XML document in an HTTP POST body. Requests use a `YAMAHA_AV` root and `cmd="GET"` or `cmd="PUT"`. A typical status request is:

```http
POST /YamahaRemoteControl/ctrl HTTP/1.1
Content-Type: text/xml; charset=utf-8

<YAMAHA_AV cmd="GET">
  <Main_Zone><Basic_Status>GetParam</Basic_Status></Main_Zone>
</YAMAHA_AV>
```

[community][1][2] Responses are XML and carry an `RC` attribute on the root; `RC="0"` indicates success. [assumed] Nonzero `RC` values become `Refused`; transport failures, malformed XML, and responses without `RC` become `DriverError`. [assumed] Requests have a two-second timeout. There is no push stream in this driver.

## Discovery

[community][1][3] The driver sends SSDP M-SEARCH requests for `urn:schemas-upnp-org:device:MediaRenderer:1` and `upnp:rootdevice`, fetches the UPnP description, and requires a Yamaha manufacturer string. It accepts a nonempty `X_controlURL` only when `X_yxcControlURL` is absent, so a MusicCast receiver advertising both control URLs is claimed by the YXC driver instead.

[assumed][1] The sender IP is the host, the description's `X_URLBase` port is used when present (otherwise port 80), and UDN is preferred as the stable receiver ID. The manual-IP probe first checks whether Yamaha YXC `system/getDeviceInfo` answers with `response_code=0`; if so, it returns `None` rather than claiming that receiver.

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [community][1][2] `Main_Zone/Basic_Status/Volume/Lvl` contains `Val`, `Exp`, and `Unit`; for example `-455`, `1`, `dB` is −45.5 dB. | [community][1][2] PUT `Main_Zone/Volume/Lvl` with `Val`, `Exp=1`, and `Unit=dB`. | [assumed] The driver reads status again and returns the confirmed raw value. | [assumed] The level sent is the target dB value times ten; raw volume is mapped using the scale below. |
| Mute | [community][1][2] `Main_Zone/Basic_Status/Volume/Mute` is `On` or `Off`. | [community][1][2] PUT `Main_Zone/Volume/Mute` with `On` or `Off`. | [assumed] Read back from status. | [assumed] |
| Power | [community][1][2] `Main_Zone/Basic_Status/Power_Control/Power` is `On` or `Standby`. | [community][1][2] PUT `Main_Zone/Power_Control/Power` with `On` or `Standby`. | [assumed] Read back from status; `Standby` maps to `False`. | [assumed] Network standby may be required for a receiver to answer while off. |
| Listening mode | [community][1][2] Read `Main_Zone/Basic_Status/Surround/Program_Sel/Current/{Straight, Sound_Program}`. | [community][1][2] PUT `Sound_Program` with the selected program, or `Straight` with `On`/`Off`. | [assumed] Read back from status; `Straight=On` is returned as the mode code `Straight`. | [assumed] Selecting a non-Straight program also sends `Straight=Off`. |

## Volume scale

[community][1][2] The driver maps the receiver's dB value to raw units with `raw = round(dB * 2 + 161)`: −80.5 dB → 0, 0 dB → 161, and +16.5 dB → 194. One raw step is 0.5 dB. [assumed] The default scale is min 0, max 194, zero 161, and two steps per dB; a volume maximum found in System or Main Zone Config refines the model's max. The default cap is raw 101 (about −30 dB), clamped to the available range.

[community][1][2] `Val` is represented as a decimal integer with an exponent; for example `Val=-455`, `Exp=1`, `Unit=dB` is −45.5 dB. [assumed] Volume writes use `Val=(raw-161)*5`, `Exp=1`, and `Unit=dB`, preserving the receiver's half-dB steps.

## Listening modes

[community][1][2] The driver tries `System/Config` and then `Main_Zone/Config` for a `Sound_Program` list and uses it when available; the set varies by model. [assumed] If neither query provides a list, it offers a common Yamaha set including Straight, stereo modes, surround decoder, cinema programs, and concert-hall programs. `Straight` is a distinct mode code. Mode codes are passed through to the receiver unchanged.

`ModeState.complete` is `true` only when a sound-program list was read from one
of those receiver configuration responses [community][1][2]. The fallback
common Yamaha set is incomplete (`false`) [assumed]. A current program absent
from the offered list remains visible but is not added to selectable modes;
standby returns no current or selectable modes [assumed]. Labels are
prettified from the code without a configured receiver [assumed].

## Quirks and gotchas

- [community][1][2] The XML remote-control protocol is not Yamaha YXC/MusicCast's HTTP/JSON API.
- [community][4] YNCA is a different Yamaha protocol family, commonly associated with serial-style commands and TCP port 50000; this driver does not implement it.
- [community][1][2] Sound-program names and volume maxima vary by model and firmware. A program visible on one receiver may be refused by another.
- [assumed] The driver has no hardware verification; standby availability, volume maximum parsing, and Straight behavior should be confirmed on each model.

## Unknowns and open questions

- [community][1][2] Some receivers also expose a unit description at `/YamahaRemoteControl/desc.xml`; [assumed] this driver does not depend on its fields.
- [assumed] Confirm XML response details, `RC` behavior, and whether every target model returns the same status tree.
- [assumed] Confirm which System or Main Zone Config variants expose the selectable sound-program list and model volume maximum.
- [assumed] Confirm the raw volume mapping, cap behavior, and whether every firmware accepts the `Val`/`Exp` write format at half-dB increments.
- [assumed] Confirm SSDP description variants and whether YXC-capable receivers always advertise `X_yxcControlURL`.
- [assumed] No real receiver has been tested. The `rxv` project is BSD-3-Clause licensed, not MIT; no third-party implementation code is copied into this driver.

## Sources

1. [community] [openHAB Yamaha Receiver binding](https://www.openhab.org/addons/bindings/yamahareceiver/) and [binding source](https://github.com/openhab/openhab-addons/tree/main/bundles/org.openhab.binding.yamahareceiver) (openHAB add-ons are EPL-2.0).
2. [community] [`wuub/rxv` Yamaha XML receiver library](https://github.com/wuub/rxv); its [license](https://github.com/wuub/rxv/blob/master/LICENSE) is BSD-3-Clause, not MIT.
3. [community] [Yamaha Remote Control XML API examples](https://github.com/pepperonas/yahama-controller/blob/main/docs/API.md).
4. [community] [`mvdwetering/ynca`](https://github.com/mvdwetering/ynca), a distinct YNCA implementation.
