"""FastAPI application exposing AwoX/EGLO mesh lamps over HTTP."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .. import __version__
from .. import protocol as ap
from ..config import Config
from ..connection import create
from ..lightshow import MIN_PAUSE, run_lightshow
from .models import BrightnessBody, ColorBody, DawnBody, SequenceBody, WhiteBody

logger = logging.getLogger("awox.bridge")

Target = Query(None, ge=0, le=0xFFFF,
               description="mesh id of the target; empty = the configured lamp")


def create_app(mesh, config) -> FastAPI:
    """Build the FastAPI app around an existing connection and config.

    ``mesh`` must expose ``send(build)``, ``query(build)``, ``query_state()``,
    ``is_connected``, ``states``, ``last_error`` and ``last_seen``.
    """
    mesh_id_default = config.mesh_id

    async def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if config.api_key and x_api_key != config.api_key:
            raise HTTPException(status_code=401,
                                detail="invalid or missing X-API-Key")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(mesh.run(), name="awox-mesh-connection")
        logger.info("bridge ready; mesh connection is being established in the background")
        yield
        task.cancel()
        await mesh.stop()

    app = FastAPI(
        title="AwoX/EGLO Mesh Bridge",
        description="Controls AwoX/EGLO lamps over the local Bluetooth adapter.",
        version=__version__,
        lifespan=lifespan,
        dependencies=[Depends(require_key)],
    )

    def _dest(target: int | None) -> int:
        if target is None:
            if mesh_id_default is None:
                raise HTTPException(status_code=503, detail=(
                    "AWOX_MESH_ID is not set, so there is no default target. "
                    "Call GET /discover to read your lamp's mesh id, set it in "
                    "config.env, or pass ?target=0x.... explicitly."))
            return mesh_id_default
        return target

    async def _run(build, target: int | None):
        try:
            await mesh.send(lambda key, mid: build(key, _dest(target)))
        except ConnectionError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

    def _require_link() -> None:
        if not mesh.is_connected:
            detail = ("no connection to the lamp (reconnect running) -- "
                      "state unknown, command not sent")
            if mesh.last_error:
                detail += f" [{mesh.last_error}]"
            raise HTTPException(status_code=503, detail=detail)

    async def _fresh_state(dest: int) -> dict:
        _require_link()
        try:
            devices = await mesh.query_state()
        except ConnectionError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err
        device = next((d for d in devices if d["mesh_id"] == dest), None)
        if device is None:
            raise HTTPException(
                status_code=503,
                detail=f"no fresh status from lamp 0x{dest:04X} "
                       "(state unknown -- command not sent)")
        return device

    @app.get("/health", summary="Connection status")
    async def health():
        return {
            "connected": mesh.is_connected,
            "mac": mesh.mac,
            "mesh_name": config.mesh_name,
            "mesh_id": (None if mesh_id_default is None
                        else f"0x{mesh_id_default:04X}"),
            "known_devices": sorted(mesh.states),
            "last_error": mesh.last_error,
        }

    async def _all_devices() -> list[dict]:
        try:
            devices = await mesh.query_state()
        except ConnectionError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err
        except Exception as err:
            raise HTTPException(status_code=503,
                                detail=f"{type(err).__name__}: {err}") from err
        return devices or [mesh.states[k] for k in sorted(mesh.states)]

    @app.get("/discover", summary="Read the real mesh id(s) from the mesh")
    async def discover():
        _require_link()
        devices = await _all_devices()
        return {"devices": devices, "fresh": bool(devices)}

    @app.get("/state", summary="Lamp state (freshly queried)")
    async def state():
        _require_link()
        devices = await _all_devices()
        return {"devices": devices, "fresh": bool(devices)}

    @app.get("/state/last", summary="Last observed state (passive, no BLE query)")
    async def state_last():
        target = mesh.states.get(mesh_id_default, {})
        return {
            "devices": [mesh.states[k] for k in sorted(mesh.states)],
            "state": target.get("state"),
            "mesh_id": mesh_id_default,
            "fresh": False,
            "connected": mesh.is_connected,
            "at": mesh.last_seen or None,
        }

    @app.post("/on", summary="Turn on")
    async def turn_on(target: int = Target):
        dest = _dest(target)
        await _fresh_state(dest)
        await _run(lambda key, d: ap.set_power(key, d, True), target)
        return {"ok": True, "target": dest, "state": True, "fresh": True}

    @app.post("/off", summary="Turn off")
    async def turn_off(target: int = Target):
        dest = _dest(target)
        await _fresh_state(dest)
        await _run(lambda key, d: ap.set_power(key, d, False), target)
        return {"ok": True, "target": dest, "state": False, "fresh": True}

    @app.post("/toggle", summary="Toggle -- reads the real state first")
    async def toggle(target: int = Target):
        dest = _dest(target)
        current = (await _fresh_state(dest))["state"]
        new_state = not current
        await _run(lambda key, d: ap.set_power(key, d, new_state), target)
        return {"ok": True, "target": dest, "state": new_state, "was": current,
                "fresh": True}

    @app.post("/brightness", summary="Brightness 0..100 %")
    async def set_brightness(body: BrightnessBody, target: int = Target):
        await _run(lambda key, dest: ap.set_brightness(key, dest, body.percent), target)
        return {"ok": True, "target": _dest(target), "percent": body.percent}

    @app.post("/color", summary="Set RGB colour")
    async def set_color(body: ColorBody, target: int = Target):
        await _run(lambda key, dest: ap.set_color_rgb(key, dest, body.red, body.green,
                                                      body.blue), target)
        if body.brightness is not None:
            await asyncio.sleep(0.35)
            await _run(lambda key, dest: ap.set_brightness(key, dest, body.brightness),
                       target)
        return {"ok": True, "target": _dest(target),
                **body.model_dump(exclude_none=True)}

    @app.post("/white", summary="Set white temperature and/or brightness")
    async def set_white(body: WhiteBody, target: int = Target):
        if body.temperature is None and body.brightness is None:
            raise HTTPException(status_code=422,
                                detail="temperature or brightness required")
        if body.temperature is not None:
            await _run(lambda key, dest: ap.set_white_temperature(key, dest,
                                                                  body.temperature),
                       target)
        if body.brightness is not None:
            await asyncio.sleep(0.35)
            await _run(lambda key, dest: ap.set_brightness(key, dest, body.brightness),
                       target)
        return {"ok": True, "target": _dest(target),
                **body.model_dump(exclude_none=True)}

    @app.post("/sequence", summary="Start a built-in colour sequence (runs in the lamp)")
    async def set_sequence(body: SequenceBody, target: int = Target):
        await _run(lambda key, dest: ap.set_sequence(key, dest, body.sequence,
                                                     body.duration_ms), target)
        return {"ok": True, "target": _dest(target), **body.model_dump()}

    @app.post("/sequence/stop", summary="Stop the running sequence")
    async def stop_sequence(target: int = Target):
        await _run(lambda key, dest: ap.stop_sequence(key, dest), target)
        return {"ok": True, "target": _dest(target)}

    @app.post("/candle", summary="Candle flicker")
    async def candle(target: int = Target):
        await _run(lambda key, dest: ap.set_candle_mode(key, dest, 1), target)
        return {"ok": True, "target": _dest(target)}

    @app.post("/time", summary="Set the lamp's clock")
    async def set_time():
        try:
            await mesh.send(lambda key, dest: ap.set_time(key))
        except ConnectionError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err
        return {"ok": True}

    @app.get("/dawn", summary="Read the stored dawn alarm")
    async def get_dawn(target: int = Target):
        dest = _dest(target)
        try:
            blocks = await mesh.query(
                lambda key, _mid: ap.get_schedule(key, dest, ap.ROUTINE_DAWN_SIMULATOR))
        except ConnectionError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err
        if not blocks:
            raise HTTPException(status_code=504,
                                detail="lamp did not report the schedule")
        return ap.decode_schedule(blocks[-1])

    @app.post("/dawn", summary="Set the dawn alarm (runs autonomously in the lamp)")
    async def set_dawn(body: DawnBody, target: int = Target):
        dest = _dest(target)
        await _run(lambda key, d: ap.set_dawn_simulator(
            key, d, body.hour, body.minute,
            fade_minutes=body.fade_minutes, brightness=body.brightness,
            temperature=body.temperature, weekdays=body.weekdays,
            enabled=body.enabled), target)
        await asyncio.sleep(1.0)
        blocks = await mesh.query(
            lambda key, _mid: ap.get_schedule(key, dest, ap.ROUTINE_DAWN_SIMULATOR))
        return {"ok": True, "target": dest,
                "saved": ap.decode_schedule(blocks[-1]) if blocks else None}

    _show = {"running": False, "step": None, "done": False, "error": None}
    _show_task: asyncio.Task | None = None

    def _show_done(task: asyncio.Task) -> None:
        nonlocal _show_task
        if _show_task is task:
            _show_task = None

    async def _run_lightshow(dest: int) -> None:
        async def send(builder):
            await mesh.send(lambda key, _mid: builder(key, dest))

        _show.update(running=True, step="starting", done=False, error=None)
        try:
            await run_lightshow(send, on_step=lambda s: _show.update(step=s))
        except asyncio.CancelledError:
            try:
                await mesh.send(lambda k, _m: ap.set_white_temperature(k, dest, 0))
                await asyncio.sleep(MIN_PAUSE)
                await mesh.send(lambda k, _m: ap.set_brightness(k, dest, 100))
            except Exception:
                pass
            _show.update(running=False, done=False, error="cancelled")
            raise
        except Exception as err:
            _show.update(running=False, done=False, error=f"{type(err).__name__}: {err}")
        else:
            _show.update(running=False, step=None, done=True, error=None)

    @app.post("/lightshow", summary="Start the light show (continues in the background)")
    async def start_lightshow(target: int = Target):
        nonlocal _show_task
        if _show["running"]:
            raise HTTPException(status_code=409, detail="light show already running")
        dest = _dest(target)
        _show.update(running=True, step="starting", done=False, error=None)
        _show_task = asyncio.create_task(_run_lightshow(dest))
        _show_task.add_done_callback(_show_done)
        return {"ok": True, "target": dest, "running": True, "step": "starting"}

    @app.get("/lightshow", summary="Light show status")
    async def lightshow_status():
        return dict(_show)

    @app.post("/lightshow/stop", summary="Cancel the running light show")
    async def stop_lightshow():
        nonlocal _show_task
        was_running = bool(_show.get("running"))
        if _show_task is not None and not _show_task.done():
            _show_task.cancel()
        _show.update(running=False, done=False, error="cancelled")
        return {"ok": True, "running": False, "was_running": was_running}

    return app


def build_app(config=None) -> FastAPI:
    """Wire up the configured backend and wrap it in the HTTP app."""
    if config is None:
        config = Config.from_env()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    return create_app(create(config), config)


def main() -> None:
    import uvicorn

    config = Config.from_env()
    uvicorn.run(build_app(config), host=config.host, port=config.port)