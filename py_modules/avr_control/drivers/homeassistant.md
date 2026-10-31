# Home Assistant media player REST API (`homeassistant`)

**Source tags:** **[measured]** seen on real hardware · **[spec]** from official Home Assistant documentation · **[community]** open-source library or forum · **[assumed]** unverified implementation detail.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/homeassistant.py` |
| Brands / models | [spec] Any brand represented by a Home Assistant `media_player` entity |
| Verified on | none (simulator only) |
| Transport | [spec] Home Assistant REST API over HTTP or HTTPS; JSON; frontend port (8123 by default) |
| Discovery | [assumed] None. mDNS discovery of Home Assistant servers may be added later; the access token and entity still require user input |
| Connections | [assumed] Independent HTTP requests; no persistent session is managed |
| State updates | [assumed] Polling only; the REST driver does not subscribe to entity state events. |

## Transport and framing

[spec] Requests use the Home Assistant REST API under `/api/`, with JSON bodies and responses. Every request sends `Authorization: Bearer <long-lived access token>` and `Content-Type: application/json`. `GET /api/` returns `{"message": "API running."}`. The API uses the same port as the frontend, 8123 by default.

[spec] Status reads use `GET /api/states/<entity_id>`. Setters call `POST /api/services/media_player/<service>` with an entity target and the service data. Requests are ordinary HTTP messages; there is no protocol-specific packet framing.

[assumed] The driver uses Python's default TLS certificate verification for HTTPS. Self-signed certificates are unsupported unless the certificate is trusted by the host. A service call may return before the entity state reflects the change, so the driver polls state for up to about 1.5 seconds and then returns the last observed value if it has not observed confirmation.

## Discovery

[assumed] Automatic discovery returns no receivers. Home Assistant servers may be discoverable via mDNS `_home-assistant._tcp`, but discovery cannot supply the required long-lived token or select the user's media-player entity.

[spec] Manual probing calls `GET /api/` and then reads the configured entity state. The resulting ID is `ha:<entity_id>`; it is stable as long as that entity ID remains unchanged.

## Setup options and onboarding

[spec] Create a long-lived access token in the Home Assistant user's profile under **Security → Long-lived access tokens**. Select **Home Assistant** as the connection type, enter the server URL (for example `http://homeassistant.local:8123`) and token, then choose **Find media players**.

[spec] Setup options call the read-only `GET /api/states` endpoint with the same bearer authentication, timeout, URL validation, redirect protection, and user-facing errors as other driver requests. Missing URL or token returns no options.

[assumed] The driver offers only `media_player.*` entities, sorted by friendly name, with the entity ID in each label. Entities without the `VOLUME_SET` feature are marked “no volume control”; this is an onboarding hint, not a filter. The user can choose a result or type an entity ID manually.

[spec] Home Assistant's advertised `supported_features` determine which controls AVR Control exposes after connection. An entity may have no volume capability even though it is a media player.

[assumed] The panel never fills the token back into the form. It is sent only to the Home Assistant URL the user entered; that URL may be outside the local network.

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | [spec] `GET /api/states/<entity_id>`, attribute `volume_level` | [spec] `POST /api/services/media_player/volume_set` with `volume_level` | [spec] Re-read entity state | [spec] `volume_level` is from 0 to 1; driver maps it to raw 0–100 percent |
| Mute | [spec] `GET /api/states/<entity_id>`, attribute `is_volume_muted` | [spec] `POST /api/services/media_player/volume_mute` with `is_volume_muted` | [spec] Re-read entity state | [spec] Supported when `VOLUME_MUTE` is advertised |
| Power | [spec] `GET /api/states/<entity_id>`, entity state | [spec] `POST /api/services/media_player/turn_on` or `turn_off` | [spec] Re-read entity state | [spec] `off` and `standby` map to off; `unknown` and `unavailable` are errors. Power capability is exposed when either `TURN_ON` or `TURN_OFF` is advertised; setters check the requested direction |
| Listening mode | [spec] `GET /api/states/<entity_id>`, attributes `sound_mode` and `sound_mode_list` | [spec] `POST /api/services/media_player/select_sound_mode` with `sound_mode` | [spec] Re-read entity state | [spec] Mode code and display name are the Home Assistant sound-mode string; known lists are checked before setting |

## Volume scale

[spec] Home Assistant reports `volume_level` as a float from 0 to 1. The driver converts it to a rounded integer percentage (raw 0–100), with 1 percentage point per step, zero at raw 0, and a default slider cap of raw 60. The driver sends raw volume divided by 100. Other integrations may expose different volume ranges or omit volume entirely.

## Listening modes

[spec] The driver fetches `sound_mode_list` from the entity and uses each string as both code and display name. The media-player entity documentation says this list is only available for entities supporting `SELECT_SOUND_MODE`; availability and names therefore depend on the integration and device.

When the entity supports `SELECT_SOUND_MODE` and `sound_mode_list` is present
as a list on its state, `ModeState.complete` is `true`; an absent field is
incomplete (`false`) [spec]. A current value not in the list remains visible
but is not added to selectable modes. Standby or an entity without
`SELECT_SOUND_MODE` returns no current or selectable modes [spec]. Labels are
the mode codes themselves and do not require a configured receiver [spec].

## Quirks and gotchas

- [spec] Home Assistant's media-player building block is provided by other integrations; not every integration exposes every capability. Supported feature flags govern which controls are available.
- [spec] Sound modes appear only when an entity advertises `SELECT_SOUND_MODE` and supplies `sound_mode_list`; no concrete AVR integration has been measured here.
- [spec] The Home Assistant user needs permission to read and control the selected entity.
- [assumed] Redirects to another scheme, host, or port (including HTTPS-to-HTTP downgrades) are rejected; enter the final URL directly.
- [assumed] HA state updates may lag service responses. The driver polls briefly; if state does not match by the deadline, setters return the last observed value, which may differ from the requested value.
- [assumed] HTTPS uses the standard Python trust store. Self-signed certificates must be trusted by the system.

## Unknowns and open questions

- [assumed] No real Home Assistant instance or receiver integration has been measured. Test volume rounding, power states, and mode availability against several AVR integrations.
- [assumed] Integrations may represent receiver standby using states other than `off` or `standby`; those are treated as powered on unless Home Assistant reports `unknown` or `unavailable`.
- [assumed] Confirm how HA permissions behave for long-lived tokens created by non-administrator users.
- [assumed, not yet measured] Tester checklist: change volume with the remote while the panel is open; does the slider follow within 1 s?

## Sources

1. [spec] [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/) — authentication, API health check, state endpoints, service calls, and permissions.
2. [spec] [Home Assistant media player entity](https://developers.home-assistant.io/docs/core/entity/media-player/) — entity attributes, volume range, supported features, and sound modes.
3. [spec] [Home Assistant media player integration](https://www.home-assistant.io/integrations/media_player/) — media-player states, actions, and integration behavior.
