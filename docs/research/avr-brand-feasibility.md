# AVR brand feasibility

> Research snapshot (2026-10-31), gathered from public specs and open-source libraries; no hardware was tested as part of this research. The eISCP driver has separately been verified on a Pioneer VSX-935. Driver specs (`py_modules/avr_control/drivers/*.md`) are the implementation reference.

## Summary

The table distinguishes code that exists from protocols that remain research candidates. “Implemented” does not imply broad brand coverage: all listed drivers except eISCP are experimental and simulator-verified only; eISCP is verified on one Pioneer VSX-935, not every Onkyo, Integra, or Pioneer model. Model and firmware differences still affect scale, standby, and available modes. Source quality and implementation evidence are assessed separately below.

## Summary table

| Brand / protocol | Status | Spec quality | Discovery / identity | Modes | Stdlib-feasible | Score / confidence |
|---|---|---|---|---|---|---|
| Denon / Marantz: Telnet with HTTP/XML fallback | Implemented: driver `denon` (experimental) | Medium-high; Denon v8.6.0 is model-specific | SSDP; model/UDN | Code-side mode map, not a universal device-advertised list | Yes; TCP and HTTP/XML | **Easy / high** [1–4] |
| Denon / Marantz: HEOS CLI | Implemented: driver `heos` (experimental) | HEOS CLI v1.7 dates to 2016 | HEOS SSDP; player identity may change | Playback modes, not AVR surround modes | Yes; TCP | **Easy / high** [1–4] |
| Yamaha YXC / MusicCast | Implemented: driver `yamaha` (experimental) | High for YXC | SSDP; YXC exposes device ID/serial | API advertises sound programs and volume ranges | Yes; HTTP/JSON | **Easy / high** [5–6] |
| Yamaha Remote Control XML (YNC) | Implemented: driver `yamaha_ync` (experimental) | Older XML is incompletely documented | SSDP; UDN | Receiver configuration may provide mode lists | Yes; HTTP/XML | **Moderate / medium** [7] |
| Onkyo / Integra / Pioneer eISCP | Implemented: driver `eiscp` (verified on Pioneer VSX-935; other models unverified) | Medium; command tables derive from model docs and community implementation | UDP broadcast discovery to 60128; receiver identifier/model | `LMD` codes are model-dependent | Yes; TCP and UDP | **Easy to moderate / high** [8–9] |
| Legacy Pioneer telnet | Implemented: driver `pioneer_telnet` (experimental; VSX-935 uses eISCP) | Low-medium; tested examples are limited | Usually manual IP; stable ID/discovery not verified | No general dynamic list verified | Yes; TCP | **Moderate / medium** [10] |
| Sony STR-DN/ES ScalarWebAPI | Implemented: driver `sony` (experimental) | Medium; Sony developer docs were inaccessible during this research | SSDP advertises ScalarWebAPI URL; UDN/MAC | Sound-field candidates on supported models | Yes for HTTP/JSON; push/WebSocket is harder | **Moderate / medium** [24] |
| Anthem MRX/AVM | Not implemented | Medium-low; one current x40 link returned 404 | Manual IP; no verified auto-discovery | Family-specific code maps | Yes; TCP | **Moderate / medium** [11] |
| NAD T7xx / D-series | Not implemented | Low-medium for NAD v2.x | Model/build-specific identity and configuration | Settings vary by model | Yes for TCP | **Moderate / medium** [12] |
| Bluesound / BluOS | Not implemented | High for API subset v1.4 (2022), not full AVR control | mDNS; `/SyncStatus` returns MAC/model | No AVR listening-mode API | Yes; HTTP/XML | **Easy for player volume; not a full AVR driver / high** [20] |
| Arcam Radia AV | Not implemented | High for the documented models; Rev F is detailed | No broadcast discovery found; post-connect query returns model and MAC | Current mode and static choices, not dynamic supported-mode list | Yes; binary TCP | **Moderate / high** [13] |
| Rotel | Not implemented | High for RC-1590/RC-1590MKII only | TCP `discover?` returns IP, port, MAC | RC-1590 is a stereo preamp; no AVR mode API | Yes; ASCII TCP | **Moderate for that model; hard for Rotel AVR coverage / high** [14–15] |
| Emotiva | Not implemented | Medium; community integration supports selected processors | UDP broadcast on 7001; stable identity not verified | No universal dynamic mode list verified | Yes; TCP/UDP | **Moderate / medium** [16] |
| McIntosh | Not implemented | High for selected preamps; no general MX processor protocol verified | No discovery documented; some models emit serial number | No AVR mode list in reviewed preamp docs | Yes for documented TCP models | **Hard for AVR-wide support / high for narrow scope** [17] |
| Cambridge Audio StreamMagic | Not implemented | Low-medium; vendor installer guide, reverse-engineered API | Installer discovery exposes IP/MAC; API has unit ID | No general CXR surround-mode list established | WebSocket needs a hand-rolled client | **Moderate / medium** [18–19] |
| Harman Kardon AVR | Not implemented | Low; no full public spec found | Manual IP | No mode list; absolute volume/state feedback unavailable in HA reference integration | Yes, but limited control | **Not worth it for an absolute slider / medium** [21] |
| Sonos / generic UPnP RenderingControl | Implemented: driver `upnp` (experimental; volume and mute only) | High for the standard’s narrow scope | SSDP; UDN/device description | No standardized AVR power or listening modes | Yes; SSDP/SOAP/XML | **Moderate / high** [22–23] |
| Google Cast | Not implemented | Medium-low for protocol internals | mDNS `_googlecast._tcp.local`; UUID/model | Receiver volume/mute only; no AVR modes | Difficult: TLS and hand-coded protobuf framing | **Hard / medium** [25] |
| LG webOS TV / SSAP | Not implemented | Low for protocol spec; strong implementation examples | SSDP UDN; device info may expose MAC/serial | TV controls, not receiver surround modes | Moderate-hard: WebSocket framing and pairing | **Moderate / medium** [26] |
| Samsung Tizen TV remote WebSocket | Not implemented | Low; local remote API is largely community-documented | SSDP UDN; HA can also use DHCP MAC | Key-based TV control; no AVR modes | Moderate-hard: WebSocket and token handling | **Moderate / medium** [27] |
| Sony BRAVIA TV: REST/IRCC | Not implemented (TV control; distinct from the Sony AVR driver) | Medium; official BRAVIA Pro docs, model-specific | SSDP/API base URL; identity varies | TV sound settings only; not an AVR mode list | Yes for HTTP; IRCC is key-code oriented | **Moderate / medium** [24] |
| HDMI-CEC on host | Not implemented | High for the standard; Deck hardware/access uncertain | CEC logical/physical addresses, not a durable network ID | No listening modes | Host-specific `/dev/cecX`; not a portable Decky assumption | **Hard / low-medium** [28] |
| Home Assistant REST media player | Implemented: driver `homeassistant` (experimental; entity-dependent) | High for REST API; capabilities depend on entity | Manual HA URL/entity ID | Only if the entity exposes sound modes | Yes; HTTP/JSON | **Easy, conditional on HA / high** [29] |

