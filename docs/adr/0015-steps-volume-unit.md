# Add a steps volume display unit

Sony receivers and some Onkyo-style models expose a volume number without a trustworthy dB mapping or percentage label. The shared scale previously forced drivers to mislabel these raw values as `%` or reject valid ranges.

Add `"steps"` as a third `VolumeScale.unit`. The UI shows the receiver's number as-is, with no suffix (for example, `Volume 35`); absolute eISCP scales default to steps, and `%` is opt-in only when a receiver actually displays percent. We reject mislabelling steps as percent and inventing a dB mapping.
