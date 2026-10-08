"""Tests for the HTTP bridge contract, using a stub connection -- no hardware.

The important property under test is the honesty rule: the bridge must never
report a state it did not verify.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from awox_mesh.bridge.app import create_app
from awox_mesh.config import Config

DEST = 0x1234


class StubMesh:
    def __init__(self, connected: bool = True, devices: list | None = None):
        self.mac = "AA:BB:CC:DD:EE:FF"
        self.is_connected = connected
        self.devices = devices if devices is not None else []
        self.states = {d["mesh_id"]: d for d in self.devices}
        self.last_error = None if connected else "link dead"
        self.last_seen = 0.0
        self.sent: list[bytes] = []
        self.query_calls = 0
        self._stop = False

    async def run(self):
        while not self._stop:
            await asyncio.sleep(0.01)

    async def stop(self):
        self._stop = True

    async def send(self, build):
        if not self.is_connected:
            raise ConnectionError(self.last_error or "no link")
        self.sent.append(build(bytes(range(16)), DEST))

    async def query(self, build, flags=(0x35,), wait=5.0):
        self.query_calls += 1
        return []

    async def query_state(self, wait=35.0):
        if not self.is_connected:
            raise ConnectionError(self.last_error or "no link")
        self.query_calls += 1
        return list(self.devices)


def make_config(**overrides) -> Config:
    base = dict(mac="AA:BB:CC:DD:EE:FF", mesh_name="meshname",
                mesh_password="meshpass", mesh_id=DEST)
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def client():
    with TestClient(create_app(StubMesh(devices=[{"mesh_id": DEST, "state": False}]),
                               make_config())) as c:
        yield c


def test_health_reports_connection(client):
    data = client.get("/health").json()
    assert data["connected"] is True
    assert data["mesh_id"] == "0x1234"
    assert data["last_error"] is None


def test_state_is_fresh_when_the_lamp_answered(client):
    data = client.get("/state").json()
    assert data["fresh"] is True
    assert data["devices"][0]["mesh_id"] == DEST


def test_turn_off_sends_a_command(client):
    data = client.post("/off").json()
    assert data["state"] is False
    assert data["fresh"] is True


def test_toggle_reports_the_previous_state(client):
    data = client.post("/toggle").json()
    assert data["was"] is False
    assert data["state"] is True


def test_brightness_rejects_out_of_range(client):
    assert client.post("/brightness", json={"percent": 101}).status_code == 422
    assert client.post("/brightness", json={"percent": -1}).status_code == 422


def test_white_requires_at_least_one_field(client):
    assert client.post("/white", json={}).status_code == 422


def test_api_key_is_enforced():
    mesh = StubMesh(devices=[{"mesh_id": DEST, "state": True}])
    app = create_app(mesh, make_config(api_key="s3cret"))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 401
        assert c.get("/health", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/health", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_no_fresh_state_means_503_and_sends_nothing():
    """The honesty rule: an unverified state is never reported as fact."""
    mesh = StubMesh(devices=[])  # lamp answers nothing
    app = create_app(mesh, make_config())
    with TestClient(app) as c:
        for action in ("/on", "/off", "/toggle"):
            response = c.post(action)
            assert response.status_code == 503, action
            assert "state unknown" in response.json()["detail"], action
    assert mesh.sent == [], "no command may be sent without a verified state"


def test_disconnected_link_reports_unknown_not_failure():
    mesh = StubMesh(connected=False)
    app = create_app(mesh, make_config())
    with TestClient(app) as c:
        response = c.post("/on")
        assert response.status_code == 503
        assert "no connection to the lamp" in response.json()["detail"]


def test_last_state_endpoint_is_never_fresh():
    with TestClient(create_app(
            StubMesh(devices=[{"mesh_id": DEST, "state": True}]), make_config())) as c:
        data = c.get("/state/last").json()
        assert data["fresh"] is False
        assert data["state"] is True


def test_lightshow_status_reports_nothing_running(client):
    data = client.get("/lightshow").json()
    assert data["running"] is False
    assert data["done"] is False


def test_dawn_read_reports_504_when_the_lamp_stays_silent(client):
    assert client.get("/dawn").status_code == 504


def test_sequence_body_validation(client):
    assert client.post("/sequence", json={"sequence": 9}).status_code == 422
    assert client.post("/sequence", json={"sequence": 3}).status_code == 200