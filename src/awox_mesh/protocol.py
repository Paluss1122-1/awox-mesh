"""AwoX/EGLO BLE mesh command framing (the non-Telink variant).

Pure packet arithmetic and crypto, no Bluetooth. See docs/protocol.md.
"""

from __future__ import annotations

import colorsys

from Crypto.Cipher import AES

OP_POWER = (0x00, 0x06)
OP_BRIGHTNESS = (0x00, 0x08)
OP_COLOR = (0x03, 0x00)

BROADCAST = 0xFFFF

TRANSITION = (0x02, 0x00)

ROUTINE_PRESENCE_SIMULATOR = 1
ROUTINE_TIMER = 2
ROUTINE_PROGRAM = 3
ROUTINE_NIGHTLIGHT = 5
ROUTINE_DAWN_SIMULATOR = 7

_EPOCH_2000 = 946684800


def _percentage_to_value(percent: int, low: int, high: int) -> int:
    percent = max(0, min(100, percent))
    return low + round((high - low) * percent / 100)


def att_encrypt(key: bytes, value: bytes) -> bytes:
    """AES-ECB with both key and value byte-reversed (TelinkNative.aes_att_encryption).

    The key is zero-padded to 16 bytes -- during pairing it is only the 8-byte
    session random, matching ``Arrays.copyOf(x, 16)`` in the vendor app.
    """
    k = bytearray(key.ljust(16, b"\x00"))
    v = bytearray(value.ljust(16, b"\x00"))
    k.reverse()
    v.reverse()
    out = bytearray(AES.new(bytes(k), AES.MODE_ECB).encrypt(bytes(v)))
    out.reverse()
    return bytes(out)


def att_decrypt(key: bytes, value: bytes) -> bytes:
    k = bytearray(key.ljust(16, b"\x00"))
    v = bytearray(value.ljust(16, b"\x00"))
    k.reverse()
    v.reverse()
    out = bytearray(AES.new(bytes(k), AES.MODE_ECB).decrypt(bytes(v)))
    out.reverse()
    return bytes(out)


