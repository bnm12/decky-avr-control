# Yamaha Extended Control (YXC) (`yamaha`)

**Source tags:** **[measured]** seen on real hardware · **[spec]** from a vendor document · **[community]** open-source library or forum · **[assumed]** unverified implementation detail.

## Summary

| | |
|---|---|
| Driver | [assumed] `py_modules/avr_control/drivers/yamaha.py` |
| Brands / models | [spec][1] Yamaha network receivers that expose Yamaha Extended Control; supported endpoints and capabilities vary by model and firmware. |
| Verified on | [assumed] none (simulator only) |
| Transport | [spec][1, §§3, 5] HTTP with JSON responses at `/YamahaExtendedControl/v1/`, normally port 80; [assumed] the driver allows a configured port for tests. |
| Discovery | [spec][1, §13.2] SSDP/UPnP description; Yamaha's `X_yxcControlURL` indicates YXC support. [assumed][1] The driver uses the description's UDN as its stable receiver ID. |
| Connections | [assumed] The specifications do not define a concurrent-client limit; the driver uses independent bounded HTTP requests. |

## Transport and framing

[spec][1, §§3, 5] The API root is `http://<host>:80/YamahaExtendedControl/v1/`. Basic operations use HTTP GET requests with JSON response bodies. For example:

```http
GET /YamahaExtendedControl/v1/main/getStatus HTTP/1.1
Host: 192.0.2.20
```

[spec][1, §3] A response includes `response_code`; zero means success. The error meanings used by the driver are:

| Code | Meaning |
|---:|---|
| 0 | [spec][1, §10] Success. |
| 1 | [spec][1, §10] Device is initializing. |
| 2 | [spec][1, §10] Internal error. |
| 3 | [spec][1, §10] Invalid request. |
| 4 | [spec][1, §10] Invalid parameter. |
| 5 | [spec][1, §10] Guarded by the receiver's current state. |
| 6 | [spec][1, §10] Timeout. |
| 99 | [spec][1, §10] Firmware update in progress. |

[assumed][1] The driver treats nonzero response codes as `DriverError`, except a guarded request and an invalid sound-program request, which become `Refused`. HTTP, timeout, and invalid-JSON failures also become `DriverError`.

## Discovery

[spec][1, §13.2] Yamaha's UPnP description includes a Yamaha `X_device` element and `X_yxcControlURL` for YXC control. [assumed][1] The driver sends SSDP M-SEARCH requests for `urn:schemas-upnp-org:device:MediaRenderer:1` and `upnp:rootdevice`, fetches the returned description, and keeps only Yamaha-manufactured devices whose XML contains that YXC control URL.

[spec][1, §13.2] The UPnP `UDN` is used as the discovery result's stable receiver ID. [assumed][1] The control URL is treated as a YXC-support marker; requests use the documented host-based API root and port 80 (or the configured test port).

[spec][1, §4.1] `system/getDeviceInfo` reports `model_name` and, on supported API versions, `device_id`. The probe accepts only a successful response with a nonempty model name and uses `device_id` as the receiver ID when available.

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [spec][1, pp.10-11, 14-16] `system/getFeatures` returns `zone` as an array; the main-zone `range_step` array describes the volume range. [spec][1, pp.48-50] `main/getStatus` reports integer `volume`. | [spec][1, p.52] `GET /YamahaExtendedControl/v1/main/setVolume?volume=N`. | [spec][1, pp.48-50] Status includes current integer `volume`. | [assumed][1] If a setter response omits the resulting value, the driver confirms it with `getStatus`. |
| Mute | [spec][1, pp.10-11, 14-16] The main-zone `func_list` advertises supported functions; [spec][1, pp.48-50] `main/getStatus` reports boolean `mute`. | [spec][1, p.53] `GET /YamahaExtendedControl/v1/main/setMute?enable=true|false`. | [spec][1, pp.48-50] Status includes `mute`. | [assumed][1] The driver offers mute only when the main-zone `func_list` advertises it. |
| Power | [spec][1, pp.10-11, 14-16] The main-zone `func_list` advertises supported functions; [spec][1, pp.48-50] `main/getStatus` reports string `power`. | [spec][1, p.51] `GET /YamahaExtendedControl/v1/main/setPower?power=on|standby`. | [spec][1, pp.48-51] Status uses `on` or `standby`. | [spec][1, p.51] `standby` is the zone's off state. Network standby is a separate system setting. |
| Listening mode | [spec][1, pp.10-11, 14-16] The main-zone `sound_program_list` is an array of selectable program IDs; [spec][1, pp.48-50] `main/getStatus` reports string `sound_program`. | [spec][1, p.54] `GET /YamahaExtendedControl/v1/main/setSoundProgram?program=X`. | [spec][1, pp.48-50, 54] Status includes selected `sound_program`. | [spec][1, p.10] A guarded or invalid program can be unavailable in the receiver's current state; [assumed][1] the driver reports this as `Refused`. |

[spec][1, pp.10-11, 14-16] `getFeatures` returns zone features in a `zone` array; each zone has a string `id`, and `func_list`, `sound_program_list`, and `range_step` are arrays. The response's `system.func_list` describes system functions, not zone capabilities. [assumed][1] This driver selects the zone whose `id` is `main` and derives capabilities from its `func_list` and `sound_program_list` rather than claiming that every Yamaha receiver supports every operation.

