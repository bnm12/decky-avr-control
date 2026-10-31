# AVR Control

Decky Loader plugin that controls a network AV receiver from the Quick Access menu:
an absolute volume slider, mute, power and listening mode, instead of CEC volume
up/down. Anything else with a volume can be controlled through Home Assistant.

## Supported receivers

| Receivers | Driver | Status |
|---|---|---|
| Onkyo, Pioneer (2016+), Integra (eISCP) | [eiscp](py_modules/avr_control/drivers/eiscp.md) | Verified on a Pioneer VSX-935 |
| Yamaha MusicCast (YXC) | [yamaha](py_modules/avr_control/drivers/yamaha.md) | Experimental: built from Yamaha's spec, not yet tested on hardware |
| Denon, Marantz | [denon](py_modules/avr_control/drivers/denon.md) | Experimental: built from Denon's spec, not yet tested on hardware |
| Pioneer before 2016 (telnet) | [pioneer_telnet](py_modules/avr_control/drivers/pioneer_telnet.md) | Experimental |
| Any Home Assistant media player | [homeassistant](py_modules/avr_control/drivers/homeassistant.md) | Experimental |

Each driver links to its protocol spec. Own one of the experimental receivers? A
two-minute recording from your receiver makes it verified: see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Features

- **Volume slider, mute and power**, in dB (or percent where the receiver has no dB
  scale), following changes made with the remote or the receiver's app (the open
  panel refreshes every 2 seconds). Controls a receiver doesn't support are hidden.
- **Per-game volume and listening mode, opt-in.** While a game is running, switch on
  *Remember volume* and/or *Remember listening mode* for it in the plugin. Each one is
  independent, so a game can remember just its mode, just its volume, or both. What a
  game remembers is restored every time it launches, and updated whenever you change it
  (with the slider, the remote or the app) while it runs. Games you haven't opted
  in are never touched. Saved profiles are listed in the plugin, where you can forget them.
- **Late night mode.** One toggle lowers the volume to the *Late night max volume* and
  caps the slider there. Switching it off restores the previous volume and cap. Per-game
  profiles are not changed while it is on.

- **Listening mode picker.** Choose a listening mode from the panel. Yamaha and Home
  Assistant report their own list; the eISCP list was collected from a VSX-935 with
  `tools/collect_modes.py` and may be incomplete. Codes that aren't in a list still work
  (eISCP shows them as `Mode XX`), and per-game profiles remember the raw code. Some modes
  are only available for certain inputs or signals; if the receiver refuses one the
  plugin says so.

## Install

1. Download [avr-control.zip](https://github.com/bnm12/decky-avr-control/releases/latest/download/avr-control.zip)
   from the latest GitHub release (or from the artifact of a workflow run).
2. Unzip it into `~/homebrew/plugins/` so you end up with
   `~/homebrew/plugins/avr-control/` (needs sudo).
3. `sudo systemctl restart plugin_loader`
4. Open the Quick Access menu. On first run the plugin searches the network for
   the receiver by itself; otherwise tap **Search network**, or enter the IP
   manually and tap Save & connect. The connection type is detected from the IP;
   for Home Assistant choose it explicitly and enter the URL, a long-lived access
   token and the media player's entity id.

Enable the receiver's network standby setting (Pioneer/Onkyo "Network Standby",
Denon/Marantz "Network Control: Always On", Yamaha "Network Standby") so it accepts
commands while idle.

When upgrading from **Pioneer Volume**, settings migrate automatically on first
launch. After confirming the new plugin works, remove the old
`~/homebrew/plugins/pioneer-volume/` folder to avoid loading both plugins.

## Build locally

```
corepack pnpm install --frozen-lockfile
corepack pnpm build
python -m unittest discover -s tests -t . -v
```

## Releasing

The version lives in `package.json` and is always the same as the git tag.
In the repo's **Actions** tab choose **Release**, then **Run workflow**, and enter a
version such as `0.2.2` (or `gh workflow run release.yml -f version=0.2.2`). It updates
`package.json`, commits, tags `v0.2.2`, and the Build workflow then attaches
`avr-control.zip` to a GitHub release. Pushing a `v*` tag by hand also works: the
zip is stamped with the tag's version, and the build warns if `package.json` differs.

## Discovery

**Search network** runs every driver's discovery at once: the eISCP UDP broadcast
on port 60128 (the same probe the Pioneer/Onkyo apps send) and an SSDP search for
Yamaha and Denon/Marantz receivers. The receiver's unique ID is remembered, so if
its IP address changes the plugin looks it up again automatically. Typing an IP by
hand disables this. Broadcasts only work within one subnet, so a VLAN or guest
network needs the IP entered manually.

The slider cap is set in the receiver's own unit (dB or percent) in the plugin's
settings, so the slider can never go above it.
## Collecting listening-mode codes

`tools/collect_modes.py` helps build a table of your receiver's listening-mode codes.
It only reads the mode, it never changes it:

```
python3 tools/collect_modes.py 192.168.1.229
```

Change the mode with the remote or the Pioneer app; each new code asks for the name
the receiver displays. Results are saved to `listening_modes.json`.

## Privacy

The plugin only talks to your receiver on your local network: UDP broadcasts on port
60128 and SSDP multicast to find it, then connections to the receiver's own IP (or to
the Home Assistant URL you enter). Nothing else is sent anywhere and there is no
telemetry. A Home Assistant token is stored in the settings file and never shown in
the panel again. Settings (receiver address, volume caps, per-game
profiles) are stored locally in Decky's settings folder.

## License

MIT, see `LICENSE`.
