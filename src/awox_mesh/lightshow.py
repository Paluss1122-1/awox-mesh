"""The light show sequence -- shared by the HTTP bridge and the CLI.

Runs the lamp once through every known function. The caller only provides the
``send`` callable and an optional progress callback; the whole sequence lives
here so server and CLI play the same show.
"""

from __future__ import annotations

import asyncio

from . import protocol as ap

MIN_PAUSE = 0.34


async def run_lightshow(send, on_step=None) -> None:
    """Send the whole show through ``send``.

    ``send(builder)`` takes a packet builder ``(session_key, mesh_id) -> packet``
    (as expected by the connection) and sends it; this function inserts the
    pauses with a minimum spacing of MIN_PAUSE.
    ``on_step(name)`` is called before each section (progress).
    """

    def color(rgb):
        return lambda k, m: ap.set_color_rgb(k, m, *rgb)

    def power(on):
        return lambda k, m: ap.set_power(k, m, on)

    async def pause(seconds):
        await asyncio.sleep(max(seconds, MIN_PAUSE))

    async def step(name, *commands):
        if on_step is not None:
            on_step(name)
        for builder, seconds in commands:
            await send(builder)
            await pause(seconds)

    await step(
        "1/8  Wake up (off/on)",
        (power(True), 0.7),
        *((power(on), 0.7) for _ in range(2) for on in (False, True)),
    )

    await step(
        "2/8  Breathing (brightness pulse)",
        (lambda k, m: ap.set_white_temperature(k, m, 0), 0.4),
        *((lambda k, m, p=level: ap.set_brightness(k, m, p), 0.45)
          for level in (100, 75, 50, 30, 15, 30, 50, 75, 100)),
    )

    await step(
        "3/8  White temperature warm -> cold -> warm",
        *((lambda k, m, p=level: ap.set_white_temperature(k, m, p), 0.8)
          for level in (0, 25, 50, 75, 100, 75, 50, 25, 0)),
    )

    await step(
        "4/8  Rainbow (2 rounds)",
        *((lambda k, m, h=hue: ap.set_color_hs(k, m, h, 100), 0.34)
          for _ in range(2) for hue in range(0, 360, 20)),
    )

    await step(
        "5/8  Colour chase (complementary)",
        *((color(rgb), 0.34)
          for rgb in ((255, 0, 0), (0, 255, 255), (0, 255, 0),
                      (255, 0, 255), (0, 0, 255), (255, 255, 0))
          for _ in range(2)),
    )

    breath = []
    for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        breath.append((color(rgb), 0.45))
        breath.extend((lambda k, m, p=p: ap.set_brightness(k, m, p), 0.45)
                      for p in (100, 40, 100))
    await step("6/8  Colour breathing (red, green, blue)", *breath)

    strobe = [(lambda k, m: ap.set_white_temperature(k, m, 0), 0.4)]
    strobe.extend((power(on), 0.34) for _ in range(5) for on in (True, False))
    for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        strobe.append((color(rgb), 0.34))
        strobe.extend((power(on), 0.34) for _ in range(3) for on in (True, False))
    await step("7/8  Strobe (warm white, red, green, blue)", *strobe)

    await step(
        "8/8  Finale (warm white)",
        (lambda k, m: ap.set_white_temperature(k, m, 0), 0.4),
        *((lambda k, m, p=p: ap.set_brightness(k, m, p), 0.45)
          for p in (30, 70, 100)),
        (power(False), 0.4),
        (power(True), 0.4),
    )
