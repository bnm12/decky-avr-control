# Keep raw listening-mode codes, with a name table collected from hardware

Profiles and backend calls always use the receiver's raw eISCP mode code (for example `00` or `FF`), never a name. The name table in `drivers/eiscp.py` supplies display labels. A code missing from the table shows as `Mode XX` and still works, so nothing breaks when the table is incomplete.

The eISCP name table is based on the VSX-935 and aligns with listening-mode slots in the 2009 Onkyo ISCP specification.

A complete vendor table for the VSX-935 could not be verified, so the picker may be missing modes.

## Considered options

A fixed list with unknown codes hidden: rejected, because a mode missing from the list could not be selected or remembered.