## Per-protocol findings

### Denon and Marantz

- Denon’s official **AVR-1713/1613 IP Control Protocol v8.6.0** describes Telnet control on TCP 23; the vendor manual documents `Network Control: Always On` for remote operation during standby. AppCommand is a separate HTTP/XML interface, generally HTTP 80. The Denon document is useful but not a universal model-family contract. [1–2]
- The `denonavr` reference maps master volume to **−80 to +18 dB, in 0.5 dB steps**. It sends `MV` Telnet commands and HTTP/AppCommand volume paths; treat the on-wire numeric form as transport-specific rather than claiming one universal raw integer scale. Its sound-mode list is assembled from library mappings, not guaranteed to be queried as a supported-mode list from every receiver. [3]
- The implemented `denon` driver prefers Telnet and has an HTTP/XML fallback; it does not use AppCommand. The TCP path is serialized through the shared receiver session. The D&M library warns of a single Telnet client limit, while HTTP avoids occupying that Telnet slot. [3–4]
- HEOS is a **separate** control surface: vendor CLI spec v1.7 (2016) uses SSDP target `urn:schemas-denon-com:device:ACT-Denon:1`, then Telnet TCP 1255 with ASCII commands and JSON responses. It supports player volume 0–100 and mute; the spec permits up to 32 simultaneous sockets and recommends one socket to a HEOS device to control the system. HEOS player IDs are not documented as stable across reset/reconfiguration, and HEOS volume is not proof of main-zone AVR control. [2]
- Reference: `ol-iver/denonavr` (MIT): `denonavr/volume.py:100-112,327-348`, `denonavr/soundmode.py:498-549`, `README.md:38-40`; Home Assistant discovery: `home-assistant/core:homeassistant/components/denonavr/config_flow.py:280-312,326-329`.

