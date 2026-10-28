# Pioneer Volume

Decky Loader plugin that adds an absolute volume slider (plus mute) for Pioneer
AVRs (tested target: VSX-935) over the network, instead of CEC volume up/down.

## Install

1. Download `pioneer-volume.zip` from the latest GitHub release (or from the
   artifact of a workflow run).
2. Unzip it into `~/homebrew/plugins/` so you end up with
   `~/homebrew/plugins/pioneer-volume/` (needs sudo).
3. `sudo systemctl restart plugin_loader`
4. Open the Quick Access menu, enter the AVR's IP, and tap Save & connect.

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

Pioneer's telnet-style control on port 23 (falls back to 8102). `###VL` sets
volume (0-185, 161 = 0 dB, 0.5 dB steps), `?V` queries it, `MO`/`MF` mute/unmute.
The slider is capped at 161 (0 dB); change `MAX_VOL` in `main.py` to alter that.
