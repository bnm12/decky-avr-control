# Backend imports are limited to the modules in Decky's frozen Python

Decky Loader runs plugin backends inside its own PyInstaller-frozen Python, not the system Python. That build bundles only the standard-library modules Decky itself imports. A missing module, even one from the standard library, crashes the backend on load on the device while every test on a developer machine passes. `xml.etree` is the case that hit us: it isn't bundled, so the backend never started, and the panel waited forever for its first reply. `xml.parsers.expat` is bundled, so XML parsing goes through our own `avr_control/xmltree.py`.

"Stdlib only" (ADR-0008) is therefore necessary but not sufficient. The rule is: import only modules listed in `tests/decky_runtime_modules.txt`. That list is generated from a PluginLoader release by `scripts/decky_bundled_modules.py`, and `tests/test_decky_runtime.py` checks every import in `main.py` and `py_modules/`, including imports inside functions. When Decky releases a new loader, regenerate the list. A module that disappears from it breaks the plugin.

`tools/` scripts run on the user's own Python, so this rule doesn't apply to them.
