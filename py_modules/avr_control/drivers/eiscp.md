# Onkyo-style eISCP (`eiscp`)

**Source tags:** **[measured]** seen on real hardware · **[spec]** vendor documentation · **[community]** open-source library or forum · **[assumed]** unverified implementation detail.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/eiscp.py` |
| Brands / models | [community] Onkyo, Pioneer and Integra receivers implementing ISCP/eISCP; recent networked models |
| Verified on | [measured] Pioneer VSX-935 |
| Transport | [measured] TCP and UDP over IPv4; TCP port 60128; ISCP packet framing |
| Discovery | [measured] UDP broadcasts to port 60128; stable ID is the receiver's ECN identifier (looks like a MAC address) |
| Connections | [measured] A VSX-935 accepts roughly one control client reliably; close the Pioneer app while collecting modes |

## Transport and framing

[spec] An eISCP packet is `ISCP`, a 16-byte header, then the payload. The header includes its own length, payload length, and version; control payloads are ASCII and end in carriage return. A command starts with `!1`, for example `!1MVLQSTN\r`. Requests use one TCP connection; status requests are batched on a single connection.

[measured] A VSX-935 sends an unsolicited `!1NJA2-http://<ip>/album_art.cgi` packet on connect. It must be skipped while waiting for replies. The response terminator observed after MVL data is `\x1a\r\n`.

[measured] The exact payload length matters: a wrong declared length for `!1MVLQSTN\r` made the VSX-935 wait indefinitely. The driver reads the exact declared payload length and ignores unrelated pushes.

## Discovery

[spec] Broadcast the ISCP-wrapped probes `!xECNQSTN\r` and `!pECNQSTN\r` to `255.255.255.255:60128` and the local `/24` broadcast. Bind the UDP source socket to port 60128 so receivers that reply to that port can be heard; fall back to an ephemeral source port if binding is denied. Resend approximately once per second.

[measured] A VSX-935 replied to the Pioneer probe with `!1ECNVSX-935/60128/XX/0009B0617D30`. The fields are model, TCP port, region, and ID; the final value is treated as the stable ID.

[measured] Windows can receive the broadcast response; WSL2 NAT did not. UDP bind to 60128 can be forbidden on Windows, where the ephemeral-port fallback is needed.

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [community] `MVLQSTN` | [measured] `MVLxx` | [measured] `MVLxx` (hex raw value) | Query is batched with mute, mode and power; volume may be absent in standby |
| Mute | [community] `AMTQSTN` | [community] `AMT00` / `AMT01` | [community] `AMT00` / `AMT01` | `00` unmuted, `01` muted |
| Power | [community] `PWRQSTN` | [community] `PWR00` / `PWR01` | [community] `PWR00` / `PWR01` | [assumed] `00` off, `01` on |
| Listening mode | [community] `LMDQSTN` | [community] `LMD<code>` | [measured] `LMD<code>` | [assumed] A timeout/refusal is reported as an unavailable mode for the current input or signal |

## Volume scale

[measured] On a Pioneer VSX-935, `MVL30` (hex `0x30`, raw 48) displayed `-58.0 dB`; raw 50 displayed `-57.0 dB`. This establishes 0.5 dB per step and extrapolates 0 dB to raw 164.

[assumed] Driver range is raw 0–185. The historical default slider cap is raw 100, and the historical absolute cap is 185; the driver therefore clamps at 185 even though the ISCP value field can represent through `0xC8`. Other receiver models may use a different useful range.

## Listening modes

[measured] Picker order and codes collected from a Pioneer VSX-935:

| Code | Name |
|---|---|
| `FF` | Auto Surround |
| `00` | Stereo |
| `01` | Direct |
| `11` | Pure Direct |
| `0F` | Mono |
| `13` | Mono Music |
| `0C` | Extended Stereo |
| `0D` | Front Stage Surround |
| `80` | Dolby Audio - Surround |
| `82` | DTS Neural:X |
| `17` | DTS Virtual:X |
| `03` | Advanced Game |
| `05` | Action |
| `06` | Rock/Pop |
| `08` | Classical |
| `09` | Unplugged |
| `0A` | Entertainment Show |
| `0B` | Drama |
| `0E` | Sports |

[spec] Seventeen of these nineteen entries line up with slots in Onkyo ISCP specification v1.15 (2009). Codes not in the table are still accepted and displayed as `Mode XX`.

## Quirks and gotchas

- [measured] NJA album-art pushes can arrive before a query response.
- [measured] One control client works most reliably on the tested VSX-935.
- [measured] Standby may answer PWR while omitting volume, mute and mode. Status tolerates missing volume and mute only when PWR reports standby; while power is on or unknown, both are awaited up to the normal two-second timeout. Listening mode remains optional with a short grace period.
- [measured] WSL2 NAT can prevent discovery replies from reaching the client.
- [assumed] Power command flag meanings follow the common Onkyo ISCP convention.

## Unknowns and open questions

- [assumed] The 0–185 range, zero reference and cap are based on the VSX-935 and may vary by model or firmware.
- [assumed] Power behavior and its numeric flags have not been independently measured on the VSX-935.
- [measured] A VSX-935 scan also showed ports 80, 7000, 8008, 8009, 8443, 8554, 8888, 9013, 10001, 10109–10120, 39009 and 60228; this driver only uses 60128.
- [assumed] Confirm how additional Onkyo/Integra models report standby and which listening modes are available for each input.

## Sources

1. [spec] [Onkyo ISCP protocol specification v1.15 (2009)](https://community.symcon.de/uploads/short-url/7mxbIQ7qRIghfbEQrvcrEkU57ad.pdf)
2. [community] [miracle2k/onkyo-eiscp](https://github.com/miracle2k/onkyo-eiscp)
3. [community] [openHAB Pioneer AVR binding](https://www.openhab.org/addons/bindings/pioneeravr/)
