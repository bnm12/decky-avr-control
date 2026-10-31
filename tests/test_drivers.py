from __future__ import annotations

import asyncio
import unittest

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeEiscp, FakeEiscpDiscovery, FakePioneerTelnet
from avr_control.drivers.base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    DriverError,
    ModeState,
    Refused,
    Status,
    Unsupported,
)
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

    async def test_mode_state_uses_measured_modes_without_offering_unknown_current(self):
        status = await self.driver.status()
        state = await self.driver.mode_state(status)
        self.assertEqual(state.current, "00")
        self.assertIn("00", [mode.code for mode in state.selectable])
        self.assertFalse(state.complete)

        self.fake.mode = "FE"
        status = await self.driver.status()
        state = await self.driver.mode_state(status)
        self.assertEqual(state.current, "FE")
        self.assertNotIn("FE", [mode.code for mode in state.selectable])
        self.assertFalse(state.complete)

        self.fake.power = False
        state = await self.driver.mode_state(await self.driver.status())
        self.assertEqual(state, ModeState(None, (), complete=False))

        async def unexpected_status():
            raise AssertionError("mode_state must reuse the supplied status")

        self.driver.status = unexpected_status
        self.assertEqual(
            await self.driver.mode_state(Status(mode="00", power=True)),
            ModeState("00", tuple(await self.driver.list_modes()), complete=False),
        )

    def test_mode_labels_do_not_require_a_configured_receiver(self):
        self.assertEqual(EiscpDriver.mode_label("00"), "Stereo")
        self.assertEqual(EiscpDriver.mode_label("fe"), "Mode fe")

    def test_scale_defaults_and_model_lookup(self):
        self.assertTrue(
            all(
                not field.required
                for field in EiscpDriver.config_fields
                if field.key in {"volume_steps", "zero_db", "max_volume", "volume_unit"}
            )
        )
        unknown = EiscpDriver({"model": "unlisted receiver"}).scale
        self.assertEqual(
            (unknown.min, unknown.max, unknown.unit, unknown.zero,
             unknown.steps_per_unit, unknown.default_cap),
            (0, 185, "dB", 164, 2, 100),
        )

        known = EiscpDriver({"model": "vsx-935"}).scale
        self.assertEqual(
            (known.zero, known.steps_per_unit, known.max, known.default_cap),
            (164, 2, 185, 100),
        )

    async def test_scale_overrides_and_set_volume_clamps_to_configured_max(self):
        driver = EiscpDriver(
            {
                "host": "127.0.0.1",
                "port": self.port,
                "model": "other receiver",
                "volume_steps": "1",
                "zero_db": "80",
                "max_volume": "100",
            }
        )
        self.assertEqual(
            (
                driver.scale.unit,
                driver.scale.zero,
                driver.scale.steps_per_unit,
                driver.scale.max,
                driver.scale.default_cap,
            ),
            ("dB", 80, 1, 100, 48),
        )
        self.assertEqual(await driver.set_volume(200), 100)
        self.assertEqual(self.fake.volume, 100)

    def test_absolute_scale_defaults_to_steps_and_requires_explicit_percent(self):
        scale = EiscpDriver(
            {"zero_db": "", "max_volume": "100"}
        ).scale
        scale_with_omitted_zero = EiscpDriver({"max_volume": "100"}).scale
        scale_with_absolute_sentinel = EiscpDriver(
            {"zero_db": "absolute", "max_volume": "100"}
        ).scale
        percent_scale = EiscpDriver(
            {"zero_db": "", "max_volume": "100", "volume_unit": "%"}
        ).scale
        non_100_steps = EiscpDriver(
            {"zero_db": "absolute", "max_volume": "80"}
        ).scale
        self.assertEqual(
            (scale.unit, scale.zero, scale.steps_per_unit, scale.max, scale.default_cap),
            ("steps", 0, 1, 100, 40),
        )
        self.assertEqual(scale_with_omitted_zero.unit, "steps")
        self.assertEqual(scale_with_absolute_sentinel.unit, "steps")
        self.assertEqual(percent_scale.unit, "%")
        self.assertEqual(
            (non_100_steps.unit, non_100_steps.max, non_100_steps.default_cap),
            ("steps", 80, 32),
        )

    def test_invalid_scale_overrides_raise_clear_driver_errors(self):
        invalid = (
            ({"volume_steps": "3"}, "volume_steps must be an integer from 1 to 2"),
            ({"zero_db": "nope"}, "zero_db must be an integer from 0 to 255"),
            ({"max_volume": "0"}, "max_volume must be an integer from 1 to 255"),
            (
                {
                    "zero_db": "",
                    "max_volume": "80",
                    "volume_unit": "%",
                },
                "volume_unit='%' requires max_volume=100",
            ),
        )
        for config, message in invalid:
            with self.subTest(config=config):
                with self.assertRaisesRegex(DriverError, message):
                    EiscpDriver(config)

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
        self.assertEqual(self.fake.mode, "00")
        found = await EiscpDriver.probe({"host": "127.0.0.1", "port": self.port})
        self.assertIsNotNone(found)
        self.assertEqual(found.driver, "eiscp")
        self.assertIsNone(
            await EiscpDriver.probe({"host": "127.0.0.1", "port": self.port + 1})
        )

    async def test_mode_validation_rejects_non_hex_codes_in_the_driver(self):
        with self.assertRaisesRegex(Refused, "Invalid mode code: STEREO"):
            await self.driver.set_mode("stereo")

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
        self.assertEqual(found[0].config["model"], "VSX-935")
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

    async def test_mode_state_does_not_probe_a_driver_without_mode_support(self):
        self.assertEqual(
            await self.driver.mode_state(),
            ModeState(None, (), complete=False),
        )
        self.assertEqual(PioneerTelnetDriver.mode_label("01"), "Mode 01")

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
