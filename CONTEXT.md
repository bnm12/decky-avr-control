# AVR Control

A Decky Loader plugin that controls a network AV receiver (or anything with a volume, through Home Assistant) from the Steam Quick Access panel: absolute volume, mute, power, listening mode, plus per-game and late-night behaviour.

## Language

### Receivers and drivers

**Receiver**:
The device being controlled, usually an AV receiver (AVR), reached over the LAN.
_Avoid_: AVR (fine in prose, not in code), amp, device

**Driver**:
The adapter that speaks one control protocol to a receiver (eISCP, Denon telnet, Yamaha YXC, Home Assistant).
_Avoid_: backend, integration, plugin

**Capability**:
One of volume, mute, power or listening mode that a driver can offer. The UI shows only what the driver offers.

**Protocol spec**:
The Markdown file next to a driver recording everything known about its protocol, with each fact tagged by source.

**Source tag**:
How a protocol fact is known: measured, spec, community or assumed.

### Finding a receiver

**Discovery**:
Searching the LAN for receivers without user input.
_Avoid_: scan, search (fine for the UI button label)

**Probe**:
Asking one address whether a receiver speaking a given driver's protocol answers there. Used when the user types an IP.

**Receiver ID**:
A receiver's stable unique identifier (MAC, serial, UDN), used to re-find it after its IP address changes.

**Rediscovery**:
Running discovery to re-find a known receiver by its receiver ID after it stopped answering at its saved address.

### Volume

**Raw volume**:
A volume value in the receiver's own integer units.
_Avoid_: level, step

**Volume scale**:
A driver's mapping from raw volume to what the user sees (dB or percent).

**Cap**:
The highest raw volume the slider allows, set by the user in display units.
_Avoid_: max volume, limit

**Late night mode**:
A temporary state that lowers the volume to the late night cap and restores the previous volume and cap when switched off.

### Listening modes

**Listening mode**:
The receiver's sound-processing mode (Stereo, Direct, Dolby Surround, ...), identified by a driver-specific code.
_Avoid_: surround mode, sound program (Yamaha's term), LMD (eISCP's command)

### Games

**Game profile**:
What a game remembers (its volume, its listening mode, or both), restored when it launches.

**Opt-in**:
A per-game, per-setting switch the user turns on so a game remembers that setting. Games without an opt-in are never touched.
