"""Tests for configuration parsing -- especially the optional mesh id.

The first-run path must work without AWOX_MESH_ID: the id is only learned from
the lamp, so requiring it up front would be a chicken-and-egg problem.
"""

import os
from pathlib import Path

import pytest

from awox_mesh.config import (
    MESH_ID_HELP,
    Config,
    ConfigError,
    default_env_paths,
    load_default_env_files,
    load_env_file,
    parse_env_file,
)

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


def test_parse_env_file_strips_quotes_and_comments(tmp_path):
    path = tmp_path / "config.env"
    path.write_text('# comment\nAWOX_MAC="AA:BB:CC:DD:EE:FF"  # inline\nBAD LINE\nAWOX_PORT=8087\n')
    values = parse_env_file(path)
    assert values == {"AWOX_MAC": "AA:BB:CC:DD:EE:FF", "AWOX_PORT": "8087"}


def test_load_env_file_sets_missing_vars(tmp_path):
    key = "AWOX_EXAMPLE_ONLY"
    os.environ.pop(key, None)
    path = tmp_path / "config.env"
    path.write_text(f"{key}=1\n")
    try:
        assert load_env_file(path) == 1
        assert os.environ[key] == "1"
    finally:
        os.environ.pop(key, None)


def test_load_env_file_does_not_override_real_env(tmp_path):
    key = "AWOX_EXAMPLE_OVERRIDE"
    os.environ[key] = "keep"
    path = tmp_path / "config.env"
    path.write_text(f"{key}=change\n")
    try:
        load_env_file(path)
        assert os.environ[key] == "keep"
    finally:
        os.environ.pop(key, None)


def test_default_env_paths_lists_workdir_then_xdg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    paths = default_env_paths()
    assert paths[0] == tmp_path / "config.env"
    assert paths[1] == Path.home() / ".config" / "awox-mesh" / "config.env"


def test_no_env_file_flag_skips_loading(tmp_path, monkeypatch):
    key = "AWOX_EXAMPLE_NOFILE"
    path = tmp_path / "config.env"
    path.write_text(f"{key}=x\n")
    monkeypatch.setenv("AWOX_NO_ENV_FILE", "1")
    os.environ.pop(key, None)
    try:
        assert load_default_env_files([path]) == []
        assert key not in os.environ
    finally:
        os.environ.pop(key, None)
