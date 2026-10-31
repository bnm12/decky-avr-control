# Rename to "AVR Control" before the store submission

With Denon, Yamaha and Home Assistant drivers, "Pioneer Volume" no longer described the plugin. We renamed it to **AVR Control** (folder and zip `avr-control`, repo `bnm12/decky-avr-control`) before submitting to the Decky store, because after publishing a rename changes the plugin's identity and install folder for every user.

## Consequences

Decky stores settings under `settings/<plugin folder>`, so the new folder starts empty. On first load the backend adopts and migrates `settings/pioneer-volume/settings.json`, and existing users should delete the old `pioneer-volume` plugin folder. The old GitHub URL redirects, but install URLs now point at `avr-control.zip`.