### Yamaha

- The Yamaha-authored **Yamaha Extended Control API Basic Rev. 2.00 (2018)** is unusually complete (129 pages) for MusicCast devices with firmware supporting the relevant API version. It specifies HTTP/JSON endpoints under `/YamahaExtendedControl`; `getDeviceInfo` reports model, device ID and serial; `getFeatures` reports per-zone volume `min/max/step`, selectable sound-program lists, and functions. Use the advertised range and step, not a fixed model-year formula. [5, pp. 1–11]
- The same spec documents power, mute, `getSoundProgramList`, and `get/setNetworkStandby`. HA discovers via SSDP and uses a serial number as unique ID. [5, pp. 27–28, 48–55; 6]
- For older receivers, distinguish **Yamaha Remote Control XML** from YNCA: XML is model-specific and commonly uses `/YamahaRemoteControl/ctrl`; the public material found was community/reverse-engineered, not a complete vendor spec. YNCA is a separate protocol family, not the same XML API. Do not mix their transport or commands. [7]
- Reference: `rsc-dev/pyamaha` (MIT; uses `requests`, so not itself stdlib-only): `pyamaha/__init__.py:157-189`, `setup.py:1-27`; HA SSDP: `home-assistant/core:homeassistant/components/yamaha_musiccast/config_flow.py:91-107`.

### Onkyo / Integra / Pioneer eISCP and legacy Pioneer

- eISCP uses TCP 60128; the reference implementation also broadcasts Onkyo/Pioneer discovery messages over UDP to 60128. Its command database includes volume, power, mute, and listening-mode commands. [8]
- Volume encoding varies: command definitions include ranges such as **0–80, 0–100 and 0–200**, while HA separately offers volume-resolution choices **50/80/100/200** and a configurable `max_volume`. Thus no dependable brand-wide “2018+ max volume” rule was verified. Model-specific configuration and hardware-reported identity are safer than a guessed cutoff. [8–9]
- The implemented `eiscp` driver already supports manual per-model scale overrides (`volume_steps`, `zero_db`, `max_volume`, and `volume_unit`). Those overrides avoid treating community range lists as universal defaults; they are not a substitute for model-specific hardware evidence.
- `LMD` can report/set a mode code, but the selectable list is code-side and model-dependent; no universal dynamic mode-list query was verified. No auth or universal connection limit was found in the reviewed sources. Network standby must be enabled on models that otherwise stop accepting commands while asleep. [8–9]
- Legacy Pioneer telnet is different: `pyoneer_vsx` defaults to TCP 23 and documents raw volume **0–185**: 0 is silent, 1 is −80 dB, then 0.5 dB steps through +12 dB. Network Standby is needed for standby control. Its persistent connection can block other control apps; it was tested on a small set of models, not the VSX-935 eISCP case. [10]
- References: `miracle2k/onkyo-eiscp:eiscp/core.py:322-360,369-398`, `eiscp/commands.py:1479-1491,1675-1688` (MIT); HA scale and normalization: `home-assistant/core:homeassistant/components/onkyo/const.py:13-21`, `media_player.py:294-307`; Pioneer: `j-mutter/pyoneer_vsx:receiver.py:220-278`, `connection.py:21-46` (Apache-2.0).

### Sony, Anthem, NAD and Arcam

