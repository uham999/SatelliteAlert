"""Per-satellite pauses: persistence, alert suppression and independent tracking."""
from dataclasses import replace
from datetime import timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orbits import DEFAULT_SETTINGS, OrbitCalculator, parse_tles, validate_settings
from satellite_alert import Monitor, atomic_json

FIXTURE = (Path(__file__).parent / "fixtures/iss.tle").read_text()


class SatelliteNotificationTests(unittest.TestCase):
    def setUp(self):
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

    def load(self):
        return Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE, local_timezone=timezone.utc)

    def tearDown(self):
        self.monitor.close()
        self.temp.cleanup()

    def test_defaults_existing_and_new_satellites_are_unchecked_and_values_validated(self):
        self.assertEqual(self.monitor.settings["satellite_notifications_paused"], {"ISS": False, "FO-29": False})
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
        self.monitor.save_settings({"satellites": ["ISS", "12345"]})
        self.assertEqual(self.monitor.settings["satellite_notifications_paused"], {"ISS": True, "12345": False})
        for invalid in ([], None, {"ISS": "true"}, {"ISS": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_settings({"satellite_notifications_paused": invalid})

    def test_pause_survives_restart_and_partial_changes_do_not_clear_other_pauses(self):
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
        self.monitor.save_settings({"satellite_notifications_paused": {"FO-29": True}})
        self.monitor.close()
        self.monitor = self.load()
        self.assertEqual(self.monitor.settings["satellite_notifications_paused"], {"ISS": True, "FO-29": True})
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
        self.assertEqual(json.loads((self.root / "settings.json").read_text())["satellite_notifications_paused"], {"ISS": False, "FO-29": True})

    def test_paused_satellite_has_no_alert_or_ledger_entry_and_can_resume_midpass(self):
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
        self.monitor.settings.update(sound_enabled=True, windows_popup=True)
        with patch.object(self.monitor, "_ring") as ring, patch.object(self.monitor, "_popup") as popup:
            self.monitor.tick()
            ring.assert_not_called()
            popup.assert_not_called()
            self.assertEqual(self.monitor.alerts, [])
            self.assertEqual(self.monitor.ledger, [])
            snapshot = self.monitor.snapshot()
            self.assertIsNone(snapshot["tracking_target"])
            self.assertTrue(next(row for row in snapshot["satellites"] if row["name"] == "ISS")["notifications_paused"])
            self.assertTrue(next(row for row in snapshot["passes"] if row["name"] == "ISS")["notifications_paused"])
            self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
            self.assertEqual([row["name"] for row in self.monitor.alerts], ["ISS"])
            ring.assert_called_once()

    def test_pause_cancels_existing_alert_without_rebuilding_and_resume_creates_fresh_active_alert(self):
        self.monitor.tick()
        passes, generation = self.monitor.passes, self.monitor.generation
        self.monitor.settings.update(sound_enabled=True)
        with patch.object(self.monitor.hardware, "set_enabled") as hardware, patch.object(self.monitor, "_ring") as ring:
            self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
            self.monitor.tick()
            hardware.assert_not_called()
            ring.assert_not_called()
        self.assertIs(self.monitor.passes, passes)
        self.assertEqual(self.monitor.generation, generation)
        alert = self.monitor.snapshot()["alerts"][0]
        self.assertFalse(alert["active"])
        self.assertTrue(alert["acknowledged"])
        self.assertEqual(alert["acknowledgement_source"], "satellite_pause")
        self.assertTrue(alert["notifications_paused"])
        with patch.object(self.monitor, "_ring"):
            self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
        self.assertTrue(self.monitor.snapshot()["alerts"][0]["active"])
        self.assertNotEqual(self.monitor.snapshot()["alerts"][0]["id"], alert["id"])

    def test_unpausing_after_threshold_set_resumes_the_remaining_visible_pass_immediately(self):
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
        self.now = self.pass_.threshold_set + (self.pass_.horizon_set - self.pass_.threshold_set) / 2
        self.monitor._rebuild()
        current = next(pass_ for pass_ in self.monitor.passes if pass_.name == 'ISS' and pass_.horizon_rise <= self.now < pass_.horizon_set)
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
        alert = self.monitor.snapshot()['alerts'][0]
        self.assertTrue(alert['active'])
        self.assertIn('resumed during the current pass', alert['message'])
        self.assertEqual(alert['expires_at'], current.to_json('horizon')['horizon_set'])
        self.assertEqual(self.monitor.snapshot()['tracking_target']['name'], 'ISS')

    def test_unpausing_an_ended_pass_does_not_reactivate_it(self):
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
        self.now = self.pass_.horizon_set + timedelta(seconds=1)
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
        self.assertEqual(self.monitor.alerts, [])
        self.assertIsNone(self.monitor.tracking_target)

    def test_pause_retargets_running_devices_and_resume_restores_current_pass_without_starting_disabled_live_control(self):
        from test_hardware import Clock, Factory
        clock = Clock()
        factory = Factory(clock)
        self.monitor.hardware.serial_factory = factory
        self.monitor.hardware.monotonic = clock
        self.monitor.hardware.ports_provider = lambda: [{'device': 'COM10'}, {'device': 'COM11'}]
        self.monitor.save_settings(dict(rotator_enabled=True, radio_enabled=True, rotator_port='COM10', radio_port='COM11'))
        _, satellite, observer = self.monitor.models[0]
        self.monitor.models.append(('FO-29', satellite, observer))
        self.monitor.passes.append(replace(self.pass_, name='FO-29', norad=24278))
        self.monitor.tick()
        self.monitor.toggle_tracking(True)
        self.monitor.hardware.step()
        self.assertEqual(self.monitor.tracking_target['name'], 'ISS')
        previous_rotator = self.monitor.hardware.rotator
        self.monitor.save_settings({'satellite_notifications_paused': {'ISS': True}})
        self.monitor.hardware.step()
        self.assertEqual(self.monitor.tracking_target['name'], 'FO-29')
        self.assertEqual(self.monitor.hardware.snapshot()['radio_verified_target'], 'FO-29')
        self.assertIn(b'S\r', previous_rotator.writes)
        self.assertTrue(self.monitor.hardware.snapshot()['enabled'])
        self.monitor.save_settings({'satellite_notifications_paused': {'FO-29': True}})
        self.monitor.hardware.step()
        self.assertIsNone(self.monitor.tracking_target)
        self.assertFalse(any(frame[4] == 0x05 for frame in self.monitor.hardware.radio.writes))
        self.monitor.save_settings({'satellite_notifications_paused': {'ISS': False}})
        self.monitor.hardware.step()
        self.assertEqual(self.monitor.hardware.snapshot()['radio_verified_target'], 'ISS')
        self.monitor.toggle_tracking(False)
        self.monitor.hardware.step()
        count = len(factory.devices)
        self.monitor.save_settings({'satellite_notifications_paused': {'ISS': True}})
        self.monitor.save_settings({'satellite_notifications_paused': {'ISS': False}})
        self.monitor.hardware.step()
        self.assertEqual(self.monitor.tracking_target['name'], 'ISS')
        self.assertFalse(self.monitor.hardware.snapshot()['enabled'])
        self.assertEqual(len(factory.devices), count)

    def test_pause_cancels_a_radio_update_already_in_flight(self):
        from test_hardware import Clock, Factory
        clock = Clock()
        factory = Factory(clock)
        controller = self.monitor.hardware
        controller.serial_factory = factory
        controller.monotonic = clock
        controller.ports_provider = lambda: [{'device': 'COM10'}, {'device': 'COM11'}]
        self.monitor.save_settings(dict(rotator_enabled=True, radio_enabled=True, rotator_port='COM10', radio_port='COM11'))
        self.monitor.tick()
        self.monitor.toggle_tracking(True)
        controller.step()
        radio, rotator = controller.radio, controller.rotator
        original_write = radio.write
        paused = []
        def pause_during_write(frame):
            size = original_write(frame)
            if frame[4] == 0x05 and not paused:
                paused.append(True)
                self.monitor.save_settings({'satellite_notifications_paused': {'ISS': True}})
            return size
        row = dict(controller.target)
        row['doppler'] = {**row['doppler'], 'rx_hz': row['doppler']['rx_hz'] + 1000, 'tx_hz': row['doppler']['tx_hz'] + 1000}
        controller.offer(row, self.monitor.settings)
        before = len(radio.writes)
        with patch.object(radio, 'write', side_effect=pause_during_write):
            controller.step()
        self.assertTrue(paused)
        self.assertEqual(sum(frame[4] == 0x05 for frame in radio.writes[before:]), 1)
        self.assertTrue(radio.closed and rotator.closed)
        self.assertIn(b'S\r', rotator.writes)
        self.assertIsNone(self.monitor.tracking_target)
        self.assertTrue(controller.snapshot()['enabled'])

    def test_other_satellite_still_alerts_and_test_alert_still_rings(self):
        _, satellite, observer = self.monitor.models[0]
        self.monitor.models.append(("FO-29", satellite, observer))
        self.monitor.passes.append(replace(self.pass_, name="FO-29", norad=24278))
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": True}})
        self.monitor.settings.update(sound_enabled=True)
        with patch.object(self.monitor, "_ring") as ring:
            self.monitor.tick()
            self.assertEqual([alert["name"] for alert in self.monitor.alerts], ["FO-29"])
            ring.assert_called_once()
            self.monitor.test_alert()
            self.assertEqual(ring.call_count, 2)


if __name__ == "__main__":
    unittest.main()
