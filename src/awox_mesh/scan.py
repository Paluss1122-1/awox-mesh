"""Find AwoX/EGLO lamps advertising nearby (Telink OUI A4:C1:38)."""

from __future__ import annotations

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
