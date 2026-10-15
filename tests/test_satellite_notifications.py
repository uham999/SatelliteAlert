"""Per-satellite pauses: persistence, alert suppression and independent tracking."""
from dataclasses import replace
from datetime import timezone
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
            self.assertEqual(snapshot["tracking_target"]["name"], "ISS")
            self.assertTrue(next(row for row in snapshot["satellites"] if row["name"] == "ISS")["notifications_paused"])
            self.assertTrue(next(row for row in snapshot["passes"] if row["name"] == "ISS")["notifications_paused"])
            self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
            self.monitor.tick()
            self.assertEqual([row["name"] for row in self.monitor.alerts], ["ISS"])
            ring.assert_called_once()

    def test_pause_existing_alert_silences_it_without_acknowledging_or_rebuilding(self):
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
        self.assertFalse(alert["acknowledged"])
        self.assertTrue(alert["notifications_paused"])
        self.monitor.save_settings({"satellite_notifications_paused": {"ISS": False}})
        self.assertTrue(self.monitor.snapshot()["alerts"][0]["active"])

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