- **Sony AVR ScalarWebAPI** is JSON-RPC over HTTP, commonly discovered through SSDP’s `X_ScalarWebAPI_BaseURL`; do not hardcode a port when the device description supplies one. HA reads `getVolumeInformation` for device min/max, and `getSoundSettings` for available sound-field candidates on supported models. UDN/MAC can identify a device. PSK authentication is model/configuration-dependent; Sony’s developer portal was inaccessible during this research, so treat receiver coverage as model-specific. The GPL-3 `python-songpal` project is a read-only reference, not code to copy. [24]
- **Anthem** reference code uses TCP 14999. X20 volume converts a 0–100 UI value to approximately −90..0 dB attenuation; x40 uses `PVOL` 0–100, with no universal dB conversion established. Power/mute commands exist; network standby control `SIP` must be enabled to accept IP power-on. Mode maps are family-specific; a linked x40 vendor spreadsheet returned 404. The library warns that persistent control can conflict with other controllers. [11]
- **NAD v2.x** is ASCII over serial or TCP 23 (`Main.Volume?`, `Main.Volume=-30`); model/build configuration determines setting ranges. T755/T757 are listed as tested; T778 is untested. NAD D-series binary TCP 50001 is a separate unsupported protocol in the cited library. Standard TCP is easy to implement with `socket`; no universal mode-list or connection-limit behavior was verified. [12]
- **Arcam Radia AV Rev F** is a high-quality official binary control spec for **AVA15/25/35 and AVP45**, not all Arcam AVR models. IP port 50000; framing uses start `0x21` and end `0x0D`. Volume and max-volume are each raw integers 0–100; the spec gives no dB conversion. Mute and power are explicit commands. Current upmixer and 2-channel/MCH decode modes can be queried, with enumerated code tables, but not a dynamic list of modes each device supports. `NetworkInfo` returns MAC and `SystemModel` returns the model. Auth, discovery, and client limits are not specified. [13, pp. 3–20]
- Older AVR500/600/888 serial material is a separate protocol; an RS-232/Ethernet bridge in an open-source project is not evidence that those receivers natively listen on TCP 50000. [14]

### Rotel, Emotiva, McIntosh, Cambridge and Harman Kardon

- **Rotel RC-1590/RC-1590MKII** has an official v2.0 protocol dated July 2019: TCP 9590, ASCII commands terminated by `!`, volume 01–96, mute and power. No dB mapping is specified. `discover?` returns IP/port/MAC. Firmware v1.40 changed response/command conventions. This is a stereo preamp, not evidence for Rotel AV processors. [15]
- **Emotiva XMC-2** is the model tested by the openHAB binding: UDP 7001 discovery, TCP 7002 control, UDP 7003/7005 notifications, plus configurable info/setup ports. Binding defaults list −96 to +15 dB; percentage volume and dB are exposed separately, but a universal raw-to-dB formula was not verified. No stable ID or dynamic listening-mode list was confirmed. The binding warns that excessive channel traffic can make a processor unresponsive. [16]
- **McIntosh** now publishes model-specific control specs for C55/C2800 (TCP 84) and CR106 (TCP 57012), using ASCII commands such as `(VOL 65)`, `(MUT 1)`, `(PWR 1)`; volume is 0–100 percent, with no dB mapping. The devices emit model/serial/firmware information on startup. These are preamps/multiroom products, not proof of a universal MX AV-processor protocol; MX support remains unverified. [17]
- **Cambridge Audio StreamMagic** has a vendor Control4 guide (2016) covering CXR-120/200 and streamers, but not a public full protocol spec. `aiostreammagic` shows `ws://host/smoip` on port 80. HA treats preamp volume as percent/100 and supports mute/power; the library does not establish a generic CXR surround-mode list. The guide warns ECO standby after a full mains reset can prevent network power-on until the front panel is used. WebSocket requires hand-rolled framing in a stdlib-only backend. [18–19]
- **Harman Kardon**: HA uses TCP 10025 by default and documents AVR-151S as tested, others untested. Its integration supports volume steps, mute, power and source selection, but HA explicitly says there are no endpoints to determine volume, mute or playback state; newest firmware may also shut the AVR off from the network so “on” fails. That makes a reliable absolute slider a poor investment. `hkavr` is MIT, but its existence does not substitute for a vendor spec. [21]

### BluOS, Sonos/UPnP, Cast, TVs, CEC and Home Assistant

