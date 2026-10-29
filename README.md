# Pioneer Volume

Decky Loader plugin that adds an absolute volume slider (plus mute) for Pioneer
AVRs (tested target: VSX-935) over the network, instead of CEC volume up/down.

## Install

1. Download `pioneer-volume.zip` from the latest GitHub release (or from the
   artifact of a workflow run).
2. Unzip it into `~/homebrew/plugins/` so you end up with
   `~/homebrew/plugins/pioneer-volume/` (needs sudo).
3. `sudo systemctl restart plugin_loader`
4. Open the Quick Access menu. On first run the plugin searches the network for
   the receiver by itself; otherwise tap **Search network**, or enter the IP
   manually and tap Save & connect.

Enable **Network Standby** on the AVR so it accepts commands while idle.

## Build locally

```
pnpm install
pnpm build
```

## Release

```
git tag v0.1.0 && git push --tags
```

The workflow builds the plugin and attaches `pioneer-volume.zip` to a GitHub release.

## Protocol notes

The plugin tries, in order, whichever protocol your AVR answers:

1. **eISCP on port 60128** (Onkyo-style, used by recent Pioneer VSX models).
   Volume is `MVLxx` (hex), mute is `AMT00`/`AMT01`. 0.5 dB per step; on a
   VSX-935 raw 48 = -58 dB and 50 = -57 dB, so 0 dB is raw 164.
2. **Pioneer text protocol on port 23 or 8102.** `###VL` sets volume
   (0-185, 161 = 0 dB, 0.5 dB steps), `MO`/`MF` mute/unmute. The slider shows dB.

The slider cap is set in dB in the plugin's settings (default about -32 dB for
eISCP, 0 dB for the text protocol), so the slider can never go above it.

## Discovery

Receivers are found the same way the Pioneer/Onkyo apps do it: a UDP broadcast
on port 60128 (the eISCP `ECNQSTN` probe, sent in both the Onkyo and Pioneer
flavours). The receiver's unique ID is remembered, so if its IP address changes
the plugin looks it up again automatically. Typing an IP by hand disables this.
Broadcasts only work within one subnet, so a VLAN or guest network needs the IP
entered manually.
