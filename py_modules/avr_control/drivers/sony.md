# Sony Audio Control API / ScalarWebAPI (`sony`)

**Source tags:** **[measured]** seen on real hardware · **[spec]** from a vendor document · **[community]** open-source library or forum · **[assumed]** unverified implementation detail.

## Summary

| | |
|---|---|
| Driver | [assumed] `py_modules/avr_control/drivers/sony.py` |
| Brands / models | [community] Sony STR-DN / ES receivers and soundbars that expose the ScalarWebAPI audio and system services; model and firmware coverage is not universal. |
| Verified on | [assumed] none (simulator only) |
| Transport | [community] HTTP POST with JSON-RPC-style JSON bodies to `/sony/<service>`, normally port 10000. |
| Discovery | [community] SSDP service type `urn:schemas-sony-com:service:ScalarWebAPI:1`; the UPnP description advertises `X_ScalarWebAPI_BaseURL`. [assumed] The driver uses its port and prefers system MAC/serial for its receiver ID, falling back to the UDN. |
| Connections | [assumed] Each request is an independent bounded HTTP request; no persistent session is held. A model-wide concurrent-client limit is unknown. |
| State updates | [assumed] Polling only; the HTTP driver does not subscribe to receiver events. |

## Transport and framing

[community] Requests are UTF-8 JSON sent with HTTP `Content-Type: application/json`. The driver uses the method's service URL, for example `POST /sony/audio`, and a body shaped like:

```json
{
  "method": "getVolumeInformation",
  "id": 1,
  "params": [{"output": ""}],
  "version": "1.1"
}
```

[community] Successful responses contain a `result` array; errors contain an `error` array with a numeric code and message. [assumed] Requests have a 2-second timeout and each request is parsed independently; the API does not require the driver to retain a connection.

[community] Public ScalarWebAPI references use `1.1` for `getPowerStatus` and `getVolumeInformation`, and `1.0` for `getInterfaceInformation`. [assumed] This driver uses `1.1` for those calls and the power setter, `1.0` for system identity and volume/mute setters, and `1.1` for sound-settings calls. Implementations such as `python-songpal` query each device's supported method versions, so the fixed defaults should be confirmed against each receiver's advertised API methods.

## Discovery

[community] The driver sends SSDP M-SEARCH for `urn:schemas-sony-com:service:ScalarWebAPI:1`, fetches the returned UPnP description and reads `av:X_ScalarWebAPI_BaseURL` from its raw XML.

[assumed] Candidates whose ScalarWebAPI service list includes `video` are ignored. The URL's port is retained in `Found.config` (defaulting to HTTP port 80 if absent); the SSDP sender IP is the receiver host. The driver queries `system/getSystemInformation` for `macAddr`, then `serialNumber`; the UPnP `UDN` is the fallback ID. `modelName` from the description is the displayed model.

[community] A read-only probe calls `system/getInterfaceInformation` and `system/getSystemInformation`. [assumed] It accepts only a recognized audio product category and rejects TV/video categories, uses `modelName` or the system model, and prefers MAC address then serial number as the ID.

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [community] `audio/getVolumeInformation` with `{"output": ""}` reports `volume`, `minVolume`, `maxVolume`, `step`, `mute` and output metadata. | [community] `audio/setAudioVolume` with `{"volume": "N", "output": ""}`. Volume is sent as a string. | [assumed] The driver reads `getVolumeInformation` again and returns its integer `volume`. | [community] Volume ranges are device-reported; a reported step is not a dB scale. |
| Mute | [community] `audio/getVolumeInformation` reports `mute` as `"on"` or `"off"`. | [community] `audio/setAudioMute` with `{"mute": "on"|"off"}`. | [assumed] The driver reads `getVolumeInformation` again. | [assumed] Mute is offered only while the API reports a parseable mute state. |
| Power | [community] `system/getPowerStatus` reports a `status`, typically `"active"` or `"off"`/`"standby"`. | [community] `system/setPowerStatus` with `{"status": "active"|"off"}`. | [assumed] The driver reads `getPowerStatus` again. | [community] Power-on availability from network standby can vary by receiver. |
| Listening mode | [community] `audio/getSoundSettings` with `{"target": "soundField"}` reports `currentValue` and `candidate` entries. | [community] `audio/setSoundSettings` with `{"settings":[{"target":"soundField","value":"CODE"}]}`. | [assumed] The driver reads `getSoundSettings` again. | [community] Candidate values and availability are receiver-specific. |