- **BluOS** vendor Custom Integration API v1.4 (2022) is HTTP GET/XML on TCP 11000 (CI580 nodes also use 11010/11020/11030), discovered via mDNS `_musc._tcp` / `_musp._tcp`. `/SyncStatus` returns model and MAC. Volume supports 0–100 and absolute dB; available range is typically −80..0 but configurable. The API is a player integration, not a guarantee of AVR power or surround-mode control. It recommends polling no faster than once per 30 seconds unless long polling. [20, pp. 1–13]
- **Sonos and generic UPnP RenderingControl**: SSDP discovers a renderer and its UDN/service URL; SOAP actions provide `Get/SetVolume` and `Get/SetMute`. RenderingControl volume is generally 0–100; dB conversion is device-specific/optional. The standard does not provide generic receiver power or listening modes. SoCo is an MIT reference (`soco/core.py:991-1026`, `soco/discovery.py:28-44`). UPnP’s standard is useful as a partial fallback, not an AVR feature-equivalent API. [22–23]
- The `upnp` driver now implements this deliberately partial volume/mute route, using the renderer description's service URLs and range where available. It does not claim generic power or listening-mode support. [22–23]
- **Google Cast**: `_googlecast._tcp.local` provides device identity; Cast V2 uses TLS on TCP 8009, length-framed protobuf messages. Receiver volume is 0.0–1.0 plus mute, not AVR dB; no mode or power API. `pychromecast` is MIT but depends on protobuf and zeroconf. A narrow stdlib client is possible, but hand-writing protocol framing and discovery is materially harder than HTTP APIs. [25]
- **LG webOS**: SSAP uses WebSocket 3000 or TLS 3001, with first-use on-screen approval and a persisted client key. `aiowebostv` exposes 0–100 volume, mute and power controls; SSDP UDN is a practical identity. No AVR listening modes. LG ThinQ uses cloud access-token/PAT authentication, not a local LAN control API; it is not advisable as a local-plugin fallback. [26]
- **Samsung Tizen**: community remote API uses WebSocket 8001/8002 and a saved token/approval flow. Its remote-key interface exposes volume-up/down and mute keys rather than a guaranteed absolute volume scale. HA uses SSDP UDN and can fall back to DHCP MAC. Some TVs expose separate UPnP volume control; do not assume it. [27]
- **Sony BRAVIA TV**: Sony’s BRAVIA Pro REST/IRCC documentation is public and model-specific. ScalarWebAPI base URL and PSK requirements are device-provided/configured; IRCC is remote-key oriented, while REST may expose absolute device-reported volume. TV sound settings are not AVR surround modes. [24]
- **HDMI-CEC**: Linux provides `/dev/cecX` only when hardware/driver supports it. CEC 2.0 defines `Set Audio Volume Level` opcode `0x73` (0–100), but device/adapter support is optional; relative volume-up/down/mute is more common. SteamOS/Deck access and bundled `cec-client`/libCEC availability were not verified. libCEC is GPL-2.0 or commercially licensed; do not copy it into a proprietary plugin. [28]
- **Home Assistant REST**: HTTP/JSON, normally port 8123, with a bearer long-lived access token. The implemented `homeassistant` driver reads entity state and calls services using `urllib`/`json`; volume is normalized from 0–1, while modes and power are entity-dependent. Its tradeoff is requiring users to run/configure HA and protect a sensitive token. [29]

## Recommendations — remaining work only

1. **Choose new brand work from concrete model demand.** Anthem, Arcam Radia AV, and BluOS are plausible next research candidates, not current drivers. Keep scope explicit: documented Anthem/Arcam families only, and BluOS as a player-control route rather than a full AVR. NAD and Cambridge likewise need a named target model before implementation effort is justified. [11–13, 18–20]
2. **Treat Rotel, Emotiva, and McIntosh evidence as narrow-scope.** The reviewed protocols cover a Rotel stereo preamp, selected Emotiva processors, and McIntosh preamp/multiroom models—not broad AV-processor families. Require a specific model/use case before adding any of them. [14–17]
3. **Keep Cast, TV control, and host HDMI-CEC deferred.** They do not provide the AVR feature set, or depend on pairing, TLS/protobuf, host hardware, and SteamOS support that is not established here. Revisit only for a clear user request and a supported integration seam. [25–28]
4. **Investigate Yamaha YXC UDP events separately.** The `yamaha` driver currently polls HTTP/JSON; whether MusicCast notifications carry the relevant receiver state is unverified. Collect model-specific evidence before adding a listener, and retain polling as the fallback. [5–6]

