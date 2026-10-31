"""Pass-only frequency nudges using real orbit fixtures and simulated CI-V."""
from copy import deepcopy
from datetime import timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orbits import DEFAULT_SETTINGS, OrbitCalculator, parse_tles
from profiles import doppler
from satellite_alert import Monitor, atomic_json
from test_hardware import Clock, Factory

FIXTURE = (Path(__file__).parent / "fixtures/iss.tle").read_text()


class PassFrequencyTests(unittest.TestCase):
    def setUp(self):
        # CI has no COM ports. Never let the host PC's connected interfaces
        # determine whether startup rewrites the fixture's settings file.
        ports = patch("satellite_alert.available_ports", return_value=[])
        ports.start()
        self.addCleanup(ports.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        records, _ = parse_tles(FIXTURE)
        self.now = OrbitCalculator().satellite(records[25544]).epoch.utc_datetime()
        atomic_json(self.root / "settings.json", {**DEFAULT_SETTINGS, "satellites": ["ISS", "FO-29"],
                    "automatic_pause_enabled": False, "sound_enabled": False, "windows_popup": False})
        self.monitor = self.load()
        self.monitor._refresh_once()
        self.monitor._rebuild()
        self.pass_ = self.monitor.passes[0]
        self.now = self.pass_.peak_time
        self.monitor.tick()

    def load(self):
        return Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE, local_timezone=timezone.utc)

    def tearDown(self):
        self.monitor.close()
        self.temp.cleanup()

    def target(self):
        return self.monitor.snapshot()["tracking_target"]

    def nudge(self, channel, step, target=None):
        target = target or self.target()
        return self.monitor.nudge_frequency(target["name"], target["pass_frequency"]["id"], channel, step)

    def test_offsets_are_independent_and_applied_before_doppler_not_after(self):
        before = deepcopy(self.monitor.settings)
        saved_before = (self.root / "settings.json").read_bytes()
        self.nudge("rx", 1000)
        self.nudge("rx", 100)
        self.nudge("tx", -1000)
        self.nudge("tx", -100)
        target = self.target()
        f = target["doppler"]
        fraction = target["range_rate_km_s"] / 299792.458
        self.assertEqual(f["rx_offset_hz"], 1100)
        self.assertEqual(f["tx_offset_hz"], -1100)
        self.assertEqual(f["rx_base_hz"], 437_801_100)
        self.assertEqual(f["tx_base_hz"], 145_988_900)
        self.assertEqual(f["rx_hz"], round(437_801_100 * (1 - fraction)))
        self.assertEqual(f["tx_hz"], round(145_988_900 * (1 + fraction)))
        self.assertEqual(self.monitor.settings, before)
        self.assertEqual((self.root / "settings.json").read_bytes(), saved_before)
        # Large offsets/range rates distinguish pre-correction rounding from
        # simply adding an offset to the already corrected frequency.
        profile = before["satellite_profiles"]["ISS"]
        corrected = doppler(profile, 7, 100_000, -100_000)
        plain = doppler(profile, 7)
        self.assertNotEqual(corrected["rx_hz"], plain["rx_hz"] + 100_000)
        self.assertNotEqual(corrected["tx_hz"], plain["tx_hz"] - 100_000)

    def test_nudges_leave_saved_settings_unchanged_after_startup_fills_com3(self):
        self.monitor.close()
        with patch("satellite_alert.available_ports", return_value=[{"device": "COM3"}]):
            self.monitor = self.load()
        self.assertEqual(self.monitor.settings["rotator_port"], "COM3")
        self.monitor._rebuild()
        self.monitor.tick()
        saved_before = (self.root / "settings.json").read_bytes()
        before = deepcopy(self.monitor.settings)
        self.nudge("rx", 100)
        self.nudge("tx", -100)
        self.assertEqual((self.root / "settings.json").read_bytes(), saved_before)
        self.assertEqual(self.monitor.settings, before)

    def test_every_new_pass_starts_at_zero_even_after_a_gap_without_ticks(self):
        previous = self.target()
        self.nudge("rx", 1000)
        self.nudge("tx", -100)
        next_pass = next(p for p in self.monitor.passes if p.horizon_rise > self.pass_.horizon_set)
        self.now = next_pass.peak_time  # Simulate sleep across horizon set.
        self.monitor.tick()
        current = self.target()["pass_frequency"]
        self.assertNotEqual(current["id"], previous["pass_frequency"]["id"])
        self.assertEqual((current["rx_offset_hz"], current["tx_offset_hz"]), (0, 0))
        with self.assertRaises(ValueError):
            self.nudge("rx", 100, target=previous)

    def test_horizon_set_clears_controls_and_adjusted_preview(self):
        previous = self.target()
        self.nudge("rx", 1000)
        self.now = self.pass_.horizon_set + timedelta(seconds=2)
        self.monitor.tick()
        self.assertIsNone(self.target())
        row = next(row for row in self.monitor.snapshot()["satellites"] if row["name"] == "ISS")
        self.assertIsNone(row["pass_frequency"])
        self.assertEqual(row["doppler"]["rx_offset_hz"], 0)
        with self.assertRaises(ValueError):
            self.nudge("tx", 100, target=previous)

    def test_reforecast_pause_and_handover_preserve_only_the_same_satellite_pass(self):
        first = self.target()["pass_frequency"]["id"]
        self.nudge("rx", 1000)
        self.monitor._rebuild()
        self.monitor.tick()
        self.assertEqual(self.target()["pass_frequency"]["id"], first)
        _, satellite, observer = self.monitor.models[0]
        self.monitor.models.append(("FO-29", satellite, observer))
        self.monitor.tick()
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
        self.assertEqual(self.target()["name"], "FO-29")
        self.assertEqual(self.target()["pass_frequency"]["rx_offset_hz"], 0)
        self.nudge("tx", -100)
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
        self.assertEqual(self.target()["name"], "ISS")
        self.assertEqual(self.target()["pass_frequency"]["rx_offset_hz"], 1000)
        self.assertEqual(self.target()["pass_frequency"]["tx_offset_hz"], 0)

    def test_reset_only_changes_its_channel_and_restart_always_starts_at_zero(self):
        self.nudge("rx", 1000)
        self.nudge("tx", -1000)
        self.nudge("rx", 0)
        self.assertEqual(self.target()["pass_frequency"]["rx_offset_hz"], 0)
        self.assertEqual(self.target()["pass_frequency"]["tx_offset_hz"], -1000)
        self.monitor.close()
        self.monitor = self.load()
        self.monitor._rebuild()
        self.monitor.tick()
        self.assertEqual(self.target()["pass_frequency"]["tx_offset_hz"], 0)

    def test_low_pass_without_a_qualifying_notification_still_allows_nudges(self):
        self.monitor.save_settings({"minimum_elevation": 89.9})
        self.monitor._rebuild()
        self.monitor.tick()
        self.assertEqual(self.monitor.passes, [])
        self.nudge("rx", -100)
        self.assertEqual(self.target()["pass_frequency"]["rx_offset_hz"], -100)

    def test_receive_only_and_validation_do_not_change_saved_or_temporary_values(self):
        target = self.target()
        for channel, step in (("rx", True), ("rx", 1), ("rx", "100"), ("tx", 100.0), ("both", 100)):
            with self.subTest(channel=channel, step=step), self.assertRaises(ValueError):
                self.nudge(channel, step)
        with self.assertRaises(ValueError):
            self.monitor.nudge_frequency("ISS", "old-pass", "rx", 100)
        self.assertEqual(self.target()["pass_frequency"], target["pass_frequency"])
        profiles = deepcopy(self.monitor.settings["satellite_profiles"])
        profiles["ISS"]["uplink_mhz"] = None
        self.monitor.save_settings({"satellite_profiles": profiles})
        with self.assertRaises(ValueError):
            self.nudge("tx", 100)
        self.nudge("rx", 100)
        self.assertIsNone(self.target()["doppler"]["tx_hz"])

    def test_nudges_reach_simulated_radio_below_deadband_and_do_not_stop_tracking(self):
        clock = Clock()
        factory = Factory(clock)
        hardware = self.monitor.hardware
        hardware.serial_factory, hardware.monotonic = factory, clock
        hardware.ports_provider = lambda: [{"device": "COM10"}, {"device": "COM11"}]
        self.monitor.save_settings(dict(rotator_enabled=True, radio_enabled=True,
            rotator_port="COM10", radio_port="COM11", radio_step_hz=1000))
        self.monitor.toggle_tracking(True)
        hardware.step()
        updates = hardware.snapshot()["frequency_updates"]
        self.nudge("rx", 100)
        self.nudge("tx", -100)
        hardware.step()
        state = hardware.snapshot()
        self.assertTrue(state["enabled"])
        self.assertFalse(state["radio_manual_override"])
        self.assertGreater(state["frequency_updates"], updates)
        self.assertEqual(state["radio_rx_hz"], self.target()["doppler"]["rx_hz"])
        self.assertEqual(state["radio_tx_hz"], self.target()["doppler"]["tx_hz"])


if __name__ == "__main__":
    unittest.main()
