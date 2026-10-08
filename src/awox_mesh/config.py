"""Shared runtime configuration for the library, bridge and CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(SystemExit):
    """Raised when a mandatory setting is missing."""


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"{name} is not set. Create config.env (see config.env.example) "
            "and start through it, or export the variable."
        )
    return value


def _parse_mesh_id(raw: str | None) -> int:
    if not raw:
        raise ConfigError(
            "AWOX_MESH_ID is not set. It is the real mesh address of your lamp "
            "(it appears in the first two bytes of every status block)."
        )
    try:
        return int(str(raw), 0)
    except ValueError as err:
        raise ConfigError(f"AWOX_MESH_ID is not a valid integer: {raw!r}") from err


@dataclass(frozen=True)
class Config:
    mac: str
    mesh_name: str
    mesh_password: str
    mesh_id: int
    api_key: str | None = None
    host: str = "0.0.0.0"
    port: int = 8087
    log_level: str = "INFO"
    adv_timeout: float = 600.0
    notifications: bool = False
    backend: str = "auto"
    adapter: str | None = None
    connect_timeout: float = 45.0
    adapter_recall_cmd: str | None = None

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            mac=_required("AWOX_MAC"),
            mesh_name=_required("AWOX_MESH_NAME"),
            mesh_password=_required("AWOX_MESH_PASSWORD"),
            mesh_id=_parse_mesh_id(os.environ.get("AWOX_MESH_ID")),
            api_key=os.environ.get("AWOX_API_KEY") or None,
            host=os.environ.get("AWOX_HOST", "0.0.0.0"),
            port=int(os.environ.get("AWOX_PORT", "8087")),
            log_level=os.environ.get("AWOX_LOG_LEVEL", "INFO"),
            adv_timeout=float(os.environ.get("AWOX_ADV_TIMEOUT", "600")),
            notifications=os.environ.get("AWOX_NOTIFICATIONS", "").lower()
            in ("1", "true", "yes"),
            backend=os.environ.get("AWOX_BACKEND", "auto").lower(),
            adapter=os.environ.get("AWOX_ADAPTER") or None,
            connect_timeout=float(os.environ.get("AWOX_CONNECT_TIMEOUT", "45")),
            adapter_recall_cmd=os.environ.get("AWOX_ADAPTER_RECALL_CMD") or None,
        )
