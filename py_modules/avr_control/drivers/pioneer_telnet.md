# Pioneer legacy text protocol (`pioneer_telnet`)

**Source tags:** **[measured]** seen on real hardware · **[spec]** vendor documentation · **[community]** open-source library or forum · **[assumed]** unverified implementation detail.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/pioneer_telnet.py` |
| Brands / models | [community] Older Pioneer receivers, including VSX-529, VSX-921 and VSX-1021 examples; generally pre-2016 |
| Verified on | [assumed] None; the tested VSX-935 does not speak this protocol |
| Transport | [community] TCP text commands; port 8102 documented for the VSX-529, CRLF terminated; [assumed] port 23 is also probed |
| Discovery | [assumed] None implemented; enter the receiver host manually |
| Connections | [assumed] One transaction per TCP connection; the shared session serializes transactions for the same host and port |

## Transport and framing

[community] Commands are ASCII text terminated by `\r\n`; responses are read as lines until the expected reply arrives. The driver tests port 23 then 8102 and remembers the working port in the receiver config. A typical volume exchange is `?V\r\n` → `VOL###`, where `###` is decimal. The shared `ReceiverSession` owns each transaction's TCP stream, serializes concurrent calls, and closes the stream after the reply or failure.

[assumed] This framing and the per-request connection behavior have not been verified on hardware for this project.

## Discovery

[assumed] No discovery protocol is implemented. A user supplies the receiver host, after which the driver probes `?V` on TCP ports 23 and 8102. The host address is used as the provisional ID; no stable hardware identifier is available.

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [community] `?V` | [community] `###VL` | [community] `VOL###` | Decimal raw value, 0–185 |
| Mute | [community] `?M` | [community] `MO` / `MF` | [community] `MUT0` / `MUT1` | [community] `MUT0` muted, `MUT1` unmuted |
| Power | [community] `?P` | [community] `PO` / `PF` | [community] `PWR0` means on and `PWR2` means standby; [assumed] `PWR1` is also treated as off |
| Listening mode | Unknown | Unsupported | Unknown | [assumed] Not exposed by this driver |

## Volume scale

[community] The implementation uses raw 0–185, 0.5 dB per step, with raw 161 equal to 0 dB and a default cap of 161. Published ranges vary: the VSX-529 example reports 000–131.

[assumed] These limits and the zero reference have not been verified on hardware for this project and may vary by model.

## Listening modes

[assumed] This driver does not expose listening modes. Whether legacy receivers offer a compatible query/set pair is unknown.

`mode_state()` returns no current or selectable modes with `complete=false`
without contacting the receiver [assumed]. Unknown display codes use the base
`Mode {code}` fallback [assumed].

## Quirks and gotchas

- [community] Power flags are inverted relative to their apparent boolean values: `PWR0` means on and `PWR2` means standby; [assumed] `PWR1` is also accepted as off.
- [community] Port 23 is tried before 8102; the port that responds is saved in configuration.
- [measured] A Pioneer VSX-935 refused connections on both ports 23 and 8102; it uses eISCP instead.

## Unknowns and open questions

- [assumed] No real hardware has validated command spelling, line framing, volume scale, mute, power, or the two ports for this implementation.
- [assumed] Confirm whether the PWR flag mapping and the `PO`/`PF` commands are consistent across supported legacy models.
- [assumed] Determine whether any legacy model supports listening-mode control and stable-ID discovery.

## Sources

1. [community] [Arno Welzel: Control AV receivers by Pioneer over the network](https://arnowelzel.de/en/control-av-receivers-by-pioneer-over-the-network) (documents `PWR0` as on and `PWR2` as standby for the VSX-529)
2. [community] [Raymond Julin: Remote control your Pioneer VSX receiver over telnet](https://www.raymondjulin.com/blog/remote-control-your-pioneer-vsx-receiver-over-telnet)
3. [spec] [Pioneer VSX-1120-K RS-232 guide](http://www.pioneerelectronics.com/StaticFiles/PUSA/Files/Home%20Custom%20Install/VSX-1120-K-RS232.PDF) (serial protocol reference; network framing may differ)
4. [measured] Local network check on a Pioneer VSX-935: ports 23 and 8102 refused; port 60128 was open.
