# AVR Control

Decky Loader plugin for controlling supported network receivers from the Steam
Quick Access menu. Home Assistant can connect media-player entities that expose
compatible controls.

## Supported receivers

| Receiver / driver | Volume | Mute | Power | Listening modes | Discovery | Status |
|---|---:|---:|---:|---:|---|---|
| Onkyo, Pioneer (2016+), Integra ([eISCP](py_modules/avr_control/drivers/eiscp.md)) | ✓ | ✓ | ✓ | ✓ | UDP broadcast | VSX-935 tested; not every feature verified |
| Yamaha MusicCast ([YXC](py_modules/avr_control/drivers/yamaha.md)) | ✓* | ✓* | ✓* | ✓* | SSDP | Experimental |
| Yamaha before MusicCast, 2010–2016 ([XML](py_modules/avr_control/drivers/yamaha_ync.md)) | ✓ | ✓ | ✓ | ✓ | SSDP | Experimental |
| Denon, Marantz ([telnet / HTTP](py_modules/avr_control/drivers/denon.md)) | ✓ | ✓ | ✓ | ✓ | SSDP | Experimental |
| Denon HEOS speakers and soundbars ([HEOS](py_modules/avr_control/drivers/heos.md)) | ✓ | ✓ | — | — | SSDP | Experimental |
| Sony receivers and soundbars ([Audio Control API](py_modules/avr_control/drivers/sony.md)) | ✓* | ✓* | ✓* | ✓* | SSDP | Experimental |
| Pioneer before 2016 ([telnet](py_modules/avr_control/drivers/pioneer_telnet.md)) | ✓ | ✓ | ✓ | — | Manual IP | Experimental |
| Sonos and other UPnP/DLNA renderers ([UPnP](py_modules/avr_control/drivers/upnp.md)) | ✓ | ✓ | — | — | SSDP | Experimental |
| Home Assistant media-player entity ([Home Assistant](py_modules/avr_control/drivers/homeassistant.md)) | Entity-dependent | Entity-dependent | Entity-dependent | Entity-dependent | Manual setup | Experimental |

`—` means unsupported. `*` capabilities depend on the receiver's advertised
features. Home Assistant capabilities depend on the selected entity. Only eISCP
has been tested on real hardware (a Pioneer VSX-935); the other drivers are
simulator-tested and remain experimental.

Each driver links to its protocol spec. Own one of the experimental receivers? A
two-minute recording from your receiver helps us validate it: see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Features

- **Volume slider and mute**, plus power where supported. Volume uses the
  driver's dB, percent, or step scale. The open panel refreshes status every
  2 seconds; unsupported controls are hidden.
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
version such as `X.Y.Z` (or `gh workflow run release.yml -f version=X.Y.Z`). It updates
`package.json`, commits, tags `vX.Y.Z`, and the Build workflow then attaches
`avr-control.zip` to a GitHub release. Use this workflow for releases; a manually
pushed tag can leave the store's `package.json` version stale.

## Discovery

**Search network** runs every driver's discovery at once: the eISCP UDP broadcast
on port 60128 (the same probe the Pioneer/Onkyo apps send) and SSDP searches for
Yamaha, Denon/Marantz, HEOS, Sony and UPnP/Sonos devices. A receiver that answers
several protocols is listed once, under its brand's driver. The receiver's unique ID is remembered, so if
its IP address changes the plugin looks it up again automatically. Typing an IP by
hand disables this. Broadcasts only work within one subnet, so a VLAN or guest
network needs the IP entered manually.

The slider cap is set in the driver's displayed unit (dB, percent, or steps) in
the plugin's settings, so the slider can never go above it.

## Troubleshooting & diagnostics

- Enable the receiver's network-standby setting to control it while idle.
- eISCP and many Denon receivers allow only one reliable control client. Close
  the vendor app or other controller while testing; Denon can fall back to HTTP
  when its telnet interface is busy or unavailable.
- Discovery broadcasts and SSDP multicast may not cross VLANs or guest-network
  boundaries. Put the Deck and receiver on a reachable network, or enter the
  receiver IP manually.
- Decky plugin logs are normally in
  `~/homebrew/logs/avr-control/`; loader service logs are available with
  `journalctl -u plugin_loader`.
- To report a receiver, run `python tools/avr_probe.py capture <receiver-ip>`,
  review the redacted capture, and attach it to a
  [Receiver report](https://github.com/bnm12/decky-avr-control/issues/new?template=receiver-report.yml).
  Never share Home Assistant tokens or credentials.
## Collecting listening-mode codes

`tools/collect_modes.py` helps build a table of your receiver's listening-mode codes.
It only reads the mode, it never changes it:

```
python3 tools/collect_modes.py 192.168.1.229
```

Change the mode with the remote or the Pioneer app; each new code asks for the name
the receiver displays. Results are saved to `listening_modes.json`.

## Privacy

The plugin contacts your receiver on the local network and the Home Assistant URL
you enter; that URL may be outside your LAN. There is no telemetry or other
built-in service. A Home Assistant token is stored in Decky's settings file and
is not shown in the panel again. Receiver settings, volume caps and per-game
profiles are stored locally in Decky's settings folder.

## License

MIT, see `LICENSE`.
