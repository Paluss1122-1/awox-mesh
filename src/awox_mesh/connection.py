"""Unified connection factory.

Both backends expose the same small surface used by the bridge and the CLI:
``run``/``stop``, ``send(build)``, ``query(build)`` and ``query_state()``, plus
``is_connected``, ``states`` and ``last_error``.

``AWOX_BACKEND`` selects the backend:

* ``gatt``  -- gatttool (full features, including ``/state`` via notifications)
* ``bleak`` -- portable BLE control (no reliable notifications)
* ``auto``  -- gatt when ``gatttool`` is on ``PATH``, otherwise bleak (default)
"""

from __future__ import annotations

import shutil

from .config import Config, ConfigError


def _has_gatttool() -> bool:
    return shutil.which("gatttool") is not None


def create(config: Config):
    backend = config.backend
    if backend == "auto":
        backend = "gatt" if _has_gatttool() else "bleak"

    if backend == "gatt":
        from .gatt import GattConnection

        return GattConnection(
            config.mac, config.mesh_name, config.mesh_password, mesh_id=config.mesh_id,
            connect_timeout=config.connect_timeout, adapter=config.adapter,
            adapter_recall_cmd=config.adapter_recall_cmd,
        )
    if backend == "bleak":
        from .ble import BleakConnection

        return BleakConnection(
            config.mac, config.mesh_name, config.mesh_password, mesh_id=config.mesh_id,
            adv_timeout=config.adv_timeout, notifications=config.notifications,
        )

    raise ConfigError(f"unknown AWOX_BACKEND: {config.backend!r}")


__all__ = ["create", "_has_gatttool"]
