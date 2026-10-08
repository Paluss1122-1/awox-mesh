# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-10-08

Second round of fixes from watching a clean install be guided entirely through a
chat assistant.

### Added

- `awox scan`: lists AwoX lamps currently advertising (MAC + name + RSSI) to fill
  `AWOX_MAC`. Needed because several lamps of one mesh share the same BLE name, so
  the name cannot identify a lamp and a remembered MAC can be stale.
- Config files are loaded automatically: `./config.env` and
  `~/.config/awox-mesh/config.env`. No more `export` dance; a real environment
  variable still wins, and `AWOX_NO_ENV_FILE=1` disables it.
- `awox-mesh-bridge` now has a CLI: `--help`, `--host`, `--port`, `--env-file`
  (and `--log-level`). Before, `--help` crashed with a config error.
- `contrib/awox-mesh.service`, a ready-to-edit systemd user unit.

### Changed

- The bridge binds to `127.0.0.1` by default instead of `0.0.0.0`. Exposing it to
  the LAN is now explicit (`AWOX_HOST=0.0.0.0`) and documented as needing
  `AWOX_API_KEY`.
- `awox state` and friends now name the exact fallback when the bridge is not
  running: `… or run: awox --direct state`.

### Documentation

- A Quickstart with the full happy path at the top.
- A "Two ways to talk to the lamp" section: bridge vs `--direct`, and that
  `discover` is the one command that falls back automatically.
- Explicitly states the repository is self-contained and needs no other AwoX
  service; notes the port-8087 clash with the unrelated legacy `awox_endpoint`.
- Multi-lamp section: same mesh name, pick by MAC.
- "Make the CLI available everywhere" (the venv trap).

## [1.1.0] - 2026-10-08

Fixes from a real first-run install on a clean user account.

### Added

- `awox discover` and `GET /discover`: read the lamp's real mesh address off the
  hardware via a broadcast status query. This closes a chicken-and-egg problem —
  `AWOX_MESH_ID` was required to start, but could only be learned from a lamp
  that needed the id to be reached. Discovery needs no bridge.
- `AWOX_MESH_ID` is now optional. The bridge and the CLI start without it;
  `/state` and `discover` work, and only control commands ask for it (with a
  message pointing at `discover`).
- `decode_status()` in the protocol layer, used by both backends. Status now
  also carries the product code and the reversed MAC tail, so several lamps of
  one mesh can be told apart.
- `awox` as an alias for the `awox-mesh` console script.

### Fixed

- The README told users to run `awox`, but only `awox-mesh` was installed — the
  command did not exist. Both names are installed now.
- `sudo apt install bluez gatttool` fails: there is no `gatttool` package, it
  ships inside `bluez`.
- `--url`/`--api-key`/`--direct` are accepted before **and** after the
  subcommand (the common options are now shared with every subparser).
- The Python API example called `mesh.wait_connected()`, which only exists on the
  bleak backend and would raise `AttributeError` on the gatt backend.
- The connect-failure message now lists the real causes, including another
  instance holding the lamp's single connection.

### Documentation

- Clear credential instructions: use the **"Using AwoX HomeControl"** login on
  the fsaris tool, not "AwoX Smart CONTROL"; pick the returned set whose
  `mesh_name` matches the lamp's BLE name; ignore the tool's own
  "look for service: mesh" hint (on the tested lamp the right set was `zigbee`).
- Prominent "one connection at a time" warning covering the phone app and any
  other running instance.

## [1.0.0] - 2026-10-08

First public release.

### Added

- `awox_mesh.protocol`: pure packet arithmetic and cryptography for the
  non-Telink AwoX/EGLO BLE mesh command format — pairing, power, brightness,
  RGB colour, white temperature, sequences, candle mode, clock and schedules
  (including the dawn alarm that runs inside the lamp). No Bluetooth required.
- `awox_mesh.ble`: asynchronous bleak backend with BlueZ Direct Connect, automatic
  recovery from the BR/EDR device-record trap, and reconnect handling.
- `awox_mesh.gatt`: gatttool backend — the only reliable way to receive status and
  schedule notifications, because the status characteristic has no CCCD.
- `awox_mesh.connection`: backend factory (`auto`, `gatt`, `bleak`) over a shared
  interface.
- `awox_mesh.bridge`: FastAPI service with power, brightness, colour, white,
  sequence, candle, state and dawn endpoints, optional `X-API-Key` protection,
  and interactive docs at `/docs`.
- `awox_mesh.lightshow`: one show through every known lamp function, pacing-safe
  at a 0.34 s minimum interval.
- `awox`: command line client, HTTP-first with a `--direct` BLE mode.
- Protocol and hardware documentation, including the full derivation and the
  dead ends.
- 39 unit tests: the protocol layer checked against independent AES-ECB and
  bitwise-CRC references with field-by-field layout assertions, plus bridge tests
  on a stub connection that pin the honesty rule.

### Notes

- `AWOX_MESH_ID` is required and has no default: control commands go to the
  lamp's real mesh address, never to `0` and never to broadcast.
- State is only ever reported when freshly read. `/on`, `/off` and `/toggle`
  answer `503` and send nothing rather than claim an unverified state.