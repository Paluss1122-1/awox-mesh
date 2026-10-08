# The AwoX/EGLO BLE mesh protocol (non-Telink variant)

This document records how the command format was derived and what the frames
look like. It is written from black-box observation plus the *structure* of the
vendor app. No vendor code, APK or binary is reproduced or shipped here; the
algorithm is described in prose so it can be reimplemented independently.

Tested device: model `EBF_RGB_Zm`, firmware `3.0.1_1593`, hardware revision
`4.60`, manufacturer AwoX. All packet examples below use **placeholder**
credentials and a placeholder session random.

## TL;DR

The pairing handshake is the same one every other AwoX library implements. The
command framing is different, and that difference is the whole reason this
firmware looks unsupported:

| | classic Telink | this firmware |
|---|---|---|
| cipher mode | CCM-like, custom | plain AES-ECB |
| nonce / counter | yes, per packet | none |
| packet length | 20 bytes | 17–20 bytes |
| first byte | packet counter | plaintext type flag |
| repeat of same command | different bytes | **byte-identical** |

The last row is the giveaway. A recorded status query repeated verbatim, three
times, unchanged — a nonce/counter scheme cannot do that.

## Pairing

The pairing characteristic is `…1914` (handle `0x1e` on this firmware; the
classic library hardcodes `0x1b`, which is FOTA here — resolve handles by UUID).

```
->  0x0c | session_random(8) | AES-ECB(session_random, name XOR password)[0:8]
<-  0x0d | response_random(8)                    (0x0e = credentials rejected)
```

Both the key and the plaintext are byte-reversed before and after the AES call,
which mirrors `Arrays.copyOf(x, 16)` padding in the app: the session random is
only 8 bytes and gets zero-padded to 16.

```python
def combine_credentials(name: bytes, password: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(name.ljust(16), password.ljust(16)))

def att_encrypt(key: bytes, value: bytes) -> bytes:
    k = reversed(key.ljust(16, b"\x00"))
    v = reversed(value.ljust(16, b"\x00"))
    return reversed(AES.new(k, AES.MODE_ECB).encrypt(v))

packet = b"\x0c" + session_random + att_encrypt(session_random, combine_credentials(name, password))[:8]
session_key = att_encrypt(combine_credentials(name, password), session_random + response_random)
```

