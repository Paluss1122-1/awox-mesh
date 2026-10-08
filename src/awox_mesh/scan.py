"""Find AwoX/EGLO BLE lamps around you.

Every AwoX lamp uses the Telink OUI ``A4:C1:38``. Scanning for that prefix is
how you get the MAC for ``config.env`` without guessing -- and, because several
lamps of one mesh share the same BLE name, the MAC is the only thing that tells
them apart.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

AWOX_OUI = "A4:C1:38"


def is_awox_address(address: str) -> bool:
    return address.upper().replace("-", ":").startswith(AWOX_OUI)


async def scan(timeout: float = 30.0) -> list[dict]:
    """Return the AwoX lamps seen within ``timeout`` seconds.

    Uses bleak's discovery. The lamps advertise rarely (about every 25 s), so a
    short window can legitimately miss them.
    """
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    lamps = []
    for address, (device, adv) in found.items():
        if not is_awox_address(address):
            continue
        lamps.append({
            "address": address.upper(),
            "name": adv.local_name or device.name or "",
            "rssi": adv.rssi,
        })
    lamps.sort(key=lambda lamp: lamp["rssi"], reverse=True)
    return lamps
