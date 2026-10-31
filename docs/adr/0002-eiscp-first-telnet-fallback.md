# eISCP first, legacy Pioneer text protocol as fallback

The first version targeted the classic Pioneer text protocol on port 23 or 8102 (`###VL`, range 0-185). On the VSX-935 both ports were refused (`nc -vz`: "Connection refused"), and an nmap scan showed 60128 open. The receiver answers eISCP on 60128, so the plugin tries eISCP first and uses the text protocol only when the receiver answers on 23 or 8102.

The eISCP scale was measured on the unit. `MVL30` (raw 48) displayed -58 dB, and `MVL32` (raw 50) displayed -57 dB. That gives 0.5 dB per step, with 0 dB at raw 164 (48 + 116). The 1 dB-per-step alternative was ruled out by the second reading.

## Considered options

The text protocol alone: rejected, because the VSX-935 doesn't answer it.

## Consequences

The 0 dB point is extrapolated from two readings, so the dB label may be off by about 1 dB at louder settings. The text-protocol fallback has not been tested on a real VSX-935.
