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

BLE on Linux also needs the usual BlueZ stack. On Debian/Ubuntu:

```bash
sudo apt install bluez
```

`gatttool` and `bluetoothctl` ship **inside** the `bluez` package — there is no
separate `gatttool` package, and `sudo apt install bluez gatttool` fails with
"Unable to locate package gatttool". No extra group membership or sudo is needed
to use BLE afterwards: the shipped D-Bus policy lets any local user talk to
`org.bluez`.

## Configuration

Copy the example and fill in what you know — the file is never committed:

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
| `AWOX_MESH_ID` | Real mesh address of the lamp, e.g. `0x1234`. Can stay empty for `discover` |
| `AWOX_BACKEND` | `auto` (default), `gatt` or `bleak` |
| `AWOX_API_KEY` | If set, requests must send `X-API-Key` |
| `AWOX_HOST` / `AWOX_PORT` | Bind address of the bridge (default `0.0.0.0:8087`) |
| `AWOX_ADV_TIMEOUT` | Seconds to wait for an advertisement (default `600`) |

### Mesh credentials — pick the right set

Mesh credentials are **not** the Wi-Fi or app password. Get them with the
credential tool from
[fsaris](https://fsaris.github.io/EspHome-AwoX-BLE-mesh-hub/awoxh-mesh-credentials-tool/):

1. On that page, use the **"Using AwoX HomeControl"** login form, **not**
   "Using AwoX Smart CONTROL". The two accounts return different meshes.
2. Sign in with your AwoX/EGLO account. If login fails, try your username in
   full lower-case.
3. The tool returns **one set per service**, for example:

   ```json
   {"mesh_name":"XXXXXXXX","mesh_password":"........","service":"zigbee"}
   {"mesh_name":"YYYYYYYY","mesh_password":"........","service":"mesh"}
   ```

4. Take the set whose **`mesh_name` equals the BLE name of your lamp** from a
   scan. **Ignore the tool's own hint** ("When multiple services are shown look
   for `service: "mesh"`") — on the tested lamp the correct set was the
   `zigbee` one, so that hint is misleading. Wrong sets are rejected with `0x0e`.

### Find the mesh id (first run)

`AWOX_MESH_ID` is the lamp's real mesh address. It cannot be derived from the MAC
or the name, and it has no safe default — control commands go to this exact
address, never to `0` and never to broadcast. You **cannot know it in advance**,
so leave it empty and read it off the hardware:

```bash
awox discover
```

This connects, sends a broadcast status query and prints every lamp that answers,
each with its mesh id and its MAC tail (so you can tell several lamps apart). Put
the id of the lamp you want into `config.env`:

```
AWOX_MESH_ID=0x1234
```

`discover` uses the bridge when one is running, and otherwise reads the id
directly over BLE — so it works before any service is set up. `/state` also
works without a mesh id; only control needs it.

> **One connection at a time.** These lamps accept a single BLE connection and
> stop advertising while it is open. Before `discover` or any direct test, close
> the vendor app **and** stop any other instance holding the lamp — including a
> running bridge or systemd service (`systemctl --user stop awox-mesh`). Two
> clients just reconnect in a loop and can wedge the chip.

## HTTP bridge

```bash
awox-mesh-bridge            # reads config.env from the environment
```

```bash
curl -s     localhost:8087/discover
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
awox discover           # first run: read the lamp's mesh id (no bridge needed)
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

Both `awox` and `awox-mesh` are installed as the same command. The common
options (`--url`, `--api-key`, `--direct`) work before or after the subcommand.

## Python API

```python
import asyncio
from awox_mesh import protocol as ap
from awox_mesh.config import Config
from awox_mesh.connection import create

async def main():
    config = Config.from_env(require_mesh_id=True)
    mesh = create(config)                       # gatt or bleak, per AWOX_BACKEND
    asyncio.create_task(mesh.run())             # keeps the link open in the background; send() connects on demand

    await mesh.send(lambda key, mid: ap.set_power(key, mid, True))
    await mesh.send(lambda key, mid: ap.set_brightness(key, mid, 40))
    await mesh.send(lambda key, mid: ap.set_color_rgb(key, mid, 255, 0, 0))

    print(await mesh.query_state())
    await mesh.stop()

asyncio.run(main())
```

Don't know the mesh id yet? Leave `AWOX_MESH_ID` empty and use
`from awox_mesh.connection import discover` to read it from the lamp first.

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
- Other models may differ. The mesh address must be discovered per lamp with
  `awox discover`; there is no broadcast fallback for control commands.
- The chip accepts only **one** connection at a time and stops advertising while
  one is open. Close the vendor app, and stop any other instance (bridge, a
  systemd service, a second checkout) before connecting — otherwise both clients
  reconnect in a loop and can wedge the chip. A wedged chip usually needs a
  power-cycle at the wall switch (off 10 s).
- Several lamps can share one mesh (same credentials, same name). `discover`
  lists them all with their MAC tails; choose one `AWOX_MESH_ID`.
- `/state` and schedule reads require the `gatt` backend. The credential tool's
  own "look for `service: mesh`" hint is not reliable — pick the set whose
  `mesh_name` matches your lamp's BLE name.
- Reverse engineered for interoperability. No vendor code, APK or binary is
  included; see [`docs/protocol.md`](docs/protocol.md) for provenance.

## Licence

MIT — see [LICENSE](LICENSE).