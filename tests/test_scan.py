"""Tests for the AwoX OUI filter used by ``awox scan``."""

import pytest

from awox_mesh.scan import is_awox_address


@pytest.mark.parametrize("address", [
    "A4:C1:38:11:22:33",
    "a4:c1:38:aa:bb:cc",
    "A4-C1-38-00-11-22",
])
def test_matches_the_telink_oui(address):
    assert is_awox_address(address)


@pytest.mark.parametrize("address", [
    "",
    "00:11:22:33:44:55",
    "A0:AD:9F:00:00:01",
    "A4:C1:39:00:00:00",
])
def test_rejects_other_devices(address):
    assert not is_awox_address(address)
