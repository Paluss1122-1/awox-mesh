"""Pick the BLE backend: gatt (gatttool), bleak, or auto."""

from __future__ import annotations

import shutil

from .config import Config, ConfigError

_UNSET_MESH_ID = 0


def _has_gatttool() -> bool:
    return shutil.which("gatttool") is not None


def resolve_backend(config: Config) -> str:
    backend = config.backend
    if backend == "auto":
        return "gatt" if _has_gatttool() else "bleak"
    return backend


def create(config: Config):
    backend = resolve_backend(config)
    mesh_id = _UNSET_MESH_ID if config.mesh_id is None else config.mesh_id

    if backend == "gatt":
        from .gatt import GattConnection

        return GattConnection(
            config.mac, config.mesh_name, config.mesh_password, mesh_id=mesh_id,
            connect_timeout=config.connect_timeout, adapter=config.adapter,
            adapter_recall_cmd=config.adapter_recall_cmd,
        )
    if backend == "bleak":
        from .ble import BleakConnection

        return BleakConnection(
            config.mac, config.mesh_name, config.mesh_password, mesh_id=mesh_id,
            adv_timeout=config.adv_timeout, notifications=config.notifications,
        )

    raise ConfigError(f"unknown AWOX_BACKEND: {config.backend!r}")


async def discover(config: Config) -> list[dict]:
    """Read every lamp's mesh id from a broadcast status query (no AWOX_MESH_ID needed)."""
    backend = resolve_backend(config)
    if backend == "bleak":
        raise ConfigError(
            "discovery needs the gatt backend: status notifications are only "
            "reachable through gatttool (the status characteristic has no CCCD). "
            "Install bluez and set AWOX_BACKEND=gatt."
        )

    from .gatt import GattConnection

    mesh = GattConnection(
        config.mac, config.mesh_name, config.mesh_password, mesh_id=_UNSET_MESH_ID,
        connect_timeout=config.connect_timeout, adapter=config.adapter,
        adapter_recall_cmd=config.adapter_recall_cmd,
    )
    try:
        return await mesh.query_state()
    finally:
        await mesh.stop()


__all__ = ["create", "discover", "resolve_backend"]
