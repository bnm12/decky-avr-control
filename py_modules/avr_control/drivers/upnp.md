# UPnP RenderingControl (`upnp`)

**Source tags:** **[measured]** seen on real hardware · **[spec]** from a vendor or protocol specification · **[community]** from an open-source library or community protocol documentation · **[assumed]** implementation choice or unverified behavior.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/upnp.py` |
| Brands / models | [spec][1] Any renderer exposing RenderingControl:1; [community][3][4] Sonos players |
| Verified on | [assumed] None (simulator only) |
| Transport | [spec][1] SOAP 1.1 over HTTP to the service's advertised control URL; [community][3] Sonos normally listens on TCP 1400 |
| Discovery | [spec][2] SSDP `LOCATION` and device UDN; search target is RenderingControl:1 |
| Connections | [assumed] One HTTP request per action; no persistent session or push subscription |
| State updates | [assumed] Polling only; the HTTP/SOAP driver does not subscribe to renderer events. |

This is deliberately a partial receiver driver: only volume and mute are offered. [spec][1] The generic RenderingControl service does not define receiver power or listening-mode controls.

## Transport and framing

[spec][1] The control point reads the device description, finds a service with service type `urn:schemas-upnp-org:service:RenderingControl:1`, and sends SOAP 1.1 actions to its `controlURL`. Relative `controlURL` and `SCPDURL` values are resolved against `URLBase` when present, otherwise against the description `LOCATION`. [assumed] Requests use a two-second timeout and `Content-Type: text/xml; charset="utf-8"`.

Example read request (XML body):

```http
POST /MediaRenderer/RenderingControl/Control HTTP/1.1
SOAPACTION: "urn:schemas-upnp-org:service:RenderingControl:1#GetVolume"
Content-Type: text/xml; charset="utf-8"

<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:GetVolume xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">
      <InstanceID>0</InstanceID>
      <Channel>Master</Channel>
    </u:GetVolume>
  </s:Body>
</s:Envelope>
```

[spec][1] The response's `GetVolumeResponse/CurrentVolume` is an integer; mute is returned by `GetMuteResponse/CurrentMute`. [assumed] SOAP faults become `Refused`; transport failures, malformed XML, and missing response values become `DriverError`. Setters issue the Set action and then read back the value to report what the renderer confirmed.

## Discovery

[spec][2] LAN discovery sends SSDP M-SEARCH with `ST: urn:schemas-upnp-org:service:RenderingControl:1`, fetches each response's `LOCATION`, and inspects the device description. The service may belong to a nested device, so the driver searches the device tree and uses that service-owning device's `UDN`, manufacturer, and model metadata. The `UDN` is the receiver ID and is saved in `Found.config` so reconstruction selects the same renderer's service when one description contains several renderers.

[assumed] A manual probe first tries `http://<host>:<port>/xml/device_description.xml` (Sonos convention; default port 1400), then sends a unicast M-SEARCH to `<host>:1900` if that description is unavailable. An explicit `config["port"]` overrides the Sonos default. It validates the service and performs read-only `GetVolume` before returning a `Found`. A saved `location` is reused only when its URL host resolves to the configured host; IP literals are compared in canonical form, and hostnames are resolved with a short timeout. Otherwise the driver discards it and resolves from the configured host. Sonos service control URLs are taken from the description rather than hardcoded. [community][3][4]

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [spec][1] `GetVolume` | [spec][1] `SetVolume` | `CurrentVolume` | [spec][1] `InstanceID=0`, `Channel=Master`; clamp and quantize down to the SCPD `Volume` allowed range when available, so a discrete step never exceeds the requested value |
| Mute | [spec][1] `GetMute` | [spec][1] `SetMute` | `CurrentMute` | [spec][1] `DesiredMute` / `CurrentMute` use `0` or `1`; always `InstanceID=0`, `Channel=Master` |
| Power | — | — | — | [spec][1] Not part of this driver's capabilities |
| Listening mode | — | — | — | [spec][1] Not part of this driver's capabilities |

## Volume scale

[spec][1] The SCPD `Volume` state variable may provide `minimum`, `maximum`, and `step`; the driver reads those values when available. [assumed] Without a usable range, the driver uses 0–100, step 1. The default cap is raw value 40, clamped to the reported range. Requested volumes are floored to the nearest legal step at or below the request, never below the reported minimum.

[community][3][4] Sonos commonly uses the 0–100 volume range, represented as `%`. [assumed] For a non-0–100 range, the driver preserves the renderer's raw minimum, maximum, and step and still labels the scale `%` because `VolumeScale` only supports `dB` or `%`; this is not normalized to 0–100 and is not a dB conversion.

## Listening modes

[spec][1] Unknown / unsupported; this driver does not expose listening modes.

`mode_state()` returns `current=None`, no selectable modes, and `complete=false`
without I/O [assumed]. The base `Mode {code}` label is used for any saved
unknown code [assumed].

## Quirks and gotchas

- [community][3][4] Sonos RenderingControl is per-player; this driver uses each player's advertised RenderingControl service rather than GroupRenderingControl.
- [community][3] The Sonos control URL commonly looks like `http://<player>:1400/MediaRenderer/RenderingControl/Control`. The description remains authoritative.
- [assumed] A Set action's response is not treated as a confirmed value; the driver follows it with the corresponding Get action.
- [assumed] The probe is read-only and requires `GetVolume` to succeed; it does not test mute by changing state.
- [assumed] The transcript probe kit derives the TCP endpoint from the description's RenderingControl control URL and routes both the rewritten description `LOCATION` and service URLs through its recording proxy. Relative service URLs are first resolved against the original `URLBase` or description `LOCATION`; the proxy rewrite changes only their authority, preserving their paths.

## Unknowns and open questions

- [assumed] No physical renderer has verified behavior, SOAP fault variants, or response timing.
- [assumed] Confirm whether non-Sonos renderers consistently implement `InstanceID=0` and `Channel=Master`, and whether their SCPDs accurately describe the volume step.
- [assumed] The non-0–100 `%` display is a documented compatibility choice, not a standardized UPnP scale.
- [assumed, not yet measured] Tester checklist: change volume with the remote while the panel is open; does the slider follow within 1 s?

## Sources

1. [UPnP Forum, RenderingControl:1 Service Template (PDF)](https://upnp.org/specs/av/UPnP-av-RenderingControl-v1-Service.pdf).
2. [UPnP Forum, Device Architecture 2.0 (PDF)](https://upnp.org/specs/arch/UPnP-arch-DeviceArchitecture-v2.0.pdf).
3. [Sonos UPnP API, RenderingControl service](https://sonos.svrooij.io/services/rendering-control) (community documentation; service type, control URL, SOAP example and volume/mute actions).
4. [SoCo, `soco/core.py`](https://github.com/SoCo/SoCo/blob/master/soco/core.py) and [`soco/discovery.py`](https://github.com/SoCo/SoCo/blob/master/soco/discovery.py) (MIT-licensed implementation reference; see [license](https://github.com/SoCo/SoCo/blob/master/LICENSE)).
