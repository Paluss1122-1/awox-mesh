"""Tests for schedule (dawn alarm) encoding and decoding."""

from awox_mesh import protocol as ap

SESSION_KEY = bytes(range(16))
DEST = 0x1234


def schedule_body(packet: bytes) -> bytes:
    """Decrypt a schedule packet and return the 12-byte body.

    ``build_schedule`` -> ``build_query`` puts a CRC byte and a length byte in
    front of ``[0x33, body...]``; the body itself starts with the mesh address.
    """
    block = ap.att_decrypt(SESSION_KEY, packet[1:17])
    payload = block[2:]
    assert payload[0] == 51  # schedule-write opcode
    return payload[1:]


def test_dawn_schedule_roundtrip():
    packet = ap.set_dawn_simulator(SESSION_KEY, DEST, 7, 30,
                                   fade_minutes=45, brightness=90,
                                   temperature=0, weekdays=0x7F)
    decoded = ap.decode_schedule(schedule_body(packet))
    assert decoded["mesh_id"] == DEST
    assert decoded["routine_id"] == ap.ROUTINE_DAWN_SIMULATOR
    assert decoded["enabled"] is True
    assert decoded["hour"] == 7
    assert decoded["minute"] == 30
    assert decoded["weekdays"] == 0x7F
    assert decoded["recurrence"] is True
    assert decoded["fade_duration"] == 45
    assert decoded["brightness"] == 90


def test_disabled_schedule_roundtrip():
    packet = ap.set_dawn_simulator(SESSION_KEY, DEST, 6, 0, enabled=False)
    decoded = ap.decode_schedule(schedule_body(packet))
    assert decoded["enabled"] is False


def test_weekday_mask_roundtrip():
    packet = ap.set_dawn_simulator(SESSION_KEY, DEST, 8, 15, weekdays=0b1010100)
    decoded = ap.decode_schedule(schedule_body(packet))
    assert decoded["weekdays"] == 0b1010100


def test_brightness_none_is_preserved_as_none():
    packet = ap.build_schedule(SESSION_KEY, DEST, routine_id=ap.ROUTINE_TIMER,
                               hour=6, minute=0, brightness=None)
    decoded = ap.decode_schedule(schedule_body(packet))
    assert decoded["brightness"] is None


def test_colour_mode_roundtrip():
    packet = ap.build_schedule(SESSION_KEY, DEST, routine_id=ap.ROUTINE_PROGRAM,
                               hour=20, minute=45, mode_lighting=1,
                               hue=120, saturation=200, brightness=60)
    decoded = ap.decode_schedule(schedule_body(packet))
    assert decoded["routine_id"] == ap.ROUTINE_PROGRAM
    assert decoded["hour"] == 20
    assert decoded["minute"] == 45
    assert decoded["brightness"] == 60


def test_multi_part_counter_encoding():
    packet = ap.build_schedule(SESSION_KEY, DEST, routine_id=ap.ROUTINE_TIMER,
                               hour=6, minute=0, current=1, total=0)
    body = schedule_body(packet)
    assert body[11] == 0x01  # single schedule -> current=1, total=0


def test_get_schedule_targets_the_address():
    packet = ap.get_schedule(SESSION_KEY, DEST, ap.ROUTINE_DAWN_SIMULATOR)
    assert packet[0] == 0x02
    block = ap.att_decrypt(SESSION_KEY, packet[1:17])
    assert block[2] == 53  # schedule-read opcode
    assert block[3:5] == DEST.to_bytes(2, "little")


def test_set_time_uses_epoch_2000():
    packet = ap.set_time(SESSION_KEY, unix_time=946684800 + 3600,
                         utc_offset_seconds=3600)
    block = ap.att_decrypt(SESSION_KEY, packet[1:17])
    payload = block[2:]
    assert payload[0] == 48  # set-clock opcode
    seconds = int.from_bytes(payload[1:5], "little")
    offset = int.from_bytes(payload[5:9], "little")
    assert seconds == 3600
    assert offset == 3600