"""Offline verification: real orbit geometry, persistence, refresh and alerts."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from orbits import (
    DEFAULT_SETTINGS, OrbitCalculator, TLE, alert_due, alert_time, already_alerted,
    iso, parse_tles, resolve_name, validate_settings,
)
from satellite_alert import Monitor, atomic_json

FIXTURE = (Path(__file__).parent / "fixtures" / "iss.tle").read_text()
RECORDS, _ = parse_tles(FIXTURE)


class SettingsAndElementsTests(unittest.TestCase):
    def test_confirmed_location_and_revised_list(self):
        self.assertEqual(DEFAULT_SETTINGS["latitude"], 51.825)
        self.assertEqual(DEFAULT_SETTINGS["longitude"], -0.375)
        self.assertEqual(DEFAULT_SETTINGS["minimum_elevation"], 35)
        self.assertEqual(DEFAULT_SETTINGS["satellites"], ["RS-44", "AO-73", "SO-50", "ISS", "FO-29", "JO-97", "AO-123"])

    def test_invalid_locations_and_nonfinite_settings_rejected(self):
        for values in ({"latitude": -91}, {"longitude": -375}, {"minimum_elevation": 90}, {"latitude": float("nan")}, {"refresh_hours": float("inf")}, {"sound_enabled": "true"}, {"latitude": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_settings(values)

    def test_satellite_input_validation_and_normalisation(self):
        self.assertEqual(validate_settings({"satellites": [" iss ", "ISS", "25544"]})["satellites"], ["ISS", "25544"])
        for names in ([], ["   "], ["<script>"], "ISS"):
            with self.subTest(names=names), self.assertRaises(ValueError):
                validate_settings({"satellites": names})

    def test_early_timing_and_optional_earth_setting(self):
        settings = validate_settings({"alert_mode": "before_horizon", "earth_overlay": False})
        self.assertEqual(settings["alert_mode"], "before_horizon")
        self.assertFalse(settings["earth_overlay"])
        self.assertTrue(validate_settings({})["earth_overlay"])
        with self.assertRaises(ValueError):
            validate_settings({"earth_overlay": "false"})

    def test_five_minute_default_and_all_warning_choices(self):
        self.assertEqual(validate_settings({})["alert_mode"], "before_horizon_5")
        for mode in ("before_horizon_5", "before_horizon", "before_horizon_20"):
            with self.subTest(mode=mode):
                self.assertEqual(validate_settings({"alert_mode": mode})["alert_mode"], mode)
        with self.assertRaises(ValueError):
            validate_settings({"alert_mode": "before_horizon_15"})

    def test_valid_elements_and_identity(self):
        self.assertEqual(len(RECORDS), 1)
        self.assertEqual(resolve_name("ISS", RECORDS).norad, 25544)
        self.assertEqual(resolve_name("25544", RECORDS).norad, 25544)
        self.assertIsNone(resolve_name("AO-97", RECORDS))

    def test_feed_rename_keeps_norad_identity(self):
        renamed = {25544: replace(RECORDS[25544], name="ISS (ZARYA)")}
        self.assertEqual(resolve_name("ISS", renamed).norad, 25544)
        misleading = {1: TLE("ISS", 1, "", "")}
        self.assertIsNone(resolve_name("ISS", misleading))

    def test_html_empty_and_truncated_downloads_rejected(self):
        for text in ("<html>Service unavailable</html>", "", "ISS\n" + RECORDS[25544].line1):
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                parse_tles(text)

    def test_bad_checksum_and_mismatched_catalogue_rejected(self):
        record = RECORDS[25544]
        broken = record.line1[:-1] + str((int(record.line1[-1]) + 1) % 10)
        with self.assertRaises(ValueError):
            parse_tles(f"ISS\n{broken}\n{record.line2}\n")
        second = record.line2[:2] + "25545" + record.line2[7:68]
        checksum = sum(int(c) if c.isdigit() else 1 if c == "-" else 0 for c in second) % 10
        with self.assertRaises(ValueError):
            parse_tles(f"ISS\n{record.line1}\n{second}{checksum}\n")

    def test_bad_extra_record_does_not_hide_valid_record(self):
        records, warnings = parse_tles(FIXTURE + "TRUNCATED\n1 not an element\n")
        self.assertIn(25544, records)
        self.assertTrue(warnings)


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calculator = OrbitCalculator()
        cls.satellite = cls.calculator.satellite(RECORDS[25544])
        cls.now = cls.satellite.epoch.utc_datetime()
        cls.settings = dict(DEFAULT_SETTINGS)
        cls.observer = cls.calculator.observer(cls.settings)
        cls.passes = cls.calculator.passes("ISS", cls.satellite, cls.observer, cls.settings, cls.now)

    def test_qualifying_passes_and_event_order(self):
        self.assertGreater(len(self.passes), 0)
        for pass_ in self.passes:
            self.assertLessEqual(pass_.horizon_rise, pass_.threshold_rise)
            self.assertLess(pass_.threshold_rise, pass_.peak_time)
            self.assertLess(pass_.peak_time, pass_.threshold_set)
            self.assertLessEqual(pass_.threshold_set, pass_.horizon_set)
            self.assertGreaterEqual(pass_.peak_elevation, 35)
            self.assertLessEqual(pass_.peak_elevation, 90)

    def test_actual_crossing_is_35_degrees_from_ground(self):
        for pass_ in self.passes:
            at = self.calculator.position(self.satellite, self.observer, pass_.threshold_rise)["elevation"]
            before = self.calculator.position(self.satellite, self.observer, pass_.threshold_rise - timedelta(seconds=3))["elevation"]
            after = self.calculator.position(self.satellite, self.observer, pass_.threshold_rise + timedelta(seconds=3))["elevation"]
            self.assertAlmostEqual(at, 35, delta=0.1)
            self.assertLess(before, 35)
            self.assertGreater(after, 35)

    def test_horizon_and_descending_crossings(self):
        pass_ = self.passes[0]
        for moment, angle in ((pass_.horizon_rise, 0), (pass_.threshold_set, 35), (pass_.horizon_set, 0)):
            self.assertAlmostEqual(self.calculator.position(self.satellite, self.observer, moment)["elevation"], angle, delta=0.1)

    def test_observer_location_changes_elevation(self):
        pass_ = self.passes[0]
        south = self.calculator.observer({**self.settings, "latitude": -51.825})
        northern = self.calculator.position(self.satellite, self.observer, pass_.peak_time)["elevation"]
        southern = self.calculator.position(self.satellite, south, pass_.peak_time)["elevation"]
        self.assertGreater(northern - southern, 30)

    def test_forecast_includes_a_pass_already_underway(self):
        pass_ = self.passes[0]
        underway = self.calculator.passes("ISS", self.satellite, self.observer, self.settings, pass_.peak_time)
        self.assertLess(underway[0].threshold_rise, pass_.peak_time)
        self.assertGreater(underway[0].threshold_set, pass_.peak_time)

    def test_threshold_and_horizon_alert_timing(self):
        pass_ = self.passes[0]
        self.assertFalse(alert_due(pass_, "threshold", pass_.threshold_rise - timedelta(seconds=1)))
        self.assertTrue(alert_due(pass_, "threshold", pass_.peak_time))
        self.assertFalse(alert_due(pass_, "threshold", pass_.threshold_set))
        self.assertFalse(alert_due(pass_, "horizon", pass_.horizon_rise - timedelta(seconds=1)))
        self.assertTrue(alert_due(pass_, "horizon", pass_.horizon_rise + timedelta(seconds=5)))
        self.assertFalse(alert_due(pass_, "horizon", pass_.horizon_set))

    def test_early_alert_is_ten_minutes_before_geometric_horizon(self):
        pass_ = self.passes[0]
        early = pass_.horizon_rise - timedelta(minutes=10)
        self.assertEqual(alert_time(pass_, "before_horizon"), early)
        self.assertEqual(pass_.to_json("before_horizon")["alert_at"], iso(early))
        self.assertFalse(alert_due(pass_, "before_horizon", early - timedelta(seconds=1)))
        self.assertTrue(alert_due(pass_, "before_horizon", early))
        self.assertTrue(alert_due(pass_, "before_horizon", early + timedelta(minutes=5)))
        self.assertFalse(alert_due(pass_, "before_horizon", pass_.threshold_set))

    def test_five_and_twenty_minute_boundaries_and_midnight(self):
        for mode, minutes in (("before_horizon_5", 5), ("before_horizon_20", 20)):
            with self.subTest(mode=mode):
                pass_ = replace(self.passes[0], horizon_rise=self.now.replace(hour=0, minute=2, second=0, microsecond=0))
                early = pass_.horizon_rise - timedelta(minutes=minutes)
                self.assertEqual(alert_time(pass_, mode), early)
                self.assertEqual(pass_.to_json(mode)["alert_at"], iso(early))
                self.assertEqual(early.date(), (pass_.horizon_rise - timedelta(days=1)).date())
                self.assertFalse(alert_due(pass_, mode, early - timedelta(seconds=1)))
                self.assertTrue(alert_due(pass_, mode, early))
                self.assertFalse(alert_due(pass_, mode, pass_.threshold_set))

    def test_early_time_crosses_midnight_correctly(self):
        rise = self.now.replace(hour=0, minute=5, second=0, microsecond=0)
        pass_ = replace(self.passes[0], horizon_rise=rise)
        early = alert_time(pass_, "before_horizon")
        self.assertEqual(early.date(), (rise - timedelta(days=1)).date())
        self.assertEqual((early.hour, early.minute), (23, 55))

    def test_early_forecasts_still_require_the_minimum_elevation(self):
        settings = {**self.settings, "alert_mode": "before_horizon"}
        windows = self.calculator.passes("ISS", self.satellite, self.observer, settings, self.now)
        self.assertTrue(windows)
        for pass_ in windows:
            self.assertGreaterEqual(pass_.peak_elevation, settings["minimum_elevation"])
            self.assertLessEqual(alert_time(pass_, "before_horizon"), self.now + timedelta(hours=settings["forecast_hours"]))

    def test_deduplication_tolerates_a_refreshed_tle(self):
        pass_ = self.passes[0]
        ledger = [dict(norad=pass_.norad, horizon_rise=iso(pass_.horizon_rise))]
        self.assertTrue(already_alerted(replace(pass_, horizon_rise=pass_.horizon_rise + timedelta(seconds=90)), ledger))
        self.assertFalse(already_alerted(replace(pass_, horizon_rise=pass_.horizon_rise + timedelta(minutes=90)), ledger))

    def test_path_and_serialisation_are_finite(self):
        value = self.passes[0].to_json("threshold")
        json.dumps(value, allow_nan=False)
        self.assertEqual(len(value["path"]), 65)
        for item in value["path"]:
            self.assertTrue(-0.1 <= item["elevation"] <= 90)
            self.assertTrue(0 <= item["azimuth"] <= 360)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="satellite-alert-tests-")
        self.root = Path(self.temp.name)
        self.now = GeometryTests.now
        settings = {**DEFAULT_SETTINGS, "satellites": ["ISS"], "alert_mode": "threshold", "sound_enabled": False, "windows_popup": False, "automatic_pause_enabled": False}
        atomic_json(self.root / "settings.json", settings)
        self.monitor = Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE)

    def tearDown(self):
        self.monitor.close()
        self.temp.cleanup()

    def prepared(self):
        self.monitor._refresh_once()
        self.monitor._rebuild()
        return self.monitor.passes[0]

    def test_daily_refresh_is_24_hours_after_success_and_survives_restart(self):
        self.monitor._refresh_once()
        self.assertEqual(self.monitor.next_refresh(), self.now + timedelta(hours=24))
        loaded = Monitor(self.root, clock=lambda: self.now, fetcher=lambda: self.fail("Cache should not trigger download"))
        self.assertEqual(loaded.fetched_at, self.now)
        self.assertEqual(loaded.next_refresh(), self.now + timedelta(hours=24))

    def start_until_downloaded(self, monitor):
        refreshed = threading.Event()
        refresh_once = monitor._refresh_once
        def record_refresh():
            try:
                refresh_once()
            finally:
                refreshed.set()
        monitor._refresh_once = record_refresh
        monitor.start()
        try:
            self.assertTrue(refreshed.wait(3), "Startup must refresh without waiting for the daily interval")
        finally:
            monitor.close()
        self.assertIsNone(monitor.fetch_error)
        self.assertEqual(monitor.fetched_at, self.now)

    def test_startup_downloads_when_no_cache_exists(self):
        self.start_until_downloaded(self.monitor)
        self.assertIn(25544, self.monitor.records)

    def test_startup_refreshes_old_download_despite_longer_schedule(self):
        self.monitor._refresh_once()
        self.now += timedelta(hours=24)
        self.monitor.settings["refresh_hours"] = 72
        self.assertGreater(self.monitor.next_refresh(), self.now)
        self.start_until_downloaded(self.monitor)

    def test_startup_refreshes_old_epoch_even_when_download_is_new(self):
        self.now += timedelta(days=1, seconds=1)
        self.monitor._refresh_once()  # New download, old epoch in provider data.
        self.assertEqual(self.monitor.next_refresh(), self.now + timedelta(hours=24))
        self.start_until_downloaded(self.monitor)
        self.assertFalse(self.monitor.manual_refresh)  # Consumed once; no busy download loop.
        self.assertEqual(self.monitor.next_refresh(), self.now + timedelta(hours=24))

    def test_startup_keeps_fresh_data_without_downloading(self):
        self.monitor._refresh_once()
        self.now += timedelta(hours=23)
        checked = threading.Event()
        rebuild = self.monitor._rebuild
        def record_rebuild():
            try:
                rebuild()
            finally:
                checked.set()
        self.monitor._rebuild = record_rebuild
        self.monitor.fetcher = lambda: self.fail("Fresh cache should not download on startup")
        self.monitor.start()
        try:
            self.assertTrue(checked.wait(3))
        finally:
            self.monitor.close()
        self.assertIsNone(self.monitor.fetch_error)
        self.assertEqual(self.monitor.fetched_at, GeometryTests.now)

    def test_startup_refreshes_missing_watched_satellite(self):
        self.monitor._refresh_once()
        self.monitor.settings["satellites"].append("AO-73")
        self.start_until_downloaded(self.monitor)
        self.assertIn(25544, self.monitor.records)

    def test_startup_refresh_failure_keeps_cache_and_backoff(self):
        self.monitor._refresh_once()
        original_cache = (self.root / "data/elements.json").read_bytes()
        self.now += timedelta(days=2)
        failed = threading.Event()
        def unavailable():
            failed.set()
            raise OSError("Offline")
        self.monitor.fetcher = unavailable
        with patch('satellite_alert.LOG'):
            self.monitor.start()
            try:
                self.assertTrue(failed.wait(3))
            finally:
                self.monitor.close()
        self.assertEqual((self.root / "data/elements.json").read_bytes(), original_cache)
        self.assertEqual(self.monitor.next_refresh(), self.now + timedelta(minutes=5))

    def test_failed_download_keeps_good_cache_and_retries_five_minutes(self):
        self.monitor._refresh_once()
        saved = (self.root / "data/elements.json").read_bytes()
        self.now += timedelta(hours=1)  # Early manual refresh: retry must not wait until tomorrow.
        self.monitor.fetcher = lambda: "<html>error</html>"
        with patch('satellite_alert.LOG'):
            self.monitor._refresh_once()
        self.assertEqual((self.root / "data/elements.json").read_bytes(), saved)
        self.assertEqual(self.monitor.next_refresh(), self.now + timedelta(minutes=5))
        self.assertIn("Download failed", self.monitor.fetch_error)

    def test_download_without_selected_satellites_is_rejected(self):
        self.monitor._refresh_once()
        good = (self.root / "data/elements.json").read_bytes()
        self.monitor.settings["satellites"] = ["AO-73"]
        with patch('satellite_alert.LOG'):
            self.monitor._refresh_once()
        self.assertEqual((self.root / "data/elements.json").read_bytes(), good)

    def test_real_alert_fires_once_and_persists(self):
        pass_ = self.prepared()
        self.now = pass_.threshold_rise - timedelta(seconds=3)
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 0)
        self.now = pass_.peak_time
        self.monitor.tick()
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 1)
        self.assertFalse(self.monitor.alerts[0]["test"])
        restarted = Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE)
        restarted._rebuild()
        restarted.tick()
        self.assertEqual(len(restarted.alerts), 1)

    def test_early_alert_fires_below_horizon_and_survives_restart(self):
        self.monitor.save_settings({"alert_mode": "before_horizon"})
        pass_ = self.prepared()
        early = pass_.horizon_rise - timedelta(minutes=10)
        self.now = early - timedelta(seconds=1)
        self.monitor.tick()
        self.assertEqual(self.monitor.alerts, [])
        self.now = early
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 1)
        alert = self.monitor.alerts[0]
        self.assertLess(alert["elevation"], 0)
        self.assertEqual(alert["alert_mode"], "before_horizon")
        self.assertIn("horizon rise", alert["message"])
        self.assertEqual(alert["horizon_rise"], iso(pass_.horizon_rise))
        self.now += timedelta(minutes=5)
        restarted = Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE)
        restarted._rebuild()
        restarted.tick()
        self.assertEqual(len(restarted.alerts), 1)

    def test_starting_during_early_warning_window_catches_pass(self):
        self.monitor.save_settings({"alert_mode": "before_horizon"})
        pass_ = self.prepared()
        self.now = pass_.horizon_rise - timedelta(minutes=4)
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 1)
        self.assertLess(self.monitor.alerts[0]["elevation"], 0)

    def test_five_and_twenty_minute_alarms_below_horizon_and_restart(self):
        for mode, minutes in (("before_horizon_5", 5), ("before_horizon_20", 20)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                settings = {**self.monitor.settings, "alert_mode": mode}
                atomic_json(root / "settings.json", settings)
                monitor = Monitor(root, clock=lambda: self.now, fetcher=lambda: FIXTURE)
                monitor._refresh_once()
                monitor._rebuild()
                pass_ = monitor.passes[0]
                self.now = pass_.horizon_rise - timedelta(minutes=minutes, seconds=1)
                monitor.tick()
                self.assertEqual(monitor.alerts, [])
                self.now += timedelta(seconds=1)
                monitor.tick()
                self.assertEqual(len(monitor.alerts), 1)
                self.assertLess(monitor.alerts[0]["elevation"], 0)
                self.assertGreaterEqual(monitor.alerts[0]["peak_elevation"], 35)
                self.assertIn("horizon rise", monitor.alerts[0]["message"])
                restarted = Monitor(root, clock=lambda: self.now, fetcher=lambda: FIXTURE)
                restarted._rebuild()
                restarted.tick()
                self.assertEqual(len(restarted.alerts), 1)

    def test_earth_toggle_preserves_active_alert_and_predictions(self):
        pass_ = self.prepared()
        self.now = pass_.peak_time
        self.monitor.tick()
        self.assertTrue(self.monitor.snapshot()["alerts"][0]["active"])
        generation = self.monitor.generation
        self.monitor.save_settings({"earth_overlay": False})
        self.assertEqual(self.monitor.generation, generation)
        self.assertIn(pass_, self.monitor.passes)
        self.assertTrue(self.monitor.snapshot()["alerts"][0]["active"])
        self.assertFalse(read_settings(self.root)["earth_overlay"])
        restarted = Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE)
        self.assertFalse(restarted.settings["earth_overlay"])

    def test_maximum_volume_setting_preserves_pending_alerts_and_predictions(self):
        pass_ = self.prepared()
        self.now = pass_.peak_time;self.monitor.tick()
        generation = self.monitor.generation
        self.monitor.save_settings({'windows_maximum_volume': False})
        self.assertEqual(self.monitor.generation, generation)
        self.assertFalse(self.monitor.alerts[0]['acknowledged'])
        self.assertTrue(self.monitor.snapshot()['alerts'][0]['active'])
        self.assertFalse(read_settings(self.root)['windows_maximum_volume'])
        with self.assertRaises(ValueError):
            self.monitor.save_settings({'windows_maximum_volume': 'yes'})

    def test_startup_resume_and_completed_pass(self):
        pass_ = self.prepared()
        self.monitor.toggle_pause(True)
        self.now = pass_.peak_time
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 0)
        self.monitor.toggle_pause(False)
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 1)
        self.now = pass_.horizon_set + timedelta(seconds=10)
        self.monitor.tick()
        self.assertFalse(self.monitor.snapshot()["alerts"][0]["active"])

    def test_acknowledgement_silences_without_realerting(self):
        pass_ = self.prepared()
        self.now = pass_.peak_time
        self.monitor.tick()
        self.monitor.acknowledge()
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 1)
        self.assertTrue(self.monitor.alerts[0]["acknowledged"])

    def test_stale_elements_suppress_alerts_even_with_recent_download(self):
        self.now += timedelta(days=8)
        self.monitor._refresh_once()
        self.monitor._rebuild()
        self.monitor.tick()
        self.assertEqual(self.monitor.satellite_status[0]["status"], "stale")
        self.assertEqual(self.monitor.alerts, [])

    def test_location_change_discards_old_passes_and_saves(self):
        self.prepared()
        self.monitor.save_settings({"latitude": -51.825})
        self.assertEqual(self.monitor.passes, [])
        self.assertEqual(read_settings(self.root)["latitude"], -51.825)

    def test_invalid_settings_do_not_replace_good_settings(self):
        before = (self.root / "settings.json").read_bytes()
        with self.assertRaises(ValueError):
            self.monitor.save_settings({"longitude": -375})
        self.assertEqual((self.root / "settings.json").read_bytes(), before)

    def test_missing_name_is_visible_and_filtered_elements_include_only_selected(self):
        self.monitor.settings["satellites"] = ["ISS", "AO-97"]
        self.prepared()
        self.assertEqual(self.monitor.satellite_status[1]["status"], "missing")
        self.assertEqual(self.monitor.filtered_elements(), FIXTURE)

    def test_test_alert_is_labelled_and_acknowledged(self):
        self.monitor.test_alert()
        self.assertTrue(self.monitor.snapshot()["alerts"][0]["test"])
        self.monitor.acknowledge()
        self.assertFalse(self.monitor.snapshot()["alerts"][0]["active"])

    def test_corrupt_cache_and_history_recover(self):
        atomic_json(self.root / "data/elements.json", {"text": "bad", "fetched_at": "not a timestamp"})
        atomic_json(self.root / "data/alerts.json", {"ledger": [{"norad": 25544}], "alerts": []})
        with patch('satellite_alert.LOG'):
            recovered = Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE)
        recovered._refresh_once()
        self.assertIsNone(recovered.fetch_error)
        self.assertIn(25544, recovered.records)


def read_settings(root):
    return json.loads((root / "settings.json").read_text())


if __name__ == "__main__":
    unittest.main()
