# eISCP first, legacy Pioneer text protocol as fallback

The VSX-935 answers eISCP on port 60128 but refuses the classic Pioneer text protocol on ports 23 and 8102. The plugin therefore tries eISCP first and uses the text protocol only when a receiver answers on either legacy port.

The eISCP scale was measured on the unit. `MVL30` (raw 48) displayed -58 dB, and `MVL32` (raw 50) displayed -57 dB. That gives 0.5 dB per step, with 0 dB at raw 164 (48 + 116). The 1 dB-per-step alternative was ruled out by the second reading.

## Considered options

The text protocol alone: rejected, because the VSX-935 doesn't answer it.

## Consequences

The 0 dB point is extrapolated from two readings, so the dB label may be off by about 1 dB at louder settings. The text-protocol fallback has not been tested on a real VSX-935.
