"""Integration checks against the actual local HTTP server (no internet)."""
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from orbits import DEFAULT_SETTINGS
from satellite_alert import LocalServer, Monitor, ROOT, atomic_json

FIXTURE = (Path(__file__).parent / "fixtures/iss.tle").read_text()


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="satellite-http-tests-")
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "static", self.root / "static")
        settings = {**DEFAULT_SETTINGS, "satellites": ["ISS"], "sound_enabled": False, "windows_popup": False, "automatic_pause_enabled": False}
        atomic_json(self.root / "settings.json", settings)
        self.now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        self.monitor = Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE, local_timezone=timezone.utc)
        self.monitor._refresh_once()
        self.monitor._rebuild()
        self.monitor.tick()
        self.server = LocalServer(("127.0.0.1", 0), self.monitor)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.monitor.close()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        actual = {"Content-Type": "application/json", "X-Satellite-Token": self.server.token}
        if headers:
            actual.update(headers)
        connection.request(method, path, json.dumps(data or {}) if method == "POST" else None, headers=actual)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, content, dict(response.getheaders()))
        connection.close()
        return result

    def test_dashboard_assets_and_live_state(self):
        for path in ("/", "/app.js", "/style.css", "/api/health", "/d3-array.min.js", "/d3-geo.min.js", "/world-land.json"):
            with self.subTest(path=path):
                status, body, _ = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
        status, body, _ = self.request("GET", "/api/state")
        value = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(value["satellites"][0]["name"], "ISS")
        self.assertTrue(value["passes"])
        self.assertEqual(value["settings"]["minimum_elevation"], 35)
        self.assertIn("ground_latitude", value["satellites"][0])
        self.assertIn("ground_longitude", value["satellites"][0])
        self.assertIn("range_rate_km_s", value["satellites"][0])

    def test_automatic_pause_settings_and_manual_resume_via_http(self):
        generation = self.monitor.generation
        self.assertEqual(self.request("POST", "/api/settings", {"automatic_pause_enabled": True, "automatic_pause_start": "00:00", "automatic_pause_hours": 7})[0], 200)
        _, body, _ = self.request("GET", "/api/state")
        self.assertTrue(json.loads(body)["paused"])
        self.assertEqual(json.loads(body)["pause_reason"], "automatic")
        self.assertEqual(self.monitor.generation, generation)
        self.assertEqual(self.request("POST", "/api/pause", {"paused": False})[0], 200)
        _, body, _ = self.request("GET", "/api/state")
        self.assertFalse(json.loads(body)["paused"])
        self.assertTrue(json.loads(body)["automatic_pause_overridden"])
        self.assertEqual(self.request("POST", "/api/settings", {"automatic_pause_start": "25:00"})[0], 400)
        self.assertEqual(self.monitor.settings["automatic_pause_start"], "00:00")

    def test_serial_port_listing_and_hardware_switch_reject_missing_ports(self):
        self.assertEqual(self.request("POST", "/api/settings", {"radio_baud": 57600})[0], 200)
        self.assertEqual(self.monitor.settings["radio_baud"], 57600)
        with patch("satellite_alert.available_ports", return_value=[{"device": "COM10", "description": "Simulated rotator"}]):
            status, body, _ = self.request("GET", "/api/ports")
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value["ports"][0]["device"], "COM10")
        self.assertEqual(value["default_profiles"]["SO-50"]["downlink_mhz"], 436.795)
        self.monitor.hardware.ports_provider = lambda: []
        self.assertEqual(self.request("POST", "/api/tracking", {"enabled": True})[0], 400)
        self.assertFalse(self.monitor.hardware.snapshot()["enabled"])
        self.assertEqual(self.request("POST", "/api/tracking", {"enabled": False})[0], 200)

    def test_satellite_frequency_crud_persists_without_frequency_reforecast(self):
        profile = {"downlink_mhz": 145.935, "uplink_mhz": None, "downlink_mode": "USB", "uplink_mode": "LSB", "notes": "receive test"}
        self.assertEqual(self.request("POST", "/api/settings", {"satellites": ["ISS", "12345"], "satellite_profiles": {"12345": profile}})[0], 200)
        self.assertIn("12345", self.monitor.settings["satellite_profiles"])
        generation = self.monitor.generation
        profile["downlink_mhz"] = 145.936
        profiles = {**self.monitor.settings["satellite_profiles"], "12345": profile}
        self.assertEqual(self.request("POST", "/api/settings", {"satellite_profiles": profiles})[0], 200)
        self.assertEqual(self.monitor.generation, generation)
        self.assertEqual(self.monitor.settings["satellite_profiles"]["12345"]["downlink_mhz"], 145.936)
        self.assertEqual(self.request("POST", "/api/settings", {"satellites": ["ISS"]})[0], 200)
        self.assertNotIn("12345", self.monitor.settings["satellite_profiles"])
        saved = json.loads((self.root / "settings.json").read_text())
        self.assertEqual(saved["satellites"], ["ISS"])

    def test_earth_image_served_locally_and_early_timing_saved(self):
        status, body, headers = self.request("GET", "/earth-overlay.png")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
        status, _, _ = self.request("POST", "/api/settings", {"alert_mode": "before_horizon", "earth_overlay": False})
        self.assertEqual(status, 200)
        self.monitor._rebuild()
        _, body, _ = self.request("GET", "/api/state")
        value = json.loads(body)
        self.assertEqual(value["settings"]["alert_mode"], "before_horizon")
        self.assertFalse(value["settings"]["earth_overlay"])
        rise = datetime.fromisoformat(value["passes"][0]["horizon_rise"])
        early = datetime.fromisoformat(value["passes"][0]["alert_at"])
        self.assertEqual((rise - early).total_seconds(), 600)

    def test_settings_save_recalculates_and_validation_preserves_old_value(self):
        status, _, _ = self.request("POST", "/api/settings", {"minimum_elevation": 40})
        self.assertEqual(status, 200)
        self.assertEqual(self.monitor.settings["minimum_elevation"], 40)
        self.assertFalse(self.monitor.passes)
        status, body, _ = self.request("POST", "/api/settings", {"longitude": -375})
        self.assertEqual(status, 400)
        self.assertIn("longitude", json.loads(body)["error"])
        self.assertEqual(self.monitor.settings["longitude"], -.375)

    def test_test_acknowledge_pause_and_filtered_elements(self):
        self.assertEqual(self.request("POST", "/api/test-alert")[0], 200)
        self.assertTrue(self.monitor.alerts[-1]["test"])
        self.assertEqual(self.request("POST", "/api/acknowledge")[0], 200)
        self.assertTrue(self.monitor.alerts[-1]["acknowledged"])
        self.assertEqual(self.request("POST", "/api/pause", {"paused": True})[0], 200)
        self.assertTrue(self.monitor.paused)
        status, body, _ = self.request("GET", "/api/elements")
        self.assertEqual(status, 200)
        self.assertEqual(body.decode(), FIXTURE)

    def test_unrelated_origins_and_missing_tokens_cannot_change_settings(self):
        for headers in ({"X-Satellite-Token": "wrong"}, {"Origin": "https://unrelated.example"}, {"Host": "unrelated.example"}):
            with self.subTest(headers=headers):
                status, _, _ = self.request("POST", "/api/settings", {"latitude": 0}, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.monitor.settings["latitude"], 51.825)


if __name__ == "__main__":
    unittest.main()
