from __future__ import annotations

import asyncio
import unittest

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeEiscp, FakeEiscpDiscovery, FakePioneerTelnet
from avr_control.drivers.base import MODE, MUTE, POWER, VOLUME, Refused, Unsupported
from avr_control.drivers.eiscp import EiscpDriver
from avr_control.drivers import eiscp as eiscp_module
from avr_control.drivers.pioneer_telnet import PioneerTelnetDriver


class EiscpDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeEiscp()
        self.port = await self.fake.start()
        self.driver = EiscpDriver({"host": "127.0.0.1", "port": self.port})

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_capabilities_status_and_setters_ignore_unsolicited_push(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE, POWER, MODE})
        status = await self.driver.status()
        self.assertEqual(
            (status.volume, status.muted, status.power, status.mode),
            (48, False, True, "00"),
        )
        self.assertEqual(await self.driver.set_volume(70), 70)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertFalse(await self.driver.set_power(False))
        self.assertEqual(await self.driver.set_mode("11"), "11")
        self.assertEqual(self.fake.volume, 70)
        self.assertTrue(self.fake.muted)
        self.assertFalse(self.fake.power)
        self.assertEqual(self.fake.mode, "11")

    async def test_standby_reports_power_without_optional_fields(self):
        self.fake.standby = True
        self.fake.power = False
        status = await self.driver.status()
        self.assertEqual(status.power, False)
        self.assertIsNone(status.volume)
        self.assertIsNone(status.muted)
        self.assertIsNone(status.mode)

    async def test_powered_receiver_waits_for_delayed_volume_and_mute(self):
        self.fake.status_delay = 0.8

        status = await self.driver.status()

        self.assertEqual((status.volume, status.muted, status.power), (48, False, True))

    async def test_status_accepts_volume_and_mute_when_power_is_unknown(self):
        self.fake.report_power = False
        self.fake.status_delay = 0.1

        status = await self.driver.status()

        self.assertEqual((status.volume, status.muted, status.power), (48, False, None))

    async def test_refused_mode_and_probe_yes_no(self):
        self.fake.refused_mode = "80"
        with self.assertRaisesRegex(Refused, "didn't accept that mode"):
            await self.driver.set_mode("80")
        found = await EiscpDriver.probe({"host": "127.0.0.1", "port": self.port})
        self.assertIsNotNone(found)
        self.assertEqual(found.driver, "eiscp")
        self.assertIsNone(
            await EiscpDriver.probe({"host": "127.0.0.1", "port": self.port + 1})
        )

    async def test_discovery_parses_and_deduplicates_by_receiver_id(self):
        loop = asyncio.get_running_loop()
        transport, responder = await loop.create_datagram_endpoint(
            FakeEiscpDiscovery, local_addr=("127.0.0.1", 0)
        )
        old_targets = eiscp_module.DISCOVERY_TARGETS
        old_bind = eiscp_module.DISCOVERY_BIND_PORT
        old_port = eiscp_module.DISCOVERY_PORT
        try:
            eiscp_module.DISCOVERY_TARGETS = [
                ("127.0.0.1", transport.get_extra_info("sockname")[1])
            ]
            eiscp_module.DISCOVERY_BIND_PORT = 0
            eiscp_module.DISCOVERY_PORT = transport.get_extra_info("sockname")[1]
            found = await EiscpDriver.discover(0.35)
        finally:
            eiscp_module.DISCOVERY_TARGETS = old_targets
            eiscp_module.DISCOVERY_BIND_PORT = old_bind
            eiscp_module.DISCOVERY_PORT = old_port
            transport.close()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].model, "VSX-935")
        self.assertEqual(found[0].id, "0009B0617D30")
        self.assertEqual(found[0].config["port"], 60128)
        probes = [data[16:].decode() for data in responder.probes]
        self.assertTrue(any("!xECNQSTN" in probe for probe in probes))
        self.assertTrue(any("!pECNQSTN" in probe for probe in probes))


class PioneerTelnetDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakePioneerTelnet()
        self.port = await self.fake.start()
        self.driver = PioneerTelnetDriver({"host": "127.0.0.1", "port": self.port})

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_status_and_setters(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE, POWER})
        status = await self.driver.status()
        self.assertEqual(
            (status.volume, status.muted, status.power),
            (48, False, True),
        )
        self.assertEqual(await self.driver.set_volume(120), 120)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertFalse(await self.driver.set_power(False))
        self.assertEqual(self.fake.volume, 120)
        self.assertTrue(self.fake.muted)
        self.assertFalse(self.fake.power)
        with self.assertRaises(Unsupported):
            await self.driver.set_mode("00")

    async def test_probe_yes_and_no(self):
        found = await PioneerTelnetDriver.probe(
            {"host": "127.0.0.1", "port": self.port}
        )
        self.assertEqual(found.driver, "pioneer_telnet")
        self.assertEqual(found.config["port"], self.port)
        self.assertIsNone(
            await PioneerTelnetDriver.probe(
                {"host": "127.0.0.1", "port": self.port + 1}
            )
        )

    async def test_status_treats_pwr2_and_pwr1_as_standby(self):
        self.fake.power = False

        status = await self.driver.status()
        self.assertFalse(status.power)

        self.fake.off_power_reply = "1"
        status = await self.driver.status()
        self.assertFalse(status.power)


if __name__ == "__main__":
    unittest.main()
