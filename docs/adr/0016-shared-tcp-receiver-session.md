# One shared TCP session per receiver, with framing left in the drivers

eISCP, Denon, legacy Pioneer and HEOS each used to own their own socket handling: open, read, timeouts, matching replies and converting errors. None of them coordinated concurrent calls, and pushed frames had nowhere to go. They now share `drivers/receiver_session.py`, a transaction channel: a driver submits writes plus a reply classifier and the reply keys it needs. The session serialises transactions per receiver, handles timeouts, cancellation and reconnect-once, and routes every frame that isn't a reply to subscribers. Framing (eISCP packets, CR lines, HEOS JSON lines) and protocol rules (Denon's 50 ms spacing, Pioneer's port fallback, eISCP's optional-reply grace) stay in each driver.

Connections still close after each transaction by default. A session stays connected only while something subscribes to it (push sync, deepening option 5), because a persistent connection can hold a single-client receiver's only slot and lock out the vendor's app.

## Considered options

- **A receiver actor** owning connection, state cache and a shared command vocabulary (`execute(Command)`, `changes()`). Rejected for now: it moves the seam above the protocols and forces every protocol into one command set before any driver has proven useful state pushes.
- **One abstraction over TCP and HTTP.** Rejected: Yamaha, Sony, UPnP and Home Assistant are request/response HTTP and gain nothing from it.
