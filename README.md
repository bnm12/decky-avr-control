# Pioneer Volume

Decky Loader plugin that adds an absolute volume slider (plus mute) for Pioneer
AVRs (tested target: VSX-935) over the network, instead of CEC volume up/down.

## Features

- **Volume slider and mute**, in dB, following changes made with the remote or the
  Pioneer app (the open panel refreshes every 2 seconds).
- **Per-game volume and listening mode, opt-in.** While a game is running, switch on
  *Remember volume* and/or *Remember listening mode* for it in the plugin. Each one is
  independent, so a game can remember just its mode, just its volume, or both. What a
  game remembers is restored every time it launches, and updated whenever you change it
  (with the slider, the remote or the app) while it runs. Games you haven't opted
  in are never touched. Saved profiles are listed in the plugin, where you can forget them.
- **Late night mode.** One toggle lowers the volume to the *Late night max volume* and
  caps the slider there. Switching it off restores the previous volume and cap. Per-game
  profiles are not changed while it is on.

- **Listening mode picker.** Choose a listening mode from the panel (eISCP receivers that
  report one). The list was collected from a VSX-935 with `tools/collect_modes.py` and
  may be incomplete. Codes that aren't in the list still work: they show as `Mode XX`, and
  per-game profiles remember the raw code, so nothing depends on the list being complete.
  Some modes are only available for certain inputs or signals; if the receiver refuses one
  the plugin says so.

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

## Releasing

The version lives in `package.json` and is always the same as the git tag.
In the repo's **Actions** tab choose **Release**, then **Run workflow**, and enter a
version such as `0.2.2` (or `gh workflow run release.yml -f version=0.2.2`). It updates
`package.json`, commits, tags `v0.2.2`, and the Build workflow then attaches
`pioneer-volume.zip` to a GitHub release. Pushing a `v*` tag by hand also works: the
zip is stamped with the tag's version, and the build warns if `package.json` differs.

## Discovery

Receivers are found the same way the Pioneer/Onkyo apps do it: a UDP broadcast
on port 60128 (the eISCP `ECNQSTN` probe, sent in both the Onkyo and Pioneer
flavours). The receiver's unique ID is remembered, so if its IP address changes
the plugin looks it up again automatically. Typing an IP by hand disables this.
Broadcasts only work within one subnet, so a VLAN or guest network needs the IP
entered manually.

## Protocol notes

The plugin tries, in order, whichever protocol your AVR answers:

1. **eISCP on port 60128** (Onkyo-style, used by recent Pioneer VSX models).
   Volume is `MVLxx` (hex), mute is `AMT00`/`AMT01`. 0.5 dB per step; on a
   VSX-935 raw 48 = -58 dB and 50 = -57 dB, so 0 dB is raw 164.
2. **Pioneer text protocol on port 23 or 8102.** `###VL` sets volume
   (0-185, 161 = 0 dB, 0.5 dB steps), `MO`/`MF` mute/unmute. The slider shows dB.

The slider cap is set in dB in the plugin's settings (default about -32 dB for
eISCP, 0 dB for the text protocol), so the slider can never go above it.

## Collecting listening-mode codes

`tools/collect_modes.py` helps build a table of your receiver's listening-mode codes.
It only reads the mode, it never changes it:

```
python3 tools/collect_modes.py 192.168.1.229
```

Change the mode with the remote or the Pioneer app; each new code asks for the name
the receiver displays. Results are saved to `listening_modes.json`.

## Privacy

The plugin only talks to your receiver on your local network: a UDP broadcast on port
60128 to find it, then TCP connections to the receiver's own IP. Nothing is sent to the
internet and there is no telemetry. Settings (receiver address, volume caps, per-game
profiles) are stored locally in Decky's settings folder.

## License

MIT, see `LICENSE`.
