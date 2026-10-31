# Late-night schedule precedence: manual toggles and game modes win

The opt-in late-night schedule turns late night mode on at the window's start and off at its end. Three rules decide conflicts:

1. A manual toggle wins until the next window boundary. Switching late night off inside the window keeps it off, and switching it on outside keeps it on. The schedule only undoes what the schedule did.
2. A game's remembered listening mode still applies when the game launches inside the window. The game opt-in is more specific than a daily schedule. The game's remembered volume is still clamped by the late-night cap, and late-night volumes never write into profiles (ADR-0004).
3. Each boundary fires at most once. If the receiver is unreachable or in standby at the boundary, the backend retries on later ticks until the window ends.

The alternative, letting the schedule re-assert itself on every tick, would fight the user, who just changed it deliberately from the couch.