### Contributor workflow already in use

`tools/avr_probe.py` records supported receiver exchanges through a local proxy, read-only by default. Sanitized JSON transcripts are contributed with receiver reports and committed as fixtures under `tests/transcripts/`; CI replays them against the drivers. This is the workflow recorded in ADR-0013, not a proposed probe-kit project. Transcript replay makes captured wire behavior reproducible, but does not by itself promote a driver to hardware-verified; all drivers other than eISCP remain experimental until an owner confirms behavior on a real model.

### Risks and gaps

- **Licensing:** MIT/Apache/EPL references are generally useful for study; do not copy GPL-3 `python-songpal` or GPL-2 libCEC into the plugin. LG/Samsung references are LGPL-3. Rotel repository README/license disagree (MIT vs Apache-2.0); resolve before reuse. [3, 10–12, 15–19, 24–28]
- **Model drift:** Vendor APIs are not consistently versioned across product years. Keep range/mode mapping model-specific and prefer runtime capability discovery where available.
- **Single-client behavior:** Denon Telnet and Pioneer telnet can conflict with other controllers; their implemented TCP paths use the shared receiver session. Any future Anthem driver would need equivalent ownership. HEOS has a documented 32-connection limit. [3, 10–11, 2]
- **Standby/security:** Many receivers need network standby enabled. Local protocols often lack authentication; restrict control to the LAN. Home Assistant tokens and TV pairing keys are secrets. Always clamp volume and avoid surprise power/mode changes.
- **Unverified:** Anthem’s linked x40 sheet currently returns 404; Sony’s developer docs were inaccessible; no SteamOS CEC device access was verified. Rotel’s reviewed spec covers a stereo preamp, McIntosh’s reviewed vendor specs cover preamps, and Cambridge’s public guide does not disclose its full API. No hardware was tested in this research.

## Numbered sources