This part is identical to
[python-awox-mesh-light](https://github.com/Leiaz/python-awox-mesh-light) and
[fsaris/EspHome-AwoX-BLE-mesh-hub](https://github.com/fsaris/EspHome-AwoX-BLE-mesh-hub),
both of which were cross-checked line by line against this implementation.

## Command frames

```python
def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x6C if crc & 1 else crc >> 1
    return crc

def build_command(session_key, dest, opcode, data):
    buf = bytes([len(data) + 5, (dest >> 8) & 0xFF, dest & 0xFF,
                 0x01, opcode[1], opcode[0]]) + data
    inner = bytes([crc8(buf)]) + buf
    flag = 1 if ((dest >> 8) & 0xFF) == 0x80 else 0
    head, tail = inner[:16], inner[16:]
    return bytes([flag]) + att_encrypt(session_key, head) + tail
```

Only the first 16 bytes of the plaintext are encrypted; anything beyond byte 16
(possible for long payloads such as the clock frame) is appended in the clear.

The type flag is plaintext: `0x00` for a control command, `0x02` for a query.

## Opcodes

| Operation | Opcode | Data field |
|---|---|---|
| Power | `{0, 6}` | `[on_off]` |
| Brightness | `{0, 8}` | `[4][value][0x02][0x00][0][0][0]`, value = percent → 2…254 |
| Colour / white temperature | `{3, 0}` | `[6][hue][sat][0x02][0x00]` |
| Colour / white temperature | `{3, 0}` | `[10][mired_lo][mired_hi][0x02][0x00]`, mired = percent → 153…454 |
| Start sequence | `{3, 0}` | `[sequence][1][dur_lo][dur_hi]` |
| Stop sequence | `{3, 0}` | `[71][0][0][0]` |
| Candle | `{0, 8}` | `[0x10][level][1]` |

Brightness, colour and white temperature are percentages on the API side; the
device only knows its native ranges, hence the mapping. Opcodes are emitted high
byte first. Note that candle shares its opcode with brightness, and colour,
white temperature and sequences all share one opcode — the first data byte
selects the variant.

## The mesh address

Control commands go to the lamp's **real** mesh address (e.g. `0x1234` = 4660).
Not `0`, and not the broadcast address `0xFFFF` — only status queries use
broadcast. There is no way to guess this address by scanning a small range: a
sweep of 0–255 would never have found it. It appears in the first two bytes
(little endian) of the decrypted status block.

## Query and schedule frames

Queries use a second frame type, flagged `0x02`, with a shorter header (no
destination inside the block):

```
[CRC8][len][opcode][payload...]   padded to 16, then AES-ECB
```

| Operation | Opcode |
|---|---|
| Status query | `0x0a` |
| Set clock | `0x30` |
| Write schedule | `0x33` |
| Read schedule | `0x35` |

The set-clock payload is seconds since 2000-01-01 UTC plus the UTC offset,
both 32-bit little endian. Schedules do not fire at the right time until the
clock has been set — the bridge does this on every connect.

A schedule body is 12 bytes:

```python
body[0:2]  mesh address (little endian)
body[2]    routine id (bits 0-6) | enabled (bit 7)
body[3]    recurrence bits (0-1) | minute (bits 2-7)
body[4]    hour (5 bits)
body[5]    weekday mask (bits 0-6) | recurrence (bit 7)
body[6]    fade duration (6 bits)
body[7]    on/off | lighting mode (bits 1-2) | candle (bit 4)
body[8]    brightness, or 0xff to leave unchanged
body[9:11] mired (white) or hue + saturation (colour), little endian
body[11]   multi-part counters: current (bits 0-3) | total (bits 4-7)
```

Two traps worth writing down:

- For a **single** schedule the counters must be `current=1, total=0`. With
  `total=1` the lamp silently discards the write — no error, nothing happens.
- `lighting mode` selects what `body[9:11]` means: `0` = mired, `1` = hue and
  saturation, `2` = neither.

Routine ids seen so far: `1` presence simulator, `2` timer, `3` program,
`5` nightlight, `7` dawn simulator.

## Status blocks

Decrypted status notifications look like this:

```
[0:2] mesh id (LE)   [5:7] product code   [7:11] MAC reversed   [12] state/mode
```

Byte 12 is **not** a plain 0/1. Bit 0 is on/off, the remaining bits encode the
mode (sequence running, candle, …). Comparing the whole byte against `1` reports
a lamp as *off* while a colour sequence is playing. Use `byte & 1`.

```python
on = bool(block[12] & 1)
```

Status messages arrive unsolicited as soon as a valid command arrives — there is
no notification subscription to set up. See `docs/hardware.md` for why you still
cannot subscribe normally.

## How the mesh address was found

Worth recording, because the obvious approaches all fail:

- A sweep over mesh addresses 0–255 misses it outright.
- The advertisement manufacturer data is byte-identical across every
  measurement and carries no state — useless as a channel.
- Decrypting the recorded control commands with the derived session key fails
  everywhere: no checksum matches.
- A search over roughly 15 000 parameterisations (four key derivations, both MAC
  directions, MAC lengths 3–6, three nonce prefixes, five sequence positions,
  sequence lengths 1–4, payload offsets 1–8) found no pair of sessions that
  decrypt to a common plaintext.

What settled it: the session key is derived correctly (the pairing packet
reproduces the recorded bytes exactly), and the *same* plaintext yields
*different* ciphertext in different sessions while staying byte-identical within
a session. Session-bound, counter-free. Combined with the 17-byte length and the
constant first byte across sessions, that leaves exactly one shape — the one
implemented in `awox_mesh/protocol.py`.

## Provenance and scope

Derived from an HCI snoop capture of the vendor app plus black-box probing of
one lamp. The pairing scheme was verified to be identical to the established
open implementations; the command framing was not, which is the finding that
matters. No vendor binary, decompiled source or APK is included in this
repository. The reverse engineering was done for interoperability: controlling
one's own hardware without a cloud account.