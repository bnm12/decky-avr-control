import asyncio
import unittest
from unittest import mock

import tests  # noqa: F401  (puts py_modules on sys.path)

from avr_control import drivers
from avr_control.drivers.base import Found


def _driver(key, items):
    class Fake:
        pass

    Fake.key = key

    async def discover(timeout):
        return items

    Fake.discover = staticmethod(discover)
    return Fake


class DiscoverAllTests(unittest.TestCase):
    def test_brand_driver_wins_over_generic_driver_for_the_same_host(self):
        brand = _driver("denon", [Found("denon", "AVR-X1700H", "SERIAL1", "10.0.0.5")])
        generic = _driver(
            "upnp",
            [
                Found("upnp", "Denon AVR-X1700H", "uuid:abc", "10.0.0.5"),
                Found("upnp", "Sonos One", "uuid:RINCON_1", "10.0.0.9"),
            ],
        )
        with mock.patch.object(drivers, "REGISTRY", [brand, generic]):
            found = asyncio.run(drivers.discover_all(0.1))
        self.assertEqual([(f.driver, f.host) for f in found], [("denon", "10.0.0.5"), ("upnp", "10.0.0.9")])

    def test_one_driver_keeps_several_devices_on_one_host(self):
        generic = _driver(
            "upnp",
            [
                Found("upnp", "Living Room", "uuid:lr", "10.0.0.7"),
                Found("upnp", "Kitchen", "uuid:k", "10.0.0.7"),
            ],
        )
        with mock.patch.object(drivers, "REGISTRY", [generic]):
            found = asyncio.run(drivers.discover_all(0.1))
        self.assertEqual([f.id for f in found], ["uuid:lr", "uuid:k"])

    def test_failing_driver_discovery_is_ignored(self):
        class Broken:
            key = "broken"

            @staticmethod
            async def discover(timeout):
                raise OSError("no network")

        ok = _driver("eiscp", [Found("eiscp", "VSX-935", "MAC", "10.0.0.2")])
        with mock.patch.object(drivers, "REGISTRY", [Broken, ok]):
            found = asyncio.run(drivers.discover_all(0.1))
        self.assertEqual([f.id for f in found], ["MAC"])


if __name__ == "__main__":
    unittest.main()
