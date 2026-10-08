"""gatttool backend for AwoX/EGLO mesh lamps.

``gatttool`` is deprecated but remains the only way to receive the lamp's
notifications here: the status characteristic has no CCCD, so BlueZ's
``StartNotify()`` fails and the chip drops the link. Notifications arrive on
their own once a valid command is sent, and gatttool exposes them as plain
text -- which is what this backend parses.

The blocking gatttool session lives in :class:`GattSession`; :class:`GattConnection`
wraps it in an asyncio-friendly, reconnecting public interface.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from os import urandom

import pexpect

from . import protocol as ap

logger = logging.getLogger(__name__)

PROMPT = r"\[LE\]>"
NOTIFY_RE = re.compile(
    r"(?:Notification|Indication)\s+handle\s*=\s*(0x[0-9a-f]{4})\s+value:\s*([0-9a-f ]+)")
VALUE_RE = re.compile(r"Characteristic value/descriptor: ([0-9a-f ]+)")

STALE_AFTER = 90.0
STATUS_WAIT = 35.0
PAIR_RETRY_PAUSE = 5.0
TICK = 30.0
TICK_MAX = 120.0
ADAPTER_RECALL_AFTER = 2


def _detect_adapter() -> str | None:
    env = os.environ.get("AWOX_ADAPTER")
    if env:
        return env.strip()
    try:
        out = subprocess.run(["hciconfig"], capture_output=True, text=True, timeout=2)
        cur = None
        ups: list[str] = []
        downs: list[str] = []
        for line in out.stdout.splitlines():
            if line.startswith("hci"):
                cur = line.split(":")[0]
            if cur and "UP" in line:
                if cur not in ups:
                    ups.append(cur)
            elif cur and "DOWN" in line:
                if cur not in downs and cur not in ups:
                    downs.append(cur)
        if ups:
            return ups[-1] if len(ups) > 1 else ups[0]
        if downs:
            return downs[0]
    except Exception:
        pass
    return None


class GattSession:
    def __init__(self, mac: str, mesh_name: str, mesh_password: str,
                 pair_handle: str = "0x1e", status_handle: str = "0x12",
                 command_handle: str = "0x15", adapter: str | None = None):
        self.mac = mac.upper()
        self.mesh_name = mesh_name.encode()
        self.mesh_password = mesh_password.encode()
        self.pair_handle = pair_handle
        self.status_handle = status_handle
        self.command_handle = command_handle
        self.adapter = adapter or _detect_adapter()
        self.session_key: bytearray | None = None
        self.child = None
        self.last_notify = 0.0
        self.pair_diagnosis = ""

    def connect(self, timeout: float = 60) -> bool:
        adapter_arg = f" -i {self.adapter}" if self.adapter else ""
        self.child = pexpect.spawn(f"gatttool{adapter_arg} -b {self.mac} -t public -I",
                                   encoding="utf-8", timeout=timeout)
        if self.adapter:
            logger.info("using BT adapter %s for %s", self.adapter, self.mac)
        self.child.expect(PROMPT)
        self.child.sendline("sec-level low")
        self.child.expect(PROMPT)

        deadline = time.time() + timeout
        while time.time() < deadline:
            self.child.sendline("connect")
            try:
                index = self.child.expect(
                    ["Connection successful", "Error", "busy", PROMPT], timeout=25)
            except pexpect.TIMEOUT:
                continue
            if index == 0:
                logger.info("connected to %s", self.mac)
                time.sleep(1.0)
                return True
            time.sleep(2)
        logger.error("connection to %s failed", self.mac)
        return False

    def _drain(self) -> None:
        """Empty the buffer so no earlier command's output is read."""
        while True:
            try:
                self.child.read_nonblocking(size=4096, timeout=0.15)
            except Exception:
                return

    def read_handle(self, handle: str, timeout: float = 6) -> bytes | None:
        self._drain()
        self.child.sendline(f"char-read-hnd {handle}")
        try:
            self.child.expect(VALUE_RE, timeout=timeout)
        except (pexpect.TIMEOUT, pexpect.EOF):
            return None
        return bytes.fromhex(self.child.match.group(1).replace(" ", "").strip())

    def write_handle(self, handle: str, data: bytes, with_response: bool = True) -> str:
        """Write a handle without draining.

        The buffer must not be drained here: with notifications active they would
        be inside it and lost.
        """
        verb = "char-write-req" if with_response else "char-write-cmd"
        self.child.sendline(f"{verb} {handle} {data.hex()}")
        try:
            self.child.expect(PROMPT, timeout=8)
        except (pexpect.TIMEOUT, pexpect.EOF):
            return ""
        return self.child.before or ""

    def pair(self) -> bool:
        self.pair_diagnosis = ""
        session_random = urandom(8)
        packet = ap.make_pair_packet(self.mesh_name, self.mesh_password, session_random)
        logger.info("sending pair packet %s to %s", bytes(packet).hex(), self.pair_handle)
        self.write_handle(self.pair_handle, bytes(packet))

        stable = None
        reads = 0
        blanks = 0
        sample = None
        deadline = time.time() + 15.0
        while time.time() < deadline:
            time.sleep(0.4)
            reply = self.read_handle(self.pair_handle, timeout=3)
            reads += 1
            if reply is None or len(reply) < 9 or reply[0] not in (0x0D, 0x0E):
                if reply is None:
                    blanks += 1
                if sample is None:
                    sample = reply
                continue
            logger.info("  pair response [%d]: %s", reads, reply.hex())
            stable = reply
            break

        if stable is None:
            if reads and blanks == reads:
                self.pair_diagnosis = (
                    "lamp does not answer on the pair characteristic at all -- "
                    "chip stuck: wall switch off 10 s, on again")
            else:
                self.pair_diagnosis = (
                    "lamp only answers with unusable data (first raw reply: "
                    f"{'none' if sample is None else sample.hex()})")
            logger.error(
                "no usable pair response after %d reads (%d without answer) -- %s",
                reads, blanks, self.pair_diagnosis)
            return False

        if stable[0] == 0x0D:
            self.session_key = ap.make_session_key(
                self.mesh_name, self.mesh_password, session_random, stable[1:9])
            logger.info("session key: %s", bytes(self.session_key).hex())
            return True

        self.pair_diagnosis = ("lamp rejected the pairing (0x0e, alternating mode) "
                               "-- mind the retry timing")
        logger.error("pairing rejected by the lamp (0x0e)")
        return False

    def collect_notifications(self, seconds: float) -> list:
        results = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                self.child.expect(NOTIFY_RE, timeout=max(1, deadline - time.time()))
            except (pexpect.TIMEOUT, pexpect.EOF):
                break
            handle, hexvals = self.child.match.groups()
            raw = bytes.fromhex(hexvals.strip().replace(" ", ""))
            self.last_notify = time.time()
            results.append((handle, raw, None))
            logger.info("NOTIFY %s: %s", handle, raw.hex())
        return results

    def close(self) -> None:
        if self.child is not None:
            try:
                self.child.sendline("disconnect")
                self.child.sendline("exit")
                self.child.close()
            except Exception:
                pass


