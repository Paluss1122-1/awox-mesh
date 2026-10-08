# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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