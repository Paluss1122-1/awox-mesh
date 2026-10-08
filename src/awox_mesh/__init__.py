"""awox-mesh -- control AwoX/EGLO BLE mesh lamps without the cloud.

    from awox_mesh import protocol
    from awox_mesh.gatt import GattConnection

    mesh = GattConnection(mac, name, password, mesh_id=0x1234)
    await mesh.send(lambda key, mid: protocol.set_power(key, mid, True))

Only ``protocol`` is imported here; the BLE transports and the bridge are
imported lazily so the protocol layer works without a Bluetooth stack.
"""

__version__ = "1.2.1"

from . import protocol

__all__ = ["protocol", "__version__"]
