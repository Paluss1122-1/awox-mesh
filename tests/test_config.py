"""Tests for configuration parsing -- especially the optional mesh id.

The first-run path must work without AWOX_MESH_ID: the id is only learned from
the lamp, so requiring it up front would be a chicken-and-egg problem.
"""

import pytest

from awox_mesh.config import MESH_ID_HELP, Config, ConfigError

BASE = {
    "AWOX_MAC": "AA:BB:CC:DD:EE:FF",
    "AWOX_MESH_NAME": "meshname",
    "AWOX_MESH_PASSWORD": "meshpass",
}


def _set(monkeypatch, **overrides):
    for name in ("AWOX_MAC", "AWOX_MESH_NAME", "AWOX_MESH_PASSWORD", "AWOX_MESH_ID",
                 "AWOX_BACKEND", "AWOX_ADV_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**BASE, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_mesh_id_is_optional_for_discovery(monkeypatch):
    _set(monkeypatch)
    assert Config.from_env().mesh_id is None


def test_require_mesh_id_raises_with_help(monkeypatch):
    _set(monkeypatch)
    with pytest.raises(ConfigError) as err:
        Config.from_env(require_mesh_id=True)
    assert "discover" in str(err.value)
    assert MESH_ID_HELP in str(err.value)


def test_mesh_id_hex_is_parsed(monkeypatch):
    _set(monkeypatch, AWOX_MESH_ID="0x1234")
    assert Config.from_env().mesh_id == 0x1234


def test_mesh_id_decimal_is_parsed(monkeypatch):
    _set(monkeypatch, AWOX_MESH_ID="4660")
    assert Config.from_env().mesh_id == 4660


def test_invalid_mesh_id_is_rejected(monkeypatch):
    _set(monkeypatch, AWOX_MESH_ID="not-a-number")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_backend_defaults_to_auto(monkeypatch):
    _set(monkeypatch)
    assert Config.from_env().backend == "auto"
