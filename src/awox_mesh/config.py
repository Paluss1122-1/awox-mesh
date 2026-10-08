"""Shared runtime configuration for the library, bridge and CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


def parse_env_file(path: str | os.PathLike) -> dict[str, str]:
    """Parse a simple ``NAME=value`` file (quotes and ``#`` comments stripped)."""
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name, sep, value = line.partition("=")
                name = name.strip()
                if not (sep and name):
                    continue
                values[name] = value.split("#", 1)[0].strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def load_env_file(path: str | os.PathLike, override: bool = False) -> int:
    """Load a config file into the environment without overriding real variables."""
    values = parse_env_file(path)
    for name, value in values.items():
        if override or name not in os.environ:
            os.environ[name] = value
    return len(values)


def default_env_paths() -> list[Path]:
    return [Path.cwd() / "config.env",
            Path.home() / ".config" / "awox-mesh" / "config.env"]


def load_default_env_files(paths: list[Path] | None = None) -> list[str]:
    """Load config.env from the working directory and the XDG config dir, if present."""
    if os.environ.get("AWOX_NO_ENV_FILE"):
        return []
    loaded = []
    for path in (paths if paths is not None else default_env_paths()):
        if Path(path).is_file() and load_env_file(path):
            loaded.append(str(path))
    return loaded


def _parse_mesh_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(str(raw), 0)
    except ValueError as err:
        raise ConfigError(f"AWOX_MESH_ID is not a valid integer: {raw!r}") from err


MESH_ID_HELP = (
    "AWOX_MESH_ID is not set. It is the real mesh address of your lamp and has "
    "no safe default. Run 'awox-mesh discover' to read it from the lamp (it is "
    "in the first two bytes of every status block), then set AWOX_MESH_ID=0x...."
)


@dataclass(frozen=True)
class Config:
    mac: str
    mesh_name: str
    mesh_password: str
    mesh_id: int | None = None
    api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8087
    log_level: str = "INFO"
    adv_timeout: float = 600.0
    notifications: bool = False
    backend: str = "auto"
    adapter: str | None = None
    connect_timeout: float = 45.0
    adapter_recall_cmd: str | None = None

    @classmethod
    def from_env(cls, require_mesh_id: bool = False) -> Config:
        config = cls(
            mac=_required("AWOX_MAC"),
            mesh_name=_required("AWOX_MESH_NAME"),
            mesh_password=_required("AWOX_MESH_PASSWORD"),
            mesh_id=_parse_mesh_id(os.environ.get("AWOX_MESH_ID")),
            api_key=os.environ.get("AWOX_API_KEY") or None,
            host=os.environ.get("AWOX_HOST", "127.0.0.1"),
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
        if require_mesh_id and config.mesh_id is None:
            raise ConfigError(MESH_ID_HELP)
        return config
