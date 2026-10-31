# Caps and late-night state belong to one receiver

Caps, the late-night cap and the late-night restore volume are raw volumes (ADR-0009), so they only mean something for the receiver they were set on. Raw 100 is −32 dB on the VSX-935 but 100 % on Home Assistant. Each of them records the driver key plus the receiver ID (or a hash of the connection config when there is no ID). When the user switches to a different receiver, the caps reset to that driver's scale defaults and late-night mode turns off without restoring a volume. Caps without receiver identity bind to the currently selected receiver. Caps are not remembered per receiver. Switching from A to B and back to A gives A its scale defaults again, and re-saving the same receiver's settings keeps its caps and late-night state.

We considered converting caps between scales instead. We rejected it because there is no conversion between dB and percent, and a wrong guess means an unexpectedly loud receiver.

This extends ADR-0012, which binds game profiles to a driver for the same reason.

When no receiver ID is available, the fallback hash uses only the driver's declared identity keys; learned transport metadata is excluded.
