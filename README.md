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

This repository is self-contained: it ships its own HTTP bridge
(`awox-mesh-bridge`) and does not need the vendor cloud or any other service.

## Quickstart

```bash
git clone https://github.com/Paluss1122-1/awox-mesh.git
cd awox-mesh
python3 -m venv .venv && . .venv/bin/activate && pip install -e .

cp config.env.example config.env && chmod 600 config.env
$EDITOR config.env              # fill AWOX_MAC, AWOX_MESH_NAME, AWOX_MESH_PASSWORD

awox scan --timeout 120        # list AwoX lamps (MAC + name); put the MAC in config.env
awox discover                  # read the real mesh id straight from the lamp
$EDITOR config.env             # set AWOX_MESH_ID=0x....

awox-mesh-bridge               # terminal 1: start the HTTP bridge (docs at /docs)
awox state                     # terminal 2: talk to the bridge
```

The CLI and the bridge load `./config.env` automatically, so you never have to
`export` anything. Env vars still win if you prefer them.

> `awox` needs the venv. In a **new terminal** it is gone again
> (`awox: command not found`) until you `source .venv/bin/activate` — or link it
> once into `~/.local/bin` (see [Make the CLI available everywhere](#make-the-cli-available-everywhere)).

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
git clone https://github.com/Paluss1122-1/awox-mesh.git
cd awox-mesh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

Python 3.10 or newer is required. BLE on Linux also needs the usual BlueZ stack.
On Debian/Ubuntu:

```bash
sudo apt install bluez
```

`gatttool` and `bluetoothctl` ship **inside** the `bluez` package — there is no
separate `gatttool` package, and `sudo apt install bluez gatttool` fails with
"Unable to locate package gatttool". No extra group membership or sudo is needed
to use BLE afterwards: the shipped D-Bus policy lets any local user talk to
`org.bluez`.

## Configuration

Copy the example and fill in what you know — the file is never committed. It is
loaded automatically from the working directory (and from
`~/.config/awox-mesh/config.env`), so `export` is not needed:

```bash
cp config.env.example config.env
chmod 600 config.env
$EDITOR config.env
```

| Variable | Meaning |
|---|---|
| `AWOX_MAC` | MAC of the lamp (Telink OUI `A4:C1:38:...`) |
| `AWOX_MESH_NAME` / `AWOX_MESH_PASSWORD` | Mesh credentials, see below |
| `AWOX_MESH_ID` | Real mesh address of the lamp, e.g. `0x1234`. Can stay empty for `discover` |
| `AWOX_BACKEND` | `auto` (default), `gatt` or `bleak` |
| `AWOX_API_KEY` | If set, requests must send `X-API-Key` |
| `AWOX_HOST` / `AWOX_PORT` | Bind address of the bridge (default `127.0.0.1:8087`) |
| `AWOX_ADV_TIMEOUT` | Seconds to wait for an advertisement (default `600`) |
| `AWOX_CONNECT_TIMEOUT` | Seconds for one connect attempt (default `45`) |
| `AWOX_LOG_LEVEL` | Log verbosity (default `INFO`) |
| `AWOX_NOTIFICATIONS` | `bleak` only: subscribe to status notifications (default off) |
| `AWOX_ADAPTER` | Force a Bluetooth adapter, e.g. `hci1` (default: auto-detect) |
| `AWOX_ADAPTER_RECALL_CMD` | Optional command to re-enumerate the adapter after repeated connect failures |

A real environment variable always wins over the file; set `AWOX_NO_ENV_FILE=1`
to ignore the files entirely. The bridge also accepts `--host`, `--port`,
`--env-file` and `--log-level` as flags, which override the file.

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

### Several lamps share one mesh

All lamps of one mesh use the same `mesh_name` **and** the same BLE name — so the
name does **not** identify a single lamp, and a MAC from an earlier session may
actually belong to a lamp that is currently switched off. `awox scan` lists the
lamps that are actually advertising right now (MAC + name + RSSI); `awox discover`
then reports which of them answers and its mesh id. Pick one lamp per
`AWOX_MESH_ID`; to address another lamp, pass `?target=0x....` to the bridge.

## Two ways to talk to the lamp

| | talks to | needs | features |
|---|---|---|---|
| `awox …` (default) | the running bridge on `127.0.0.1:8087` | `awox-mesh-bridge` running | everything, incl. multi-lamp `?target=` |
| `awox --direct …` | the lamp over BLE | nothing else | single lamp, requires `AWOX_MESH_ID` |

`awox discover` is special: it uses the bridge if one is running and otherwise
falls back to direct BLE automatically, so first-run setup needs no server. The
other commands stay in their mode — if you see `bridge unreachable`, either start
`awox-mesh-bridge` or add `--direct`. The error message tells you both.

## HTTP bridge

The bridge is this repository's own FastAPI service. It does **not** need any
other AwoX service. Interactive OpenAPI docs are at `/docs`.

```bash
awox-mesh-bridge                        # uses ./config.env, binds 127.0.0.1:8087
awox-mesh-bridge --help                 # --host / --port / --env-file
awox-mesh-bridge --host 0.0.0.0 --port 8087   # expose to the LAN (set AWOX_API_KEY!)
```

By default it binds to **`127.0.0.1`** and is only reachable locally. If another
host (e.g. Home Assistant) must reach it, set `AWOX_HOST=0.0.0.0` **and**
`AWOX_API_KEY=<secret>`.

> **Port 8087 is also used by the unrelated legacy `awox_endpoint` project.** If
> that old service is still installed, only one of the two can own the port —
> stop the other one, or pick a different `AWOX_PORT`.

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
curl -X POST localhost:8087/time
curl -X POST localhost:8087/lightshow          # and /lightshow/stop
curl -s     localhost:8087/state
curl -s     localhost:8087/state/last          # passive cache, no BLE traffic
curl -s     localhost:8087/health
```

`?target=0x....` (or decimal) works on every control endpoint. `POST /time` sets
the lamp's clock by hand; normally both backends do that on connect.

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

### State handling

`/on`, `/off` and `/toggle` read the real state first. If no fresh status
arrives they answer `503` and send nothing, rather than report a state they did
not verify. `/state` marks cached values with `"fresh": false`, and `/state/last`
is a passive snapshot that costs no BLE traffic. A `503` means "unknown", not
"failed to switch".

## CLI

```bash
awox scan --timeout 120 # list AwoX lamps (MAC + name) for config.env
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

### Make the CLI available everywhere

Inside the venv the commands exist; in a new shell they do not. Either activate
the venv again, or link the scripts once into your PATH:

```bash
ln -s "$PWD/.venv/bin/awox" "$HOME/.local/bin/awox"
ln -s "$PWD/.venv/bin/awox-mesh-bridge" "$HOME/.local/bin/awox-mesh-bridge"
```

(A `pipx install -e .` or `uv tool install --editable .` gives you the same
result without the manual links.)

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

A ready-to-edit unit ships in [`contrib/awox-mesh.service`](contrib/awox-mesh.service):

```bash
mkdir -p ~/.config/systemd/user
cp contrib/awox-mesh.service ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/awox-mesh.service   # set WorkingDirectory AND ExecStart to your clone
systemctl --user enable --now awox-mesh
systemctl --user status awox-mesh
loginctl enable-linger "$USER"                      # so it survives logout
```

The unit ships with paths for a clone at `~/awox-mesh`. If yours lives elsewhere,
change **both** `WorkingDirectory` and `ExecStart`. It runs
`awox-mesh-bridge --env-file <clone>/config.env`, so it does not depend on the
shell environment. Keep `config.env` at mode `600` — it holds credentials.

## Home Assistant

The bridge is a plain REST service; you do not need a custom integration. As long
as HA can reach the host (see `AWOX_HOST` above), `rest_command` is enough:

```yaml
rest_command:
  awox_on:
    url: "http://127.0.0.1:8087/on"
  awox_off:
    url: "http://127.0.0.1:8087/off"
  awox_scene_blue:
    url: "http://127.0.0.1:8087/color"
    method: POST
    content_type: "application/json"
    payload: '{"red":0,"green":0,"blue":255}'
```

With `AWOX_API_KEY` set, add `headers: {X-API-Key: "<secret>"}` to each command.
A proper HA integration on top of `/state` is the natural next step and is not in
this repository yet.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `bridge unreachable` | the bridge is not running: start `awox-mesh-bridge`, or use `awox --direct …` |
| `AWOX_MESH_ID is not set` | expected before first `discover`; set it afterwards |
| `connection failed` | vendor app or another instance holds the lamp; power-cycle the wall switch |
| `503` on `/on`, `/off`, `/toggle` | no fresh status arrived, so nothing was sent. Retry; check the link |
| `/state` returns `"fresh": false` | the lamp is not answering; the value is the last cache |
| pairing reply `0x0e` | wrong credential set or the lamp's alternating pairing mode |
| `discovery needs the gatt backend` | `gatttool` is missing; install `bluez` or set `AWOX_BACKEND=gatt` |
| lamp answers nothing at all | chip stuck; power-cycle the wall switch (off 10 s) |

## Security

- The bridge binds to `127.0.0.1` and is unprotected until you set `AWOX_API_KEY`
  and, only if needed, `AWOX_HOST=0.0.0.0`. Do not expose it to the internet.
- `config.env` holds the mesh credentials; keep it at mode `600` and out of git.
- BLE traffic is the vendor's own plaintext-ish scheme; treat the mesh password as
  a shared secret for anyone in radio range.

## Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
```

The tests need no hardware. The protocol tests check the crypto against an
independent AES-ECB reference, the CRC against a bitwise reference, and the
command and schedule layout field by field. The bridge tests run against a stub
connection and check that no state is reported without a fresh readback and that
no command is sent when the state is unknown.

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