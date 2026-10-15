"""Integration checks against the actual local HTTP server (no internet)."""
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from orbits import DEFAULT_SETTINGS
from satellite_alert import LocalServer, Monitor, ROOT, atomic_json
from test_hardware import Clock, Factory

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

    def test_frequency_lookup_is_authenticated_and_resolves_norad_identity(self):
        reply = {"status": "found", "matches": [{"label": "ISS", "profile": {"downlink_mhz": 437.8}}], "message": "Offline test"}
        with patch.object(self.monitor.frequency_lookup, "lookup", return_value=reply) as lookup:
            status, body, _ = self.request("POST", "/api/profile-lookup", {"name": "25544"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), reply)
            self.assertIn("ISS", lookup.call_args.args[1])
            self.assertEqual(self.request("POST", "/api/profile-lookup", {"name": "ISS"}, {"X-Satellite-Token": "wrong"})[0], 403)
            self.assertEqual(lookup.call_count, 1)
            self.assertEqual(self.request("POST", "/api/profile-lookup", {"name": "<script>"})[0], 400)
            self.assertEqual(self.request("POST", "/api/profile-lookup", {"name": None})[0], 400)

    def test_notification_pause_api_saves_and_returns_quiet_active_alerts(self):
        self.now = self.monitor.passes[0].peak_time
        self.monitor.tick()
        self.assertTrue(self.monitor.alerts)
        status, _, _ = self.request("POST", "/api/settings", {"satellite_notifications_paused": {"ISS": True}})
        self.assertEqual(status, 200)
        _, body, _ = self.request("GET", "/api/state")
        value = json.loads(body)
        self.assertTrue(value["settings"]["satellite_notifications_paused"]["ISS"])
        self.assertTrue(value["satellites"][0]["notifications_paused"])
        self.assertFalse(value["alerts"][0]["active"])
        self.assertFalse(value["alerts"][0]["acknowledged"])
        self.assertEqual(self.request("POST", "/api/settings", {"satellite_notifications_paused": {"ISS": "true"}})[0], 400)

    def test_dashboard_assets_and_live_state(self):
        for path in ("/", "/app.js", "/style.css", "/api/health", "/d3-array.min.js", "/d3-geo.min.js", "/world-land.json", "/alert-chime.wav"):
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
        status, body, headers = self.request("GET", "/alert-chime.wav")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "audio/wav")
        self.assertTrue(body.startswith(b"RIFF"))

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

    def test_radio_test_endpoint_and_pause_independence_without_opening_ports(self):
        self.monitor.hardware.ports_provider = lambda: [{"device": "COM6"}]
        self.assertEqual(self.request("POST", "/api/radio-test", {"enabled": True})[0], 200)
        _, body, _ = self.request("GET", "/api/state")
        self.assertTrue(json.loads(body)["hardware"]["radio_test_enabled"])
        self.assertFalse(json.loads(body)["hardware"]["enabled"])
        self.assertEqual(self.request("POST", "/api/pause", {"paused": True})[0], 200)
        self.assertTrue(self.monitor.hardware.snapshot()["radio_test_enabled"])
        self.assertEqual(self.request("POST", "/api/tracking", {"enabled": True})[0], 400)
        self.assertEqual(self.request("POST", "/api/radio-test", {"enabled": False})[0], 200)
        self.assertFalse(self.monitor.hardware.snapshot()["radio_test_enabled"])
        self.assertIsNone(self.monitor.hardware.radio)

    def test_radio_test_rejects_missing_ports_and_settings_edits_stop_it(self):
        self.monitor.hardware.ports_provider = lambda: []
        self.assertEqual(self.request("POST", "/api/radio-test", {"enabled": True})[0], 400)
        self.assertEqual(self.request("POST", "/api/radio-test", {"enabled": "true"})[0], 400)
        self.monitor.hardware.ports_provider = lambda: [{"device": "COM6"}]
        self.assertEqual(self.request("POST", "/api/radio-test", {"enabled": True})[0], 200)
        self.assertEqual(self.request("POST", "/api/settings", {"radio_baud": 9600})[0], 200)
        self.assertFalse(self.monitor.hardware.snapshot()["radio_test_enabled"])
        self.assertEqual(self.monitor.settings["radio_baud"], 9600)

    def test_rotator_test_endpoint_returns_raw_reply_and_time_in_shared_state(self):
        clock = Clock()
        factory = Factory(clock)
        factory.rotator_reply = b"AZ=183EL=010\r\n"
        self.monitor.hardware.serial_factory = factory
        self.monitor.hardware.ports_provider = lambda: [{"device": "COM10"}]
        self.monitor.hardware.monotonic = clock
        self.monitor.hardware.utc_clock = lambda: self.now
        self.assertEqual(self.request("POST", "/api/settings", {"rotator_port": "COM10", "rotator_enabled": False})[0], 200)
        status, body, _ = self.request("POST", "/api/rotator-test")
        result = json.loads(body)["result"]
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tested_at"], self.now.isoformat())
        self.assertEqual(result["response"], "AZ=183EL=010\r\n")
        self.assertEqual(result["position"], {"azimuth": 183, "elevation": 10})
        self.assertEqual(factory.devices[0].writes, [b"C2\r"])
        self.assertTrue(factory.devices[0].closed)
        _, body, _ = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(state["hardware"]["rotator_test"], result)
        self.assertFalse(state["hardware"]["enabled"])
        self.assertEqual(state["hardware"]["rotator_position"], result["position"])

    def test_rotator_test_missing_port_warns_without_opening_and_preserves_alerts_and_pause(self):
        self.monitor.settings["rotator_port"] = ""
        self.monitor.toggle_pause(True)
        self.monitor.test_alert()
        with patch.object(self.monitor.hardware, "_open") as open_port:
            status, body, _ = self.request("POST", "/api/rotator-test")
        result = json.loads(body)["result"]
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "warning")
        self.assertIn("No rotator port is configured", result["message"])
        self.assertFalse(result["command_sent"])
        open_port.assert_not_called()
        self.assertTrue(self.monitor.paused)
        self.assertFalse(self.monitor.alerts[-1]["acknowledged"])
        self.assertEqual(self.monitor.hardware.snapshot()["rotator_test"], result)

    def test_closing_windows_popup_keeps_dashboard_alert_until_explicit_acknowledgement(self):
        alert = self.monitor._make_alert("ISS", False, self.now, self.now + timedelta(seconds=60), 35, 65,
                                         horizon_rise=self.now + timedelta(minutes=5))
        self.monitor.alerts.append(alert)
        messages = []
        windows = SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(MessageBoxW=lambda *args: messages.append(args) or 1)))
        with patch("satellite_alert.threading.Thread") as popup_thread:
            self.monitor._popup([alert])
            callback = popup_thread.call_args.kwargs["target"]
        with patch.dict("sys.modules", ctypes=windows):
            callback()
        self.assertIn("Acknowledge & silence", messages[0][1])
        self.assertFalse(alert["acknowledged"])
        self.now += timedelta(seconds=61)
        _, body, _ = self.request("GET", "/api/state")
        saved = next(row for row in json.loads(body)["alerts"] if row["id"] == alert["id"])
        self.assertFalse(saved["active"])
        self.assertFalse(saved["acknowledged"])
        self.assertEqual(self.request("POST", "/api/acknowledge", {"id": alert["id"]})[0], 200)
        self.assertTrue(alert["acknowledged"])

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