1. [Denon AVR-1713/1613 IP Control Protocol v8.6.0](https://assets.denon.com/DocumentMaster/UK/AVR1713_AVR1613_PROTOCOL_V860.pdf); Denon [Network Control manual example](https://manuals.denon.com/AVRX3200W/NA/EN/HJWMSYmehwmguq.php).
2. HEOS CLI Protocol Specification v1.7 (2016), [vendor download URL](http://rn.dmglobal.com/euheos/HEOS_CLI_ProtocolSpecification.pdf); mirrored copy at `fhem/HEOS:HEOS_CLI_ProtocolSpecification.pdf`, pp. 1–4.
3. `ol-iver/denonavr`: `denonavr/volume.py:100-112,327-348`, `soundmode.py:498-549`, `README.md:38-40`; MIT license.
4. `home-assistant/core:homeassistant/components/denonavr/config_flow.py:280-312,326-329`.
5. Yamaha, [YXC API Specification Basic Rev. 2.00 (2018)](https://raw.githubusercontent.com/rsc-dev/pyamaha/master/doc/YXC_API_Spec_Basic_v2.0.pdf), pp. 1–11, 27–28, 48–55.
6. `home-assistant/core:homeassistant/components/yamaha_musiccast/config_flow.py:91-107`; `rsc-dev/pyamaha:setup.py:1-27` (MIT; depends on `requests`).
7. `mvdwetering/ynca` and `mvdwetering/yamaha_ynca` (community implementation); older Yamaha XML evidence is model-specific, not a complete current vendor API spec.
8. `miracle2k/onkyo-eiscp:eiscp/core.py:322-360,369-398`, `eiscp/commands.py:1479-1491,1675-1688` (MIT).
9. `home-assistant/core:homeassistant/components/onkyo/const.py:13-21`, `media_player.py:294-307`, `config_flow.py:195-231`.
10. `j-mutter/pyoneer_vsx:receiver.py:220-278`, `connection.py:21-46`, `README.md:39,43,54` (Apache-2.0).
11. `nugget/python-anthemav:README.rst:20-25,33-45,95,108-112`, `anthemav/protocol.py:682-697,902-918,1284-1355` (MIT); linked x40 vendor spreadsheet URL currently returns 404.
12. `rrooggiieerr/nad_serial.py:README.md:28-51,103-115`, `configs/README.md:3-140` (Apache-2.0).
13. [ARCAM Radia AV Series Communication Protocol Rev F](https://www.arcam.co.uk/ugc/tor/AVA25/Control%20Protocols/ARCAM%20Radia%20AV%20Series%20Comm%20Protocol%20Rev_F.pdf), pp. 3–20.
14. `majherek/arcam-rs232:README.md:3-29,379-407,474-476` (MIT); older serial protocol, not proof of native TCP.
15. Rotel, [RC-1590/RC-1590MKII RS232/IP ASCII Controller Command List v2.0](https://rotel.com/sites/default/files/product/rs232/RC1590MKII%20Protocol.pdf), pp. 1–7; `oleggalch/rotel-homeassistant:README.md:5-62` and `LICENSE` (license discrepancy to resolve).
16. `openhab/openhab-addons:bundles/org.openhab.binding.emotiva/README.md:5-46,71-92`, `EmotivaBindingConstants.java:80-90`, `EmotivaConfiguration.java:19-39` (EPL-2.0).
17. McIntosh [C55/C2800 External Control Rev A](https://www.mcintoshlabs.com/-/media/Files/mcintoshlabs/DocumentMaster/us/C55-C2800-External-Control.pdf) and [CR106 External Control Rev A](https://www.mcintoshlabs.com/-/media/Files/mcintoshlabs/DocumentMaster/us/CR106-External-Control.pdf); `stefan747/node-mcintosh:LICENSE` (Apache-2.0).
18. Cambridge Audio, [Stream Magic Control4 Driver User Guide](https://www.cambridgeaudio.com/sites/default/files/compliance/doc/CambridgeAudioSMOIP_Control4_User_Guide.pdf), pp. 1–5, 9.
19. `noahhusby/aiostreammagic:README.md:11-35,65-101`, `aiostreammagic/stream_magic.py:146-166,193-217`, `models.py:110-120` (MIT).
20. Lenbrook, [BluOS Custom Integration API v1.4 (2022)](https://content-bluesound-com.s3.amazonaws.com/uploads/2022/04/Custom-Integration-API-v1.4.pdf), pp. 1–13.
21. Home Assistant, [Harman Kardon AVR integration](https://www.home-assistant.io/integrations/harman_kardon_avr/); source `home-assistant/core:homeassistant/components/harman_kardon_avr/media_player.py:1-36,53-107,126-137`.
22. `SoCo/SoCo:soco/core.py:991-1026`, `soco/discovery.py:28-44` (MIT).
23. ISO/IEC 29341-20-13:2017, [UPnP RenderingControl service standard](https://www.iso.org/standard/69149.html) (full standard is paywalled).
24. Sony [BRAVIA Pro REST API structure](https://pro-bravia.sony.net/remote-display-control/rest-api/structure/); `home-assistant/core:homeassistant/components/songpal/config_flow.py:109-138`, `media_player.py:128-158,283-333,363-410`; `rytilahti/python-songpal` (GPL-3; read-only reference).
25. Google [Cast protocol flow](https://developers.google.com/cast/docs/protocol); `balloob/pychromecast:socket_client.py:166,216,359-361` (MIT).
26. `home-assistant-libs/aiowebostv:webos_client.py:36-48,137-182`, `README.md:48-79` (LG SSAP reference; LGPL-3); HA discovery at `home-assistant/core:homeassistant/components/webostv/config_flow.py:113-136`.
27. `xchwarze/samsung-tv-ws-api:samsungtvws/connection.py:36-115`, `remote.py:185-217`, `__init__.py:1-12` (LGPL-3); HA discovery at `home-assistant/core:homeassistant/components/samsungtv/config_flow.py:520-572`.
28. Linux [CEC userspace API introduction](https://docs.kernel.org/userspace-api/media/cec/cec-intro.html); `torvalds/linux:include/uapi/linux/cec.h`; Pulse-Eight libCEC is GPL-2.0 or commercially licensed.
29. Home Assistant [REST API documentation](https://developers.home-assistant.io/docs/api/rest/) (authentication, state and service-call endpoints).
