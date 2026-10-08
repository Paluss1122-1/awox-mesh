"""Tests for the pure protocol layer -- no Bluetooth required."""

import pytest
from Crypto.Cipher import AES

from awox_mesh import protocol as ap

# Placeholder credentials only. Never real ones: they stay in the git history.
MESH_NAME = b"meshname"
MESH_PASSWORD = b"meshpass"
SESSION_RANDOM = bytes.fromhex("0001020304050607")
RESPONSE_RANDOM = bytes.fromhex("08090a0b0c0d0e0f")
SESSION_KEY = bytes(range(16))
DEST = 0x1234


def reference_att_encrypt(key: bytes, value: bytes) -> bytes:
    """Straight AES-ECB with both operands byte-reversed, written out by hand."""
    k = bytes(reversed(bytes(key).ljust(16, b"\x00")))
    v = bytes(reversed(bytes(value).ljust(16, b"\x00")))
    return bytes(reversed(AES.new(k, AES.MODE_ECB).encrypt(v)))


def reference_crc8(data: bytes) -> int:
    """Bit-by-bit reference CRC-8, reverse direction, polynomial 0x6C."""
    crc = 0
    for byte in data:
        for _bit in range(8):
            mix = (crc ^ byte) & 1
            crc >>= 1
            if mix:
                crc ^= 0x6C
            byte >>= 1
    return crc


def test_att_encrypt_matches_reference():
    assert ap.att_encrypt(SESSION_KEY, b"hello") == reference_att_encrypt(
        SESSION_KEY, b"hello")


def test_att_encrypt_roundtrip():
    payload = b"0123456789abcdef"
    assert ap.att_decrypt(SESSION_KEY, ap.att_encrypt(SESSION_KEY, payload)) == payload


def test_crc8_matches_reference():
    for data in (b"", b"\x00", b"\xff", b"\x0c\x29\xbf\x62", bytes(range(16))):
        assert ap.crc8(data) == reference_crc8(data)


def test_pair_packet_layout():
    packet = ap.make_pair_packet(MESH_NAME, MESH_PASSWORD, SESSION_RANDOM)
    assert packet[0] == 0x0C
    assert packet[1:9] == SESSION_RANDOM
    assert len(packet) == 17
    # The encrypted half is exactly what an independent AES-ECB call produces.
    expected = reference_att_encrypt(
        SESSION_RANDOM, ap.combine_credentials(MESH_NAME, MESH_PASSWORD))[:8]
    assert packet[9:17] == expected


def test_pair_packet_is_deterministic():
    first = ap.make_pair_packet(MESH_NAME, MESH_PASSWORD, SESSION_RANDOM)
    second = ap.make_pair_packet(MESH_NAME, MESH_PASSWORD, SESSION_RANDOM)
    assert first == second


def test_session_key_derivation_is_deterministic_and_16_bytes():
    key = ap.make_session_key(MESH_NAME, MESH_PASSWORD, SESSION_RANDOM,
                              RESPONSE_RANDOM)
    assert len(key) == 16
    assert key == ap.make_session_key(MESH_NAME, MESH_PASSWORD, SESSION_RANDOM,
                                      RESPONSE_RANDOM)
    assert key != ap.make_session_key(MESH_NAME, b"other", SESSION_RANDOM,
                                      RESPONSE_RANDOM)


def test_set_power_packet_shape():
    packet = ap.set_power(SESSION_KEY, DEST, True)
    assert packet[0] == 0x00  # plaintext flag byte
    block = ap.att_decrypt(SESSION_KEY, packet[1:17])
    assert ap.crc8(block[1:1 + block[1] + 1]) == block[0]
    assert block[1] == 6  # len(data) + 5
    assert block[2:4] == DEST.to_bytes(2, "big")
    assert block[4] == 0x01
    assert block[5:7] == bytes([0x06, 0x00])  # opcode {0, 6}, emitted high byte first
    assert block[7] == 0x01  # the on/off value


def test_set_brightness_maps_percent_to_range():
    def value(percent):
        packet = ap.set_brightness(SESSION_KEY, DEST, percent)
        block = ap.att_decrypt(SESSION_KEY, packet[1:17])
        return block[8]

    assert value(0) == 2
    assert value(100) == 254
    assert value(50) == 128


def test_set_white_temperature_maps_to_mired():
    def mired(percent):
        packet = ap.set_white_temperature(SESSION_KEY, DEST, percent)
        block = ap.att_decrypt(SESSION_KEY, packet[1:17])
        return block[8] | (block[8 + 1] << 8)

    assert mired(100) == 454
    assert mired(0) == 153


def test_set_color_rgb_converts_to_hue_saturation():
    packet = ap.set_color_rgb(SESSION_KEY, DEST, 255, 0, 0)
    block = ap.att_decrypt(SESSION_KEY, packet[1:17])
    hue, sat = block[8], block[9]
    assert sat == 254  # fully saturated red
    assert hue == 0  # red sits at the hue wrap-around


def test_build_status_query_uses_broadcast_by_default():
    packet = ap.build_status_query(SESSION_KEY)
    assert packet[0] == 0x02
    block = ap.att_decrypt(SESSION_KEY, packet[1:17])
    assert block[2] == 0x0A
    assert block[3:5] == b"\xff\xff"


def test_decode_packet_validates_crc():
    packet = ap.set_power(SESSION_KEY, DEST, True)
    flag, block, rest = ap.decode_packet(SESSION_KEY, packet)
    assert flag == 0x00
    assert block[1] == 6
    assert rest == b""
    assert ap.decode_packet(SESSION_KEY, packet[:-1]) is None
    corrupted = bytes([packet[0]]) + bytes([packet[1] ^ 0xFF]) + packet[2:]
    assert ap.decode_packet(SESSION_KEY, corrupted) is None


def test_flag_byte_set_for_high_address():
    packet = ap.build_command(SESSION_KEY, 0x8000, ap.OP_POWER, b"\x01")
    assert packet[0] == 0x01


@pytest.mark.parametrize("percent,expected", [
    (-10, 2), (0, 2), (50, 128), (100, 254), (150, 254),
])
def test_brightness_percent_is_clamped_to_the_device_range(percent, expected):
    packet = ap.set_brightness(SESSION_KEY, DEST, percent)
    block = ap.att_decrypt(SESSION_KEY, packet[1:17])
    assert block[8] == expected


def test_decode_status_reads_mesh_id_state_and_mac_tail():
    block = bytes.fromhex("3412000000b02075915a380101fe9900")
    status = ap.decode_status(block)
    assert status["mesh_id"] == 0x1234
    assert status["state"] is True
    assert status["product"] == "b020"
    assert status["mac_tail"] == "38:5A:91:75"
    assert status["raw"] == block.hex()


def test_decode_status_uses_only_the_low_bit_for_state():
    # byte 12 = 0x03 means "on" while a mode (e.g. a sequence) is active.
    block = bytes.fromhex("3412000000b02075915a380003fe9900")
    status = ap.decode_status(block)
    assert status["mode"] == 0x03
    assert status["state"] is True
    block_off = bytes.fromhex("3412000000b02075915a380002fe9900")
    assert ap.decode_status(block_off)["state"] is False