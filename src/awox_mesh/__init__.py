"""awox-mesh -- control AwoX/EGLO BLE mesh lamps without the cloud.

Quick start::

    from awox_mesh import protocol, GattConnection

    mesh = GattConnection(mac, mesh_name, mesh_password, mesh_id=0x1234)
    await mesh.send(lambda key, mid: protocol.set_power(key, mid, True))

Heavy submodules (BLE transports, HTTP bridge) are imported lazily by the
factory in :mod:`awox_mesh.connection` so the pure ``protocol`` module can be
used without installing any Bluetooth stack.
"""

__version__ = "1.2.0"

from . import protocol

__all__ = ["protocol", "__version__"]