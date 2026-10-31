# Discover the receiver by eISCP UDP broadcast and re-find it by ID

The Pioneer and Onkyo apps find receivers with a UDP broadcast to port 60128. The plugin does the same: it sends the `!xECNQSTN` (Onkyo) and `!pECNQSTN` (Pioneer) probes to 255.255.255.255 and the subnet's `.255` address, resending about once a second for 2.5 s. A reply such as `!1ECNVSX-935/60128/XX/0009B0617D30` gives the model, port and unique ID. The reply was confirmed from Windows on the LAN.

The receiver's ID is saved next to its IP. When connecting fails, the plugin runs discovery again, at most every 30 s, and moves to the new IP if the ID matches. Entering an IP by hand deletes the saved ID, which turns this off, so a typed address is never replaced automatically.

Discovery binds source port 60128 and falls back to an ephemeral port if the bind fails, which it did on Windows.

## Consequences

Discovery cannot be tested from WSL2. The broadcast sent from WSL returned nothing, while the same probe from Windows got a reply. Broadcasts don't cross subnets or VLANs, so those setups need a manual IP.
