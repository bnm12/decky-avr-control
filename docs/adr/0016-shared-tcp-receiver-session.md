# One shared TCP session per receiver, with framing left in the drivers

TCP drivers for eISCP, Denon, legacy Pioneer and HEOS need serialized transactions per receiver and a way to deliver unsolicited frames. `drivers/receiver_session.py` provides that shared transaction channel: a driver submits writes plus a reply classifier and the reply keys it needs. The session serialises transactions per receiver, handles timeouts, cancellation and reconnect-once, and routes every frame that isn't a reply to subscribers. Once a key is collected, later frames classified to that key are forwarded as unsolicited state rather than discarded. Optional replies are bounded by a grace period and cannot invalidate required replies already collected if the stream closes. Framing (eISCP packets, CR lines, HEOS JSON lines) and protocol rules (Denon's 50 ms spacing, Pioneer's port fallback, eISCP's optional-reply grace) stay in each driver.

Connections close after each transaction by default. A session stays connected only while something subscribes to it, because a persistent connection can hold a single-client receiver's only slot and lock out the vendor's app.

## Considered options

- **A receiver actor** owning connection, state cache and a shared command vocabulary (`execute(Command)`, `changes()`). Rejected because it moves the seam above the protocols and forces every protocol into one command set despite their protocol-specific state and framing.
- **One abstraction over TCP and HTTP.** Rejected: Yamaha, Sony, UPnP and Home Assistant are request/response HTTP and gain nothing from it.
