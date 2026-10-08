"""Command line client for awox-mesh.

By default it talks to the HTTP bridge (the same API the REST docs describe).
With ``--direct`` it opens a BLE connection itself and bypasses the bridge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8087"


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


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
        print("colour set")
        client.post("/color", {"red": args.red, "green": args.green, "blue": args.blue,
                               "brightness": args.brightness})
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


async def _run_direct(args) -> int:
    from . import protocol as ap
    from .config import Config
    from .connection import create

    if args.command not in ("on", "off", "toggle", "state", "brightness", "lightshow"):
        return _fail(f"{args.command} needs the bridge -- drop --direct, "
                     "or use curl against the REST API")

    config = Config.from_env()
    mesh = create(config)
    try:
        if args.command == "state":
            devices = await mesh.query_state()
            if not devices:
                return _fail("no status from the lamp")
            for d in devices:
                print(f"lamp 0x{d['mesh_id']:04X}: {'ON' if d['state'] else 'OFF'}")
            return 0
        if args.command == "brightness":
            await mesh.send(lambda k, m: ap.set_brightness(k, m, args.percent))
            print(f"brightness {args.percent}%")
            return 0
        if args.command == "lightshow":
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
    except ConnectionError as err:
        return _fail(str(err) or "no mesh connection")
    finally:
        await mesh.stop()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="awox", description="Control AwoX/EGLO mesh lamps.")
    p.add_argument("--url", default=DEFAULT_URL, help="bridge base URL")
    p.add_argument("--api-key", help="X-API-Key for the bridge")
    p.add_argument("--direct", action="store_true",
                   help="talk to the lamp directly over BLE instead of the bridge")
    sub = p.add_subparsers(dest="command", required=True)

    for name, help_text in (("on", "turn on"), ("off", "turn off"),
                            ("toggle", "toggle"), ("state", "show state")):
        sub.add_parser(name, help=help_text)

    b = sub.add_parser("brightness", help="set brightness")
    b.add_argument("percent", type=int, choices=range(0, 101))

    c = sub.add_parser("color", help="set RGB colour")
    c.add_argument("--red", type=int, required=True)
    c.add_argument("--green", type=int, required=True)
    c.add_argument("--blue", type=int, required=True)
    c.add_argument("--brightness", type=int, choices=range(0, 101))

    w = sub.add_parser("white", help="set white temperature and/or brightness")
    w.add_argument("--temperature", type=int, choices=range(0, 101))
    w.add_argument("--brightness", type=int, choices=range(0, 101))

    sub.add_parser("health", help="bridge/connection status")

    ls = sub.add_parser("lightshow", help="control the light show")
    ls.add_argument("action", nargs="?", default="start",
                    choices=("start", "stop", "status"))

    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "white" and args.temperature is None \
            and args.brightness is None:
        return _fail("white needs --temperature and/or --brightness")
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
        return _fail(f"bridge unreachable at {args.url}: {err.reason} "
                     "(start it or use --direct)")
    except TimeoutError:
        return _fail("bridge did not answer in time")


if __name__ == "__main__":
    raise SystemExit(main())