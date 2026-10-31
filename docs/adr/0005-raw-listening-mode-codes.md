# Keep raw listening-mode codes, with a name table collected from hardware

Profiles and backend calls always use the receiver's raw eISCP mode code (for example `00` or `FF`), never a name. The name table in `main.py` only affects display. A code missing from the table shows as `Mode XX` and still works, so nothing breaks when the table is incomplete.

The table holds 19 entries collected from a VSX-935 with `tools/collect_modes.py`. That script only reads the mode (`!1LMDQSTN`) and asks for the name shown on the front display for each new code. The codes line up with the listening-mode slots in the 2009 Onkyo ISCP specification.

A complete vendor table for the VSX-935 could not be verified, so the picker may be missing modes.

## Considered options

A fixed list with unknown codes hidden: rejected, because a mode missing from the list could not be selected or remembered.
