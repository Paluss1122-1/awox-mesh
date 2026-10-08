# awox-mesh

Control **AwoX / EGLO Connect** BLE mesh lamps from Linux without the cloud, without
Home Assistant and without a Zigbee stick — using the Bluetooth adapter your machine
already has.

This library speaks a **non-Telink command protocol** that is not described anywhere
else. Every widely used open implementation
(`python-awox-mesh-light`, `EspHome-AwoX-BLE-mesh-hub`, `home-assistant-awox`)
assumes Telink CCM framing with a nonce and a packet counter. Recent AwoX/EGLO
firmware — for example model `EBF_RGB_Zm`, firmware `3.0.1_1593` — does something
else entirely: a single AES-ECB block per packet. Commands written for the classic
schema connect fine and then silently do nothing, which is why this firmware often
looks "unsupported".

The full derivation is in [`docs/protocol.md`](docs/protocol.md). The
[hardware quirks](docs/hardware.md) that make these lamps awkward to drive are in
[`docs/hardware.md`](docs/hardware.md).

## What you get

- `awox_mesh.protocol` — pure packet arithmetic and cryptography, no Bluetooth
  needed, fully unit-tested.
- `awox_mesh.ble` — portable control via [bleak](https://github.com/hbldh/bleak).
- `awox_mesh.gatt` — `gatttool` backend; the only way to receive status and
  schedule notifications.
- `awox_mesh.bridge` — a small FastAPI service, so you can drive the lamp from
  Home Assistant, scripts or anything that speaks HTTP.
- `awox` — a command line client.

Features: on/off, brightness, RGB colour, white temperature, built-in colour
sequences, candle flicker, state readback, and a **dawn alarm that runs inside the
lamp** — no server, no phone, no Bluetooth link needed once it is set.

## Install

```bash
git clone <your-fork-url> awox-mesh
cd awox-mesh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

BLE on Linux also needs the usual BlueZ stack (`bluetoothd`, `bluez`). On
Debian/Ubuntu: `sudo apt install bluez gatttool`.

## Configuration

Copy the example and fill in the credentials — the file is never committed:

```bash
cp config.env.example config.env
chmod 600 config.env
$EDITOR config.env
set -a && source config.env && set +a
```

| Variable | Meaning |
|---|---|
| `AWOX_MAC` | MAC of the lamp (Telink OUI `A4:C1:38:...`) |
| `AWOX_MESH_NAME` / `AWOX_MESH_PASSWORD` | Mesh credentials, see below |
| `AWOX_MESH_ID` | **Required.** Real mesh address of the lamp, e.g. `0x1234` |
| `AWOX_BACKEND` | `auto` (default), `gatt` or `bleak` |
| `AWOX_API_KEY` | If set, requests must send `X-API-Key` |
| `AWOX_HOST` / `AWOX_PORT` | Bind address of the bridge (default `0.0.0.0:8087`) |
| `AWOX_ADV_TIMEOUT` | Seconds to wait for an advertisement (default `600`) |

Mesh credentials are **not** the Wi-Fi or app password. Obtain them with the
credential tool from
[fsaris](https://fsaris.github.io/EspHome-AwoX-BLE-mesh-hub/awoxh-mesh-credentials-tool/),
which reads them from your AwoX/EGLO Connect account. The correct `mesh_name` is
the one whose BLE name matches your lamp in a scan — the `service` field
(`zigbee` / `mesh`) is not a reliable hint. Wrong credentials are rejected with
`0x0e`, which is unambiguous.

`AWOX_MESH_ID` has no sensible default: commands go to the lamp's real mesh
address, never to `0` and never to the broadcast address. It appears in the first
two bytes (little endian) of every status block.

## HTTP bridge

```bash
awox-mesh-bridge            # reads config.env from the environment
```

```bash
curl -X POST localhost:8087/on
curl -X POST localhost:8087/off
curl -X POST localhost:8087/toggle
curl -X POST localhost:8087/brightness -H 'content-type: application/json' -d '{"percent":25}'
curl -X POST localhost:8087/color      -H 'content-type: application/json' -d '{"red":255,"green":0,"blue":0}'
curl -X POST localhost:8087/white      -H 'content-type: application/json' -d '{"temperature":0,"brightness":100}'
curl -X POST localhost:8087/sequence -H 'content-type: application/json' -d '{"sequence":3}'
curl -X POST localhost:8087/sequence/stop
curl -X POST localhost:8087/candle
curl -s     localhost:8087/state
curl -s     localhost:8087/health
```

Interactive API docs are served at `/docs`.

**Dawn alarm — runs in the lamp itself:**

```bash
curl -X POST localhost:8087/dawn -H 'content-type: application/json' \
  -d '{"hour":7,"minute":30,"fade_minutes":45,"brightness":90,"temperature":0}'
curl -s localhost:8087/dawn
```

From 07:30 the lamp fades up over 45 minutes to 90 % — with the bridge switched
off. `weekdays` is a 7-bit mask (`0x7F` = daily), `enabled: false` disables the
alarm without deleting it. The bridge sets the lamp's clock on every connect.

**Colour sequences also continue in the lamp** once started, so continuous
Bluetooth traffic is not needed — much gentler on the chip than streaming colours
per second.

### The honesty rule

`/on`, `/off` and `/toggle` **read the real state first**. If no fresh status
arrives they answer `503` and **send nothing** rather than reporting a state they
did not verify. `/state` marks cached values with `"fresh": false`, and
`/state/last` is a passive snapshot that costs no BLE traffic. A `503` means
"unknown", not "failed to switch".

## CLI

```bash
awox state
awox on && awox off
awox toggle
awox brightness 40
awox color --red 255 --green 0 --blue 0 --brightness 80
awox white --temperature 0 --brightness 100
awox health
awox lightshow          # one show through every function
awox --direct state     # talk to the lamp directly, without the bridge
```

## Python API

```python
import asyncio
from awox_mesh import protocol as ap
from awox_mesh.config import Config
from awox_mesh.connection import create

async def main():
    config = Config.from_env()
    mesh = create(config)                       # gatt or bleak, per AWOX_BACKEND
    asyncio.create_task(mesh.run())             # keep the link open
    await mesh.wait_connected()

    await mesh.send(lambda key, mid: ap.set_power(key, mid, True))
    await mesh.send(lambda key, mid: ap.set_brightness(key, mid, 40))
    await mesh.send(lambda key, mid: ap.set_color_rgb(key, mid, 255, 0, 0))

    print(await mesh.query_state())
    await mesh.stop()

asyncio.run(main())
```

## Protocol at a glance

```
pairing  ->  0x0c | session_random(8) | AES(session_random, name XOR password)[0:8]
         <-  0x0d | response_random(8)            (0x0e = credentials rejected)

session_key = AES(name XOR password, session_random + response_random)

command  =  [flag byte, plaintext] + AES-ECB(session_key, first 16 bytes) [+ tail]
block    =  [CRC8][len][dest_hi][dest_lo][0x01][opcode_hi][opcode_lo][data...]  padded to 16
            len  = len(data) + 5
            CRC8 = reverse direction, polynomial 0x6C, initial value 0
```

One AES block, no nonce, no counter: the same command produces the same bytes
within a session. Details, opcodes and the schedule frames are in
[`docs/protocol.md`](docs/protocol.md).

## Run as a service

```ini
# ~/.config/systemd/user/awox-mesh.service
[Unit]
Description=AwoX/EGLO Mesh Bridge
After=bluetooth.target

[Service]
# The unit runs the interpreter explicitly: on an NTFS checkout the exec bit on
# config.env cannot survive, so do not rely on it.
ExecStart=/bin/bash -c 'set -a; . %h/Projects/awox-mesh/config.env; set +a; exec %h/Projects/awox-mesh/.venv/bin/awox-mesh-bridge'
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now awox-mesh
loginctl enable-linger "$USER"      # so it survives logout
```

Keep `config.env` at mode `600` — it holds credentials.

## Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
```

The tests need no hardware. The protocol tests check the crypto against an
independent AES-ECB reference, the CRC against a bitwise reference, and the
command and schedule layout field by field. The bridge tests run against a stub
connection and pin the honesty rule: no state is reported without a fresh
readback, and no command is sent when the state is unknown.

## Status and caveats

- Verified against model `EBF_RGB_Zm`, firmware `3.0.1_1593`, hardware `4.60`.
- Other models may differ. The mesh address must be discovered per lamp; there is
  no broadcast fallback for control commands.
- The chip accepts only **one** connection at a time and stops advertising while
  one is open — close the vendor app before using this.
- `/state` and schedule reads require the `gatt` backend.
- Reverse engineered for interoperability. No vendor code, APK or binary is
  included; see [`docs/protocol.md`](docs/protocol.md) for provenance.

## Licence

MIT — see [LICENSE](LICENSE).