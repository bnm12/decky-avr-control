# Protocol specs live next to their driver, in one template, with source tags

Every driver has a protocol spec at `py_modules/avr_control/drivers/<key>.md` in the layout of `PROTOCOL_TEMPLATE.md`. Most drivers are written from public documents and community libraries, with no hardware to test on, so each fact carries a source tag: **[measured]**, **[spec]**, **[community]** or **[assumed]**. A contributor with the hardware can then see exactly which claims need checking.

The specs sit beside the code, not in `docs/`, so a change to a driver and its spec lands in the same diff. They ship inside the plugin zip, which is harmless.
