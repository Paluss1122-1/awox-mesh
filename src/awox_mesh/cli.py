"""Command line client for awox-mesh."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8087"


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _print_devices(devices: list[dict]) -> None:
    for d in sorted(devices, key=lambda x: x["mesh_id"]):
        tail = d.get("mac_tail")
        product = d.get("product")
        extra = "  ".join(part for part in (
            f"mac …{tail}" if tail else "",
            f"product {product}" if product else "",
        ) if part)
        print(f"lamp 0x{d['mesh_id']:04X}  {'ON ' if d['state'] else 'OFF'}  {extra}".rstrip())
    ids = ", ".join(f"0x{d['mesh_id']:04X}" for d in devices)
    print(f"\nFound {len(devices)} lamp(s). Set AWOX_MESH_ID to the one you want: {ids}")
    if len(devices) > 1:
        print("Several lamps share this mesh; tell them apart by the MAC tail above.")


class _Client:
    def __init__(self, base: str, api_key: str | None):
        self.base = base.rstrip("/")
        self.api_key = api_key

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("X-API-Key", self.api_key)
        with urllib.request.urlopen(req, timeout=70) as resp:
            return json.load(resp)

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self._request("POST", path, body)


def _run_http(client: _Client, args) -> int:
    if args.command == "health":
        print(json.dumps(client.get("/health"), indent=2))
        return 0
    if args.command == "discover":
        devices = client.get("/discover").get("devices") or []
        if not devices:
            return _fail("no lamp answered the broadcast status query")
        _print_devices(devices)
        return 0
    if args.command == "state":
        data = client.get("/state")
        mark = "" if data.get("fresh") else "  (unconfirmed cache)"
        for d in data.get("devices") or []:
            print(f"lamp 0x{d['mesh_id']:04X}: {'ON' if d['state'] else 'OFF'}{mark}")
        if not data.get("devices"):
            return _fail("no status from the lamp")
        return 0
    if args.command == "brightness":
        print(f"brightness {client.post('/brightness', {'percent': args.percent})['percent']}%")
        return 0
    if args.command == "color":
        client.post("/color", {"red": args.red, "green": args.green, "blue": args.blue,
                               "brightness": args.brightness})
        print("colour set")
        return 0
    if args.command == "white":
        body = {"temperature": args.temperature, "brightness": args.brightness}
        client.post("/white", {k: v for k, v in body.items() if v is not None})
        print("white set")
        return 0
    if args.command == "lightshow":
        if args.action == "status":
            data = client.get("/lightshow")
            if data.get("running"):
                print(f"light show running: {data.get('step') or '...'}")
            elif data.get("error"):
                print(f"light show ended: {data['error']}")
            else:
                print("light show done." if data.get("done") else "no light show active")
        elif args.action == "stop":
            data = client.post("/lightshow/stop")
            print("light show stopped." if data.get("was_running")
                  else "no running light show")
        else:
            client.post("/lightshow")
            print("light show started.")
        return 0

    data = client.post(f"/{args.command}")
    suffix = f" (was {'on' if data.get('was') else 'off'})" \
        if args.command == "toggle" else ""
    print(f"lamp 0x{data.get('target', 0):04X}: "
          f"{'ON' if data['state'] else 'OFF'}{suffix}")
    return 0


async def _direct_discover() -> int:
    from .config import Config
    from .connection import discover

    try:
        config = Config.from_env()
    except Exception as err:
        return _fail(str(err))
    print(f"connecting to {config.mac} … the lamp advertises rarely, this can take a minute",
          file=sys.stderr)
    try:
        devices = await discover(config)
    except Exception as err:
        return _fail(f"{type(err).__name__}: {err}")
    if not devices:
        return _fail("no lamp answered the broadcast status query -- close the vendor "
                     "app and any other instance (bridge/service) holding the lamp; "
                     "if the chip is stuck, power-cycle at the wall switch")
    _print_devices(devices)
    return 0


async def _run_scan(timeout: float) -> int:
    from .scan import AWOX_OUI, scan

    print(f"scanning {timeout:.0f}s for AwoX lamps (OUI {AWOX_OUI}) …", file=sys.stderr)
    try:
        lamps = await scan(timeout)
    except Exception as err:
        return _fail(f"{type(err).__name__}: {err}")
    if not lamps:
        return _fail("no AwoX lamp advertised -- these lamps advertise only every "
                     "~25 s, so try a longer --timeout, make sure the lamp has power, "
                     "and close the vendor app")
    for lamp in lamps:
        print(f"{lamp['address']}  {(lamp['name'] or '(no name)'):14s}  RSSI {lamp['rssi']} dBm")
    print(f"\nFound {len(lamps)} lamp(s). Put the MAC you want into AWOX_MAC.")
    return 0


async def _run_direct(args) -> int:
    from . import protocol as ap
    from .config import MESH_ID_HELP, Config, ConfigError
    from .connection import create

    if args.command not in ("on", "off", "toggle", "state", "brightness", "lightshow"):
        return _fail(f"{args.command} needs the bridge -- drop --direct, "
                     "or use curl against the REST API")

    mesh = None
    try:
        config = Config.from_env()
        needs_id = args.command in ("on", "off", "toggle", "brightness", "lightshow")
        if needs_id and config.mesh_id is None:
            return _fail(MESH_ID_HELP)
        mesh = create(config)

        if args.command == "state":
            devices = await mesh.query_state()
            if not devices:
                return _fail("no status from the lamp")
            _print_devices(devices)
            return 0
        if args.command == "brightness":
            await mesh.send(lambda k, m: ap.set_brightness(k, m, args.percent))
            print(f"brightness {args.percent}%")
            return 0
        if args.command == "lightshow":
            if args.action != "start":
                return _fail("light show status and stop only work through the bridge; "
                             "in --direct mode the show runs in the foreground, so "
                             "just wait for it or press Ctrl+C")
            from .lightshow import run_lightshow

            async def send(builder):
                await mesh.send(lambda k, _m: builder(k, config.mesh_id))

            await run_lightshow(send, on_step=print)
            print("light show done.")
            return 0
        devices = await mesh.query_state()
        current = next((d["state"] for d in devices
                        if d["mesh_id"] == config.mesh_id), None)
        if current is None:
            return _fail("no fresh status from the lamp (state unknown -- not sent)")
        new_state = {"on": True, "off": False}.get(args.command, not current)
        await mesh.send(lambda k, m: ap.set_power(k, m, new_state))
        suffix = f" (was {'on' if current else 'off'})" if args.command == "toggle" else ""
        print(f"lamp 0x{config.mesh_id:04X}: {'ON' if new_state else 'OFF'}{suffix}")
        return 0
    except ConfigError as err:
        return _fail(str(err))
    except ConnectionError as err:
        return _fail(str(err) or "no mesh connection")
    finally:
        if mesh is not None:
            await mesh.stop()


def _program_name() -> str:
    name = os.path.basename(sys.argv[0] or "")
    if not name or name == "cli.py" or name.endswith(".py"):
        return "awox-mesh"
    return name


def build_parser() -> argparse.ArgumentParser:
    # Same options on the subparsers with SUPPRESS defaults, so they work in
    # either position without the subparser clobbering the top-level value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=argparse.SUPPRESS,
                        help="bridge base URL")
    common.add_argument("--api-key", default=argparse.SUPPRESS,
                        help="X-API-Key for the bridge")
    common.add_argument("--direct", action="store_true", default=argparse.SUPPRESS,
                        help="talk to the lamp directly over BLE instead of the bridge")

    p = argparse.ArgumentParser(prog=_program_name(),
                                description="Control AwoX/EGLO mesh lamps.")
    p.add_argument("--url", default=DEFAULT_URL, help="bridge base URL")
    p.add_argument("--api-key", help="X-API-Key for the bridge")
    p.add_argument("--direct", action="store_true",
                   help="talk to the lamp directly over BLE instead of the bridge")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", parents=[common],
                   help="read the lamp's real mesh id (first run, no bridge needed)")

    sc = sub.add_parser("scan", parents=[common],
                        help="list AwoX lamps in range with their MAC (for AWOX_MAC)")
    sc.add_argument("--timeout", type=float, default=30.0,
                    help="seconds to scan (default 30; lamps advertise rarely)")

    for name, help_text in (("on", "turn on"), ("off", "turn off"),
                            ("toggle", "toggle"), ("state", "show state")):
        sub.add_parser(name, parents=[common], help=help_text)

    b = sub.add_parser("brightness", parents=[common], help="set brightness")
    b.add_argument("percent", type=int, choices=range(0, 101))

    c = sub.add_parser("color", parents=[common], help="set RGB colour")
    c.add_argument("--red", type=int, required=True)
    c.add_argument("--green", type=int, required=True)
    c.add_argument("--blue", type=int, required=True)
    c.add_argument("--brightness", type=int, choices=range(0, 101))

    w = sub.add_parser("white", parents=[common],
                       help="set white temperature and/or brightness")
    w.add_argument("--temperature", type=int, choices=range(0, 101))
    w.add_argument("--brightness", type=int, choices=range(0, 101))

    sub.add_parser("health", parents=[common], help="bridge/connection status")

    ls = sub.add_parser("lightshow", parents=[common], help="control the light show")
    ls.add_argument("action", nargs="?", default="start",
                    choices=("start", "stop", "status"))

    return p


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "scan":
        return asyncio.run(_run_scan(args.timeout))

    from .config import load_default_env_files

    for path in load_default_env_files():
        print(f"config: {path}", file=sys.stderr)

    if args.command == "white" and args.temperature is None \
            and args.brightness is None:
        return _fail("white needs --temperature and/or --brightness")

    if args.command == "discover":
        if args.direct:
            return asyncio.run(_direct_discover())
        try:
            return _run_http(_Client(args.url, args.api_key), args)
        except urllib.error.HTTPError as err:
            detail = err.reason
            try:
                detail = json.loads(err.read().decode("utf-8", "replace")).get("detail", detail)
            except Exception:
                pass
            return _fail(f"bridge error ({err.code}): {detail}")
        except urllib.error.URLError:
            print("bridge not reachable -- reading the mesh id directly over BLE",
                  file=sys.stderr)
            return asyncio.run(_direct_discover())

    if args.direct:
        return asyncio.run(_run_direct(args))
    try:
        return _run_http(_Client(args.url, args.api_key), args)
    except urllib.error.HTTPError as err:
        detail = err.reason
        try:
            detail = json.loads(err.read().decode("utf-8", "replace")).get("detail", detail)
        except Exception:
            pass
        return _fail(f"bridge error ({err.code}): {detail}")
    except urllib.error.URLError as err:
        hint = "start the bridge (awox-mesh-bridge)"
        if args.command in ("on", "off", "toggle", "brightness", "state"):
            hint += f", or run: awox --direct {args.command}"
        return _fail(f"bridge unreachable at {args.url}: {err.reason} ({hint})")
    except TimeoutError:
        return _fail("bridge did not answer in time")


if __name__ == "__main__":
    raise SystemExit(main())