def crc8(data: bytes) -> int:
    """Reverse CRC-8, polynomial 0x6C, initial value 0."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x6C if crc & 1 else crc >> 1
    return crc


def combine_credentials(mesh_name: bytes, mesh_password: bytes) -> bytes:
    return bytes(a ^ b for a, b in
                 zip(mesh_name.ljust(16, b"\x00"), mesh_password.ljust(16, b"\x00"),
                     strict=False))


def make_pair_packet(mesh_name: bytes, mesh_password: bytes,
                     session_random: bytes) -> bytes:
    enc = att_encrypt(session_random, combine_credentials(mesh_name, mesh_password))
    return b"\x0c" + session_random + enc[:8]


def make_session_key(mesh_name: bytes, mesh_password: bytes,
                     session_random: bytes, response_random: bytes) -> bytes:
    return att_encrypt(combine_credentials(mesh_name, mesh_password),
                       session_random + response_random)


def build_command(session_key: bytes, dest: int, opcode, data: bytes) -> bytes:
    op_lo, op_hi = opcode
    buf = bytes([len(data) + 5, (dest >> 8) & 0xFF, dest & 0xFF, 0x01, op_hi, op_lo]) + data
    inner = bytes([crc8(buf)]) + buf
    flag = 1 if ((dest >> 8) & 0xFF) == 0x80 else 0

    head, tail = inner[:16], inner[16:]
    return bytes([flag]) + att_encrypt(session_key, head) + tail


def build_query(session_key: bytes, payload: bytes) -> bytes:
    buf = bytes([len(payload)]) + payload
    inner = bytes([crc8(buf)]) + buf
    return b"\x02" + att_encrypt(session_key, inner)


def build_status_query(session_key: bytes, dest: int = BROADCAST) -> bytes:
    return build_query(session_key, bytes([0x0A, dest & 0xFF, (dest >> 8) & 0xFF]))


def decode_packet(session_key: bytes, packet: bytes):
    if len(packet) < 17:
        return None
    block = att_decrypt(session_key, packet[1:17])
    if crc8(block[1:1 + block[1] + 1]) != block[0]:
        return None
    return packet[0], block, packet[17:]


def set_time(session_key: bytes, unix_time: float | None = None,
             utc_offset_seconds: int | None = None) -> bytes:
    """Set the lamp's clock.

    Schedules do not fire at the right time until the clock has been set. The
    payload is seconds since 2000-01-01 UTC plus the timezone offset, both as
    32-bit little-endian.
    """
    import time as _time

    if unix_time is None:
        unix_time = _time.time()
    if utc_offset_seconds is None:
        utc_offset_seconds = -(_time.altzone if _time.daylight and _time.localtime().tm_isdst
                               else _time.timezone)

    seconds = int(unix_time) - _EPOCH_2000
    payload = (bytes([48])
               + seconds.to_bytes(4, "little", signed=False)
               + (utc_offset_seconds & 0xFFFFFFFF).to_bytes(4, "little"))
    return build_query(session_key, payload)


def get_schedule(session_key: bytes, dest: int, routine_id: int) -> bytes:
    return build_query(session_key,
                       bytes([53, dest & 0xFF, (dest >> 8) & 0xFF, routine_id & 0xFF]))


def build_schedule(session_key: bytes, dest: int, *, routine_id: int,
                   hour: int, minute: int, enabled: bool = True,
                   fade_duration: int = 30, brightness: int | None = 100,
                   weekdays: int = 0x7F, recurrence: bool = True,
                   mode_onoff: bool = True, mode_lighting: int = 0,
                   temperature: int = 0, hue: int = 0, saturation: int = 0,
                   candle: bool = False, current: int = 1, total: int = 0) -> bytes:
    """Write a schedule into the lamp.

    It then runs autonomously: the lamp has its own clock, so neither server nor
    phone has to stay online.

    ``mode_lighting``: 0 = white temperature (``temperature`` in percent),
    1 = colour (``hue`` 0..254, ``saturation`` 0..254). ``brightness`` None
    leaves it unchanged. ``weekdays`` is a 7-bit mask; 0x7F = every day.

    ``current``/``total`` are the multi-part counters. For a single schedule
    current=1 and total=0 -- with total=1 the lamp silently drops the write.
    """
    body = bytearray(12)
    body[0] = dest & 0xFF
    body[1] = (dest >> 8) & 0xFF
    body[2] = (routine_id & 0x7F) | ((1 if enabled else 0) << 7)

    body[3] = (1 & 0x03) | ((minute & 0x3F) << 2)
    body[4] = hour & 0x1F

    body[5] = (weekdays & 0x7F) | ((1 if recurrence else 0) << 7)
    body[6] = fade_duration & 0x3F
    body[7] = ((1 if mode_onoff else 0)
               | ((1 if mode_lighting == 1 else 0) << 1)
               | ((1 if mode_lighting == 2 else 0) << 2)
               | ((1 if candle else 0) << 4))
    body[8] = 0xFF if brightness is None else (brightness & 0xFF)

    if mode_lighting == 1:
        body[9] = hue & 0xFF
        body[10] = saturation & 0xFF
    elif mode_lighting == 0:
        mired = _percentage_to_value(temperature, 153, 454)
        body[9] = mired & 0xFF
        body[10] = (mired >> 8) & 0xFF
    else:
        body[9] = body[10] = 0xFF

    body[11] = (current & 0x0F) | ((total & 0x0F) << 4)

    return build_query(session_key, bytes([51]) + bytes(body))


def set_dawn_simulator(session_key: bytes, dest: int, hour: int, minute: int, *,
                       fade_minutes: int = 30, brightness: int = 100,
                       temperature: int = 0, weekdays: int = 0x7F,
                       enabled: bool = True) -> bytes:
    return build_schedule(session_key, dest,
                          routine_id=ROUTINE_DAWN_SIMULATOR,
                          hour=hour, minute=minute, enabled=enabled,
                          fade_duration=fade_minutes, brightness=brightness,
                          weekdays=weekdays, mode_onoff=True,
                          mode_lighting=0, temperature=temperature)


def set_power(session_key: bytes, dest: int, on: bool) -> bytes:
    return build_command(session_key, dest, OP_POWER, bytes([1 if on else 0]))


def set_brightness(session_key: bytes, dest: int, percent: int) -> bytes:
    """Brightness 0..100 %. Data field as in the vendor app: [4][value][transition][0][0][0]."""
    value = _percentage_to_value(percent, 2, 254)
    return build_command(session_key, dest, OP_BRIGHTNESS,
                         bytes([4, value, TRANSITION[0], TRANSITION[1], 0, 0, 0]))


def set_white_temperature(session_key: bytes, dest: int, percent: int) -> bytes:
    """White temperature 0..100 % (maps to 153..454 mired)."""
    mired = _percentage_to_value(percent, 153, 454)
    return build_command(session_key, dest, OP_COLOR,
                         bytes([10, mired & 0xFF, (mired >> 8) & 0xFF,
                                TRANSITION[0], TRANSITION[1]]))


def set_color_hs(session_key: bytes, dest: int, hue_degrees: float,
                 saturation_percent: float) -> bytes:
    hue = int(hue_degrees * 254.0 / 360.0) & 0xFF
    sat = int(saturation_percent * 254.0 / 100.0) & 0xFF
    return build_command(session_key, dest, OP_COLOR,
                         bytes([6, hue, sat, TRANSITION[0], TRANSITION[1]]))


def set_color_rgb(session_key: bytes, dest: int, red: int, green: int, blue: int) -> bytes:
    """Convenience: convert RGB to HSV -- the device only knows hue/saturation."""
    h, _l, s = colorsys.rgb_to_hls(red / 255.0, green / 255.0, blue / 255.0)
    return set_color_hs(session_key, dest, h * 360.0, s * 100.0)


def set_sequence(session_key: bytes, dest: int, sequence_id: int,
                 duration_ms: int = 3072) -> bytes:
    """Start a built-in colour sequence.

    The lamp then plays it on its own -- no continuous Bluetooth traffic needed.
    ``duration_ms`` is the time per colour; the vendor app sends a fixed 3072 ms.
    """
    lo, hi = duration_ms & 0xFF, (duration_ms >> 8) & 0xFF
    return build_command(session_key, dest, OP_COLOR,
                         bytes([sequence_id & 0xFF, 1, lo, hi]))


def stop_sequence(session_key: bytes, dest: int) -> bytes:
    return build_command(session_key, dest, OP_COLOR, bytes([71, 0, 0, 0]))


def set_candle_mode(session_key: bytes, dest: int, level: int = 1) -> bytes:
    """Candle flicker -- shares its opcode with brightness."""
    return build_command(session_key, dest, OP_BRIGHTNESS,
                         bytes([0x10, level & 0xFF, 1]))


def decode_schedule(block: bytes) -> dict:
    mired = block[9] | (block[10] << 8)
    return {
        "mesh_id": int.from_bytes(block[0:2], "little"),
        "routine_id": block[2] & 0x7F,
        "enabled": bool(block[2] & 0x80),
        "minute": (block[3] >> 2) & 0x3F,
        "hour": block[4] & 0x1F,
        "weekdays": block[5] & 0x7F,
        "recurrence": bool(block[5] & 0x80),
        "fade_duration": block[6] & 0x3F,
        "mode_onoff": bool(block[7] & 0x01),
        "candle": bool(block[7] & 0x10),
        "brightness": None if block[8] == 0xFF else block[8],
        "mired": mired,
        "raw": block.hex(),
    }


def decode_status(block: bytes) -> dict:
    """Decode a decrypted status block.

    Layout: ``[0:2]`` mesh id (LE), ``[5:7]`` product code, ``[7:11]`` the last
    four bytes of the device MAC in reverse, ``[12]`` state and mode.

    Byte 12 is not a plain 0/1: bit 0 is on/off, the remaining bits encode the
    mode (sequence running, candle, ...). Use ``& 1`` -- comparing the whole byte
    against 1 reports a lamp as off while a colour sequence plays.
    """
    return {
        "mesh_id": int.from_bytes(block[0:2], "little"),
        "product": block[5:7].hex(),
        "mac_tail": ":".join(f"{byte:02X}" for byte in reversed(block[7:11])),
        "mode": block[12],
        "state": bool(block[12] & 1),
        "raw": block.hex(),
    }
