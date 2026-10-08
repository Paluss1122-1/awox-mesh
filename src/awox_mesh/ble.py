"""Asynchronous BLE client for AwoX/EGLO mesh lamps (bleak backend).

This backend gives portable, cross-platform **control** over BLE. Reading the
lamp state via notifications is not reliable here: the status characteristic has
no CCCD, so BlueZ's ``StartNotify()`` fails with ``Unlikely Error`` and the chip
then drops the link. Use the gatttool backend (``awox_mesh.gatt``) when you need
``query_state``/``query``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from os import urandom

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from . import protocol as ap

try:
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    HAVE_DBUS = True
except ImportError:  # pragma: no cover
    BusType = MessageBus = None
    HAVE_DBUS = False

logger = logging.getLogger(__name__)

PAIR_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d1914"
COMMAND_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d1912"
STATUS_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d1911"

BROADCAST = ap.BROADCAST


class AuthError(RuntimeError):
    """The device rejected the mesh name/password."""


class BleakConnection:
    def __init__(self, mac: str, mesh_name: str, mesh_password: str, mesh_id: int,
                 keepalive: float = 30.0, reconnect_delay: float = 5.0,
                 adv_timeout: float = 90.0, connect_attempts: int = 4,
                 notifications: bool = False):
        assert len(mesh_name.encode()) <= 16, "mesh_name max. 16 bytes"
        assert len(mesh_password.encode()) <= 16, "mesh_password max. 16 bytes"

        self.mac = mac.upper()
        self.mesh_name = mesh_name.encode()
        self.mesh_password = mesh_password.encode()
        self.mesh_id = mesh_id
        self.keepalive = keepalive
        self.reconnect_delay = reconnect_delay
        self.adv_timeout = adv_timeout
        self.connect_attempts = connect_attempts
        self.notifications = notifications
        self.notifications_active = False
        self.write_with_response = True

        self._client: BleakClient | None = None
        self._session_key: bytearray | None = None
        self._write_lock = asyncio.Lock()
        self._connected_evt = asyncio.Event()
        self._stop = False

        self.states: dict[int, dict] = {}
        self.last_error: str | None = None
        self.last_seen: float = 0.0

    @property
    def is_connected(self) -> bool:
        return (self._session_key is not None
                and self._client is not None
                and self._client.is_connected)

    async def wait_connected(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._connected_evt.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _await_advertisement(self, timeout: float):
        loop = asyncio.get_running_loop()
        found: asyncio.Future = loop.create_future()

        def detected(device, adv):
            if device.address.upper() == self.mac and not found.done():
                logger.info("Advertisement from %s (RSSI %s dBm)", self.mac, adv.rssi)
                found.set_result(device)

        async with BleakScanner(detected):
            return await asyncio.wait_for(found, timeout)

    async def _find_path(self, bus: MessageBus) -> str | None:
        introspection = await bus.introspect("org.bluez", "/")
        proxy = bus.get_proxy_object("org.bluez", "/", introspection)
        manager = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
        for path, interfaces in (await manager.call_get_managed_objects()).items():
            device = interfaces.get("org.bluez.Device1")
            if device and device["Address"].value.upper() == self.mac:
                return path
        return None

    async def _bluez_device_path(self) -> str | None:
        """BlueZ D-Bus object path of the lamp, if bluetoothd knows it."""
        if not HAVE_DBUS:
            return None
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            return await self._find_path(bus)
        except Exception as err:
            logger.debug("BlueZ query failed: %s: %s", type(err).__name__, err)
            return None
        finally:
            bus.disconnect()

    async def _bluez_reset_device(self, remove: bool) -> None:
        if not HAVE_DBUS:
            return
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            path = await self._find_path(bus)
            if path is None:
                return

            introspection = await bus.introspect("org.bluez", path)
            device = bus.get_proxy_object("org.bluez", path, introspection) \
                        .get_interface("org.bluez.Device1")
            try:
                await device.call_disconnect()
            except Exception:
                pass

            if remove:
                adapter_path = path.rsplit("/", 1)[0]
                introspection = await bus.introspect("org.bluez", adapter_path)
                adapter = bus.get_proxy_object("org.bluez", adapter_path, introspection) \
                             .get_interface("org.bluez.Adapter1")
                await adapter.call_remove_device(path)
                logger.info("Removed BlueZ device record %s -- it will be rediscovered", path)
        except Exception as err:
            logger.debug("BlueZ reset failed: %s: %s", type(err).__name__, err)
        finally:
            bus.disconnect()

    async def _connect_once(self) -> None:
        """Connect, preferring BlueZ Direct Connect.

        If bluetoothd already knows the device, call ``Connect()`` directly: the
        controller then keeps its own connection window open and grabs the lamp
        whenever it advertises -- the same trick the phone app uses to catch the
        lamp despite its rare advertisements.
        """
        deadline = time.monotonic() + self.adv_timeout
        attempt = 0
        last: Exception | None = None
        client: BleakClient | None = None

        while time.monotonic() < deadline:
            attempt += 1
            try:
                path = await self._bluez_device_path()
                if path:
                    logger.info("Direct Connect to %s (attempt %d) ...", path, attempt)
                    device = BLEDevice(self.mac, None, {"path": path})
                else:
                    remaining = max(10.0, deadline - time.monotonic())
                    logger.info("BlueZ does not know %s -- waiting for advertisement "
                                "(attempt %d, up to %.0fs) ...", self.mac, attempt, remaining)
                    device = await self._await_advertisement(remaining)

                client = BleakClient(device, timeout=30.0,
                                     disconnected_callback=self._on_disconnect)
                await client.connect()
                await asyncio.sleep(0.5)
                if not client.is_connected:
                    raise BleakError("link lost right after connect")
                break

            except asyncio.TimeoutError:
                last = BleakError(
                    f"No advertisement from {self.mac} received (unplugged, out of "
                    f"range, or the phone app holds the connection?)")
                break
            except Exception as err:
                last = err
                logger.warning("Connection attempt %d failed: %s: %s",
                               attempt, type(err).__name__, err)
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    client = None

                text = str(err)
                if "br-connection" in text or "le-connection" in text:
                    await self._bluez_reset_device(remove=True)
                    await asyncio.sleep(3.0)
                elif "already in progress" in text or "in progress" in text:
                    await self._bluez_reset_device(remove=False)
                    await asyncio.sleep(5.0)
                else:
                    await asyncio.sleep(2.0)

        if client is None or not client.is_connected:
            raise BleakError(f"connection failed after {attempt} attempt(s): "
                             f"{type(last).__name__ if last else 'Timeout'}: {last}")

        logger.info("BLE connected to %s", self.mac)

        try:
            await self._handshake(client)
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
            raise

        self.last_error = None
        self._connected_evt.set()

        if self.notifications_active:
            await self.request_status()

    async def _handshake(self, client: BleakClient) -> None:
        session_random = urandom(8)
        pair_packet = ap.make_pair_packet(self.mesh_name, self.mesh_password, session_random)
        await client.write_gatt_char(PAIR_CHAR_UUID, bytes(pair_packet), response=True)

        reply = None
        for _ in range(15):
            await asyncio.sleep(0.4)
            candidate = bytearray(await client.read_gatt_char(PAIR_CHAR_UUID))
            if len(candidate) >= 9 and candidate[0] in (0x0D, 0x0E):
                reply = candidate
                break
        if reply is None:
            raise BleakError("no usable pair response")

        if reply[0] == 0x0D:
            self._session_key = ap.make_session_key(
                self.mesh_name, self.mesh_password, session_random, bytes(reply[1:9]))
        elif reply[0] == 0x0E:
            raise AuthError(
                "The device rejected the mesh credentials. Were the lamps re-paired "
                "with a different account? Re-fetch the credentials.")
        else:
            raise BleakError(f"unexpected pair response: {reply.hex()}")

        self._client = client
        logger.info("Mesh session established (%s)", self.mesh_name.decode())

        self.notifications_active = False
        if self.notifications:
            try:
                await client.start_notify(STATUS_CHAR_UUID, self._on_notification)
                await client.write_gatt_char(STATUS_CHAR_UUID, b"\x01", response=True)
                self.notifications_active = True
                logger.info("Status notifications active")
            except Exception as err:
                logger.warning(
                    "Status notifications unavailable (%s: %s) -- control still "
                    "works, /state will stay empty",
                    type(err).__name__, err)

    def _on_disconnect(self, _client) -> None:
        logger.warning("BLE link to %s lost", self.mac)
        self._session_key = None
        self._connected_evt.clear()

    async def run(self) -> None:
        """Keep the connection open; run as a background task."""
        while not self._stop:
            if not self.is_connected:
                try:
                    await self._connect_once()
                except AuthError as err:
                    self.last_error = str(err)
                    logger.error("%s", err)
                    return
                except Exception as err:
                    self.last_error = f"{type(err).__name__}: {err}"
                    logger.warning("Connection attempt failed: %s", self.last_error)
                    await asyncio.sleep(self.reconnect_delay)
                    continue

            await asyncio.sleep(self.keepalive)
            if self.is_connected:
                try:
                    await self.request_status()
                except Exception as err:
                    logger.warning("Keepalive failed: %s", err)

    async def stop(self) -> None:
        self._stop = True
        self._session_key = None
        self._connected_evt.clear()
        if self._client is not None and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as err:
                logger.warning("Disconnect failed: %s", err)

    async def _send_raw(self, packet: bytes, response: bool | None = None) -> None:
        if not self.is_connected:
            raise ConnectionError(self.last_error or "not connected to the mesh")
        if response is None:
            response = self.write_with_response
        async with self._write_lock:
            await self._client.write_gatt_char(COMMAND_CHAR_UUID, packet, response=response)

    def _target(self, dest: int | None) -> int:
        return self.mesh_id if dest is None else dest

    async def send(self, build) -> None:
        """Send a packet built by ``build(session_key, mesh_id)``."""
        if not self.is_connected:
            raise ConnectionError(self.last_error or "not connected to the mesh")
        await self._send_raw(build(self._session_key, self.mesh_id))

    async def query(self, build, flags=(0x35,), wait: float = 5.0) -> list[bytes]:
        """Notifications are not reliably reachable over bleak; returns ``[]``."""
        logger.debug("query() is not supported by the bleak backend")
        return []

    async def query_state(self, wait: float = 35.0) -> list[dict]:
        """Best effort: request status and return whatever arrived via notifications."""
        if not self.is_connected:
            raise ConnectionError(self.last_error or "not connected to the mesh")
        if not self.notifications_active:
            return []
        await self.request_status()
        await asyncio.sleep(min(wait, 2.0))
        devices = list(self.states.values())
        if devices:
            self.last_seen = time.time()
        return devices

    async def request_status(self, dest: int = BROADCAST) -> None:
        await self._send_raw(ap.build_status_query(self._session_key, dest))

    def _on_notification(self, _sender, data: bytearray) -> None:
        if self._session_key is None:
            return
        status = self.parse_status(self._session_key, bytes(data))
        if status:
            self.states[status["mesh_id"]] = status

    @staticmethod
    def parse_status(session_key: bytes, packet: bytes) -> dict | None:
        if len(packet) < 17 or packet[0] not in (0x80, 0x82):
            return None
        return ap.decode_status(ap.att_decrypt(session_key, packet[1:17]))
