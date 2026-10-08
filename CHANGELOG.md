# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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