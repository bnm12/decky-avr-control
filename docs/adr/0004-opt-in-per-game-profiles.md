# Per-game profiles are opt-in, with separate volume and mode toggles

Games are never touched unless the user opts them in. The "This game" section has two independent toggles, Remember volume and Remember listening mode, so a game can keep only its mode, only its volume, or both. Turning both off deletes the profile. Saved values are applied at launch, and changes made while the game runs (slider, remote or app) are written back to its profile.

The mode is applied again about 4 seconds after launch, because AVRs sometimes switch mode once the game's audio starts. Volume-only profiles skip this step.

Late-night mode never writes into profiles. While it is on, a profile saves the pre-late-night level, not the lowered one, so turning it off restores the normal volume.

## Considered options

Remembering every game automatically was rejected in favor of per-game opt-in. Separate volume and mode toggles let users remember either setting without coupling it to the other.