[assumed] API error codes 3 and 12 are surfaced as `Unsupported`; 40000 is treated as power-off/refused; other API, HTTP and JSON errors become `DriverError`. When an audio call reports 40000, `status()` reports power off and leaves audio state unknown.

## Volume scale

[community] `getVolumeInformation` provides the receiver's current integer volume, minimum and maximum. No universal conversion from these volume values to dB has been established.

[assumed] The driver uses a percent scale only when the reported range is exactly 0–100. Every other range uses `"steps"` and shows Sony's raw volume number without a unit suffix. The raw min/max are retained, and the default cap is about 40% of the reported range above its minimum. No dB zero point or dB step is inferred.

## Listening modes

[community] The driver reads the `candidate` list for target `soundField`, uses each candidate's `value` as the opaque mode code, and uses `title` as the display name. Entries marked unavailable are omitted. Candidate availability and names can differ by model and current input.

If MODE is supported and the receiver returns a `candidate` array, it is the
selectable `soundField` list and `ModeState.complete` is `true` [community].
Without that array the state is incomplete [assumed]. A current value not
present among available candidates remains current but is not added to
selectable modes. Unsupported mode data or standby returns `current=None`, no
selectable modes, and `complete=false` [community][assumed]. Labels use the
static prettifier without a configured receiver [assumed].

## Quirks and gotchas

- [community] The ScalarWebAPI is shared by Sony audio products and BRAVIA TVs; an SSDP match alone does not establish that the device is an AVR or soundbar. The probe checks the product category, and discovery filters advertised video services.
- [community] Sony's API may reject audio queries while the receiver is in standby. [assumed] The driver returns `Status(power=False)` with volume, mute and mode unset in that case.
- [assumed] `list_modes()` returns an empty list, rather than raising, when listening mode is unsupported (codes 3/12), when the receiver refuses the sound-field query (40000, e.g. standby), or when MODE was already dropped by `status()`. `mode_state()` also suppresses unsupported/standby mode data. Connection and HTTP failures still raise `DriverError`, so power control stays available while the receiver is in standby.
- [community] Some Sony TV configurations use a PSK authentication header. [assumed] No PSK field is included here because it was not established as necessary for the supported AVR/soundbar scope.
- [community] API method versions and supported endpoints are model/firmware-specific. [assumed] The fixed request versions above may need adjustment on some receivers.

## Unknowns and open questions

- [assumed] No real Sony receiver has verified request versions, response shapes, error-code meanings, standby behavior, authentication or power-on behavior in this driver.
- [assumed] Confirm whether every supported model accepts the empty `output` parameter, the string volume value, and `off` as the power setter's off-state.
- [assumed] Confirm which non-100 volume ranges are raw steps versus percent-like values, and whether API `step` values larger than one need special handling.
- [assumed] Confirm how accurately the UPnP description identifies TV-only devices and whether receivers report `macAddr` consistently.
- [assumed, not yet measured] Tester checklist: change volume with the remote while the panel is open; does the slider follow within 1 s?

## Sources

1. [spec] [Sony Audio Control API developer page](https://developer.sony.com/develop/audio-control-api) — inaccessible during this research; no hardware-specific claims here are presented as verified vendor-spec facts.
2. [community] [`rytilahti/python-songpal`](https://github.com/rytilahti/python-songpal) — GPL-3; read for protocol facts only, no code copied. It exposes the system power, identity, volume and sound-setting calls described above and negotiates supported method versions.
3. [community] [Home Assistant Songpal integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/songpal) — SSDP discovery and ScalarWebAPI base URL handling; the config flow filters BRAVIA TVs by advertised video service types.
4. [community] [Home Assistant Songpal integration documentation](https://www.home-assistant.io/integrations/songpal/) — supported-device and setup context.
5. [community] [`iclanton/SonyAudioControlApi`](https://github.com/iclanton/SonyAudioControlApi) — API wrapper reference for method names, response fields and example method versions.