class GattConnection:
    def __init__(self, mac: str, mesh_name: str, mesh_password: str,
                 mesh_id: int, connect_timeout: float = 90.0,
                 adapter: str | None = None,
                 adapter_recall_cmd: str | None = None):
        self.mac = mac.upper()
        self.mesh_name = mesh_name
        self.mesh_password = mesh_password
        self.mesh_id = mesh_id
        self.connect_timeout = connect_timeout
        self.adapter = adapter
        self.adapter_recall_cmd = adapter_recall_cmd

        self._session: GattSession | None = None
        self._lock = asyncio.Lock()
        self._stop = False

        self.states: dict[int, dict] = {}
        self.last_error: str | None = None
        self.last_seen: float = 0.0

    @property
    def is_connected(self) -> bool:
        return (self._session is not None
                and self._session.session_key is not None
                and self._session.child is not None
                and self._session.child.isalive())

    @property
    def session_key(self) -> bytes | None:
        return bytes(self._session.session_key) if self.is_connected else None

    def _connect_blocking(self) -> bool:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass

        last_error = "no mesh connection"
        for attempt in (1, 2):
            session = GattSession(self.mac, self.mesh_name, self.mesh_password,
                                  adapter=self.adapter)
            if not session.connect(timeout=self.connect_timeout):
                last_error = "connection failed (lamp off, app connected?)"
                session.close()
                break
            if not session.pair():
                last_error = (session.pair_diagnosis
                              or "pairing rejected by the lamp (0x0e)")
                session.close()
                if attempt == 1:
                    logger.warning(
                        "pairing rejected (0x0e) -- lamp in alternating mode, "
                        "retry with a fresh session in %.0f s", PAIR_RETRY_PAUSE)
                    time.sleep(PAIR_RETRY_PAUSE)
                continue
            self._session = session
            self.last_error = None

            try:
                session.write_handle(session.command_handle,
                                     ap.set_time(bytes(session.session_key)))
            except Exception as err:
                logger.debug("setting the clock failed: %s", err)
            return True

        self.last_error = last_error
        return False

    async def ensure_connected(self) -> bool:
        if self.is_connected:
            return True
        ok = await asyncio.to_thread(self._connect_blocking)
        if ok:
            logger.info("mesh session up (%s)", self.mac)
        return ok

    def _close_session(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
        self._session = None

    async def _recall_adapter(self) -> None:
        if not self.adapter_recall_cmd:
            return
        logger.warning("re-enumerating the adapter -- reason: %d failed connect cycles",
                       ADAPTER_RECALL_AFTER)
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "-n", self.adapter_recall_cmd],
                capture_output=True, text=True, timeout=90)
        except Exception as err:
            logger.error("adapter recall failed: %s: %s", type(err).__name__, err)
            return
        for line in (proc.stdout or "").splitlines():
            logger.info("  %s", line)
        if proc.returncode == 0:
            logger.info("adapter recall done, next connect uses a fresh hci")
        else:
            logger.error("adapter recall exited with code %s: %s",
                         proc.returncode, (proc.stderr or "").strip())

    async def run(self) -> None:
        stale = 0
        fails = 0
        while not self._stop:
            delay = TICK
            try:
                if not self.is_connected:
                    async with self._lock:
                        ok = await self.ensure_connected()
                    stale = 0
                    if ok:
                        fails = 0
                    else:
                        fails += 1
                        delay = min(TICK * 2 ** min(fails - 1, 2), TICK_MAX)
                        logger.info("connect cycle %d failed, next try in %.0f s",
                                    fails, delay)
                        if fails == ADAPTER_RECALL_AFTER:
                            await self._recall_adapter()
                else:
                    devices = await self.query_state()
                    session = self._session
                    heard = (session is not None
                             and time.time() - session.last_notify < STALE_AFTER)
                    if devices or heard:
                        stale = 0
                        fails = 0
                    else:
                        stale += 1
                        if stale >= 2:
                            logger.warning(
                                "%dx no notification from the lamp any more -- "
                                "link considered dead, forcing reconnect", stale)
                            async with self._lock:
                                await asyncio.to_thread(self._close_session)
                                await self.ensure_connected()
                            stale = 0
            except Exception as err:
                self.last_error = f"{type(err).__name__}: {err}"
                logger.warning("connection loop: %s", self.last_error)
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._stop = True
        if self._session is not None:
            await asyncio.to_thread(self._session.close)
            self._session = None

    async def send(self, build) -> None:
        async with self._lock:
            if (self._session is not None and self.last_seen
                    and time.time() - self.last_seen > STALE_AFTER):
                age = time.time() - self.last_seen
                logger.warning(
                    "last confirmation %.0f s ago -- link considered dead, "
                    "reconnect before the command", age)
                await asyncio.to_thread(self._close_session)
            if not await self.ensure_connected():
                raise ConnectionError(self.last_error or "no mesh connection")
            packet = build(self.session_key, self.mesh_id)
            await asyncio.to_thread(
                self._session.write_handle, self._session.command_handle, packet)

    async def query(self, build, flags=(0x35,), wait: float = 5.0) -> list[bytes]:
        async with self._lock:
            if not await self.ensure_connected():
                raise ConnectionError(self.last_error or "no mesh connection")
            key = self.session_key

            def _run():
                self._session.write_handle(self._session.command_handle,
                                           build(key, self.mesh_id))
                blocks: list[bytes] = []
                deadline = time.time() + wait
                first = min(1.0, wait)
                for _handle, raw, _dec in self._session.collect_notifications(first):
                    if len(raw) >= 17 and raw[0] in flags:
                        blocks.append(ap.att_decrypt(key, raw[1:17]))
                if blocks:
                    return blocks
                while time.time() < deadline:
                    chunk = min(0.5, deadline - time.time())
                    if chunk <= 0:
                        break
                    found = False
                    for _handle, raw, _dec in self._session.collect_notifications(chunk):
                        if len(raw) >= 17 and raw[0] in flags:
                            blocks.append(ap.att_decrypt(key, raw[1:17]))
                            found = True
                    if found:
                        return blocks
                return blocks

            try:
                return await asyncio.to_thread(_run)
            except Exception as err:
                self.last_error = f"{type(err).__name__}: {err}"
                return []

    async def query_state(self, wait: float = STATUS_WAIT) -> list[dict]:
        async with self._lock:
            if not await self.ensure_connected():
                raise ConnectionError(self.last_error or "no mesh connection")
            key = self.session_key

            def _collect(collector, found: dict[int, dict]) -> bool:
                had = False
                for _handle, raw, _dec in collector:
                    if len(raw) < 17 or raw[0] not in (0x80, 0x82):
                        continue
                    block = ap.att_decrypt(key, raw[1:17])
                    mesh_id = int.from_bytes(block[0:2], "little")
                    found[mesh_id] = {
                        "mesh_id": mesh_id,
                        "state": bool(block[12] & 1),
                        "mode": block[12],
                        "raw": block.hex(),
                    }
                    had = True
                return had

            def _query():
                self._session.write_handle(self._session.command_handle,
                                           ap.build_status_query(key))
                found: dict[int, dict] = {}
                deadline = time.time() + wait
                first = min(1.0, wait)
                if _collect(self._session.collect_notifications(first), found):
                    return list(found.values())
                while time.time() < deadline:
                    chunk = min(0.5, deadline - time.time())
                    if chunk <= 0:
                        break
                    if _collect(self._session.collect_notifications(chunk), found):
                        return list(found.values())
                return list(found.values())

            try:
                devices = await asyncio.to_thread(_query)
            except Exception as err:
                self.last_error = f"{type(err).__name__}: {err}"
                logger.warning("status query failed: %s", self.last_error)
                return []

        for device in devices:
            self.states[device["mesh_id"]] = device
        if devices:
            self.last_seen = time.time()
        return devices
