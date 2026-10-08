# Hardware notes: AwoX/EGLO BLE mesh lamps

Everything here is about the physical device, not the protocol. It is the part
that turns "my code is correct" into "and yet nothing happens".

Tested on `EBF_RGB_Zm`, firmware `3.0.1_1593`.

## One connection at a time

The chip accepts exactly one connection and **stops advertising while one is
open**. Consequences:

- Close the vendor app before using this. An open app connection means your
  connect attempts simply never see an advertisement.
- Concurrent clients are pointless — a second one produces timeouts, not
  contention.
- After an aborted GATT access the lamp can stop advertising altogether. Then
  only a power cycle at the wall switch helps (off for ~10 s).

## Advertisements are rare

Measured: one advertisement per ~25 s. Scanning for a fixed short window usually
fails.

Two mitigations, both implemented:

- Scan for a long time (`AWOX_ADV_TIMEOUT`, default 600 s).
- Prefer BlueZ **Direct Connect** when `bluetoothd` already knows the device: the
  controller keeps a connection window open itself and grabs the lamp whenever it
  advertises. This is the same mechanism that lets the phone app connect
  instantly.

## The BR/EDR trap

Most frequent failure: `br-connection-canceled`. BlueZ has Bluetooth Classic
recorded in the device entry, so every LE connect attempt is cancelled. The
bleak backend detects this and removes the device record automatically; manually:

```bash
bluetoothctl remove <MAC>
```

## No CCCD — notifications are only reachable via gatttool

The status characteristic (`…1911`, handle `0x12`) has **no** `0x2902` client
configuration descriptor — only a user-description descriptor.

Consequence: BlueZ `StartNotify()` fails with `Unlikely Error`, and the lamp then
drops the connection and stays silent for 20–30 minutes. Notifications are
therefore unreachable through bleak in practice, but they *are* reachable through
`gatttool`, which is why this project ships a gatttool backend and why `/state`
and schedule reads require `AWOX_BACKEND=gatt`.

If you enable `AWOX_NOTIFICATIONS=1` with the bleak backend, the code tries it
anyway and downgrades gracefully: control keeps working, `/state` stays empty.

## Command pacing

Faster than roughly **0.3 s per command** and the lamp swallows commands; under
sustained load it throws the link away (measured: dropped after 68 and after 97
commands at an 80 ms interval). The light show enforces a 0.34 s minimum gap.

A consequence worth knowing: a too-fast strobe sometimes simply stays on, because
the off commands were dropped.

## Built-in sequences and schedules are the cheap path

Because the lamp keeps its own clock and can play a colour sequence by itself,
both features run **without** any ongoing Bluetooth traffic:

- Start a sequence once instead of streaming colours per second.
- Schedules (including the dawn alarm) fire with the phone switched off, the
  bridge stopped and Bluetooth unused.

For long-running effects this is not just convenient — it is the only pacing that
does not wear the chip out.

## Adapter problems look like lamp problems

The most confusing failure is not a lamp at all:

- On some kernels (7.2.x observed) the USB Bluetooth dongle resets during
  extended scanning and comes back under a **different** `hci` index.
  `No route to host (113)`, `connected:false` on a healthy chip, and "the laptop
  won't connect to my headphones" are all symptoms.
- The fix is a kernel that does not regress here (7.1.8 was reliable), not
  unplugging the dongle.
- Re-enumerating the adapter helps the *adapter*, never a lamp that has genuinely
  hung.

If you suspect the adapter, check `hci` indexes and kernel version first, and
only then look at the lamp.

## Pairing alternates accept/reject

Freshly accepted pairing (`0x0d`) blocks the attempt immediately after it: the
next round comes back `0x0e` carrying the same device random as the success. The
rejected round clears the session and the attempt after that goes through again —
a strict accepted → rejected → accepted pattern.

This is **not** a credentials problem (those are proven correct): the lamp keeps
its active session until a pairing attempt resolves it. Since the BLE link is
often already dead, every command pays the reconnect cost and then hits the
accept or reject round. The code handles this by retrying **exactly once** with a
fresh session after a `0x0e`.

## Two failure signatures on `0x0e`

- `Pair-Antwort [1]: 0e…` — the alternating mode described above; a 5 s pause is
  enough.
- **No** reply at all on the pairing characteristic — the chip is stuck. Rest
  first, then power-cycle at the wall switch.

The log line that reports a rejected pairing is written unconditionally in round
one; only the part after the `--` is the diagnosis.

## Identifying the model

The BLE name, the product code in the status block and the manufacturer data
identify the lamp, but **not** the mesh address — that has to come from a status
block, i.e. from a working connection. Expect to set `AWOX_MESH_ID` once per
lamp.