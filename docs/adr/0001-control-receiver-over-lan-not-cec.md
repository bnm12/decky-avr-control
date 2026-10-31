# Control the receiver over the LAN, not HDMI-CEC

The Steam Machine's volume buttons send HDMI-CEC volume up/down presses, and the AVR treats each press as one step. CEC has only relative up, down and mute commands. It has no way to set an absolute level such as "40", so a slider cannot be built on it.

The plugin controls the receiver over the network instead. The Pioneer network control interface accepts absolute volume commands (see 0002 for the protocol). Google Home gets its slider through the receiver's Chromecast service (Cast; the nmap scan showed ports 8008 and 8009 open). Using that route would need the `pychromecast` library, which was judged too heavy when the AVR's own control port (60128) answered.

## Consequences

The receiver must have Network Standby enabled, or it ignores commands while idle. The plugin needs the receiver's IP address on the same LAN as the Steam Machine.