## Volume scale

[spec][1, pp.10-11, 14-16] The `main` zone's `range_step` array contains entries identified by `id`; `volume` gives the raw range and step, while `actual_volume_db` gives the dB range and step when available. The example also includes `actual_volume_numeric`, which is not used to derive this driver's dB scale. [spec][1, pp.48-50] `getStatus` reports raw `volume` as an integer and may include `actual_volume`, an object with string `mode`, floating-point `value`, and string `unit`.

[assumed][1] When an `actual_volume_db` range is advertised, the driver creates a dB scale with `steps_per_unit = 1 / db_step` (rounded to the integer scale supported by `VolumeScale`). It derives the raw value for 0 dB by linearly mapping the advertised dB endpoints to the integer raw-volume endpoints. If status reports `actual_volume.mode` as `db` alongside raw volume, that pair refines the zero point. The default cap is the raw value corresponding to approximately -30 dB.

[assumed][1] Without an actual-volume dB range, a reported raw maximum of 100 uses a percent scale (`zero=0`, one raw step per percent) and a 70% default cap. For other ranges, the driver uses the common Yamaha mapping: raw 0–161, 0.5 dB per step, 0 dB at raw 161, and a default cap of raw 101 (approximately -30 dB). This fallback is based on community implementations and is not guaranteed for every model.

## Listening modes

[spec][1, pp.10-11, 14-16, 54] The driver gets the available program codes from the main zone's `sound_program_list` array and sends a selected code unchanged. The list is receiver- and zone-specific.

[assumed][1] Known codes are prettified for display, including `straight` → “Straight”, `7ch_stereo` → “7ch Stereo”, `surr_decoder` → “Surround Decoder”, `all_ch_stereo` → “All-Channel Stereo”, `sci-fi` → “Sci-Fi”, `mono_movie` → “Mono Movie”, and `hall_in_munich` → “Hall in Munich”. Unknown codes are displayed by replacing underscores and hyphens with spaces and title-casing the result.

## Quirks and gotchas

- [community][3][4][6] YXC is not the same protocol as Yamaha's older XML remote-control API. [assumed][1][3][4] Pre-2016 legacy receivers that expose only `/YamahaRemoteControl/ctrl` are not supported by this driver.
- [spec][1, §§4.12–4.13, 5.3] Zone power uses `on`/`standby`; network standby is separately configured through system endpoints. A receiver may need network standby enabled to answer while the main zone is off.
- [spec][1, §§4.2, 5.1] Zones and their available capabilities differ by model; this driver addresses only `main`.
- [spec][1, §10] Code 5 means the receiver guarded the request. [assumed][1] It is surfaced as `Refused`, not as a transport failure.

## Unknowns and open questions

- [assumed][1][3][6] No real receiver has verified the API root, the default port, the XML discovery marker, or the timeout behavior in this driver.
- [assumed][1][3][4][6] Confirm the raw-volume and actual-dB ranges, step relationships, and zero calibration across Yamaha models and firmware. The common 0–161 fallback is not a universal mapping.
- [assumed][1][6] Confirm whether `getFeatures` advertises all supported functions consistently and whether setter replies include their confirmed state on every model.
- [assumed][1] Confirm that enabling network standby is required for discovery/probing while the main zone is in standby, and determine receiver-specific behavior.
- [assumed][1][2] The Advanced API includes MusicCast distribution and streaming operations that are outside this driver's volume, mute, power, and mode capabilities.

## Sources

1. [spec] [Yamaha Extended Control API Specification (Basic), Rev. 2.00 (public mirror)](https://github.com/rsc-dev/pyamaha/blob/ac3a64d2990ec4b4e258c2218a3ae93600419e6f/doc/YXC_API_Spec_Basic_v2.0.pdf)
2. [spec] [Yamaha Extended Control API Specification (Advanced), Rev. 2.00 (public mirror)](https://github.com/rsc-dev/pyamaha/blob/ac3a64d2990ec4b4e258c2218a3ae93600419e6f/doc/YXC_API_Spec_Advanced_v2.0.pdf)
3. [community] [aiomusiccast `pyamaha.py`](https://github.com/vigonotion/aiomusiccast/blob/2d56413224b42b2d8e1b5a51a834df9e7fac33c0/aiomusiccast/pyamaha.py)
4. [community] [aiomusiccast `musiccast_device.py`](https://github.com/vigonotion/aiomusiccast/blob/2d56413224b42b2d8e1b5a51a834df9e7fac33c0/aiomusiccast/musiccast_device.py)
5. [community] [pymusiccast `zone.py`](https://github.com/jalmeroth/pymusiccast/blob/616379ae22d6b518c61042d58be6d18a46242168/pymusiccast/zone.py)
6. [community] [openHAB Yamaha MusicCast binding](https://github.com/openhab/openhab-addons/tree/1d0fe59b9a86fd36f87d1bed6e993d2603115492/bundles/org.openhab.binding.yamahamusiccast)
