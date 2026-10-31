# Caps and late-night state belong to one receiver

Caps, the late-night cap and the late-night restore volume are raw volumes (ADR-0009), so they only mean something for the receiver they were set on. Raw 100 is −32 dB on the VSX-935 but 100 % on Home Assistant. Each of them records the driver key plus the receiver ID (or a hash of the connection config when there is no ID). When the user switches to a different receiver, the caps reset to that driver's scale defaults and late-night mode turns off without restoring a volume. Values saved before this rule bind to whichever receiver is current at the time.

We considered converting caps between scales instead. We rejected it because there is no conversion between dB and percent, and a wrong guess means an unexpectedly loud receiver.

This extends ADR-0012, which binds game profiles to a driver for the same reason.
