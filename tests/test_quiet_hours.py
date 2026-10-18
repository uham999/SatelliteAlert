"""Daily pause boundaries, persistent manual control and watch-list ordering."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from orbits import DEFAULT_SETTINGS, Pass, iso, validate_settings
from satellite_alert import Monitor, atomic_json

UTC = timezone.utc
FIXTURE = (Path(__file__).parent / "fixtures/iss.tle").read_text()


class QuietHoursTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 7, 12, tzinfo=UTC)
        atomic_json(self.root / "settings.json", {**DEFAULT_SETTINGS, "satellites": ["ISS"], "sound_enabled": False, "windows_popup": False, "auto_acknowledge_enabled": False})
        self.monitor = self.load()

    def load(self):
        return Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE, local_timezone=UTC)

    def tearDown(self):
        self.monitor.close()
        self.temp.cleanup()

    def paused_at(self, hour, minute=0, second=0):
        self.now = self.now.replace(hour=hour, minute=minute, second=second)
        return self.monitor.snapshot()["paused"]

    def test_default_enabled_midnight_to_seven_and_exact_boundaries(self):
        self.assertTrue(self.monitor.settings["automatic_pause_enabled"])
        self.assertEqual(self.monitor.settings["automatic_pause_start"], "00:00")
        self.assertEqual(self.monitor.settings["automatic_pause_hours"], 7)
        self.assertFalse(self.paused_at(23, 59, 59))
        self.assertTrue(self.paused_at(0))
        self.assertTrue(self.paused_at(6, 59, 59))
        self.assertFalse(self.paused_at(7))
        self.assertTrue(self.paused_at(0))

    def test_disabled_schedule_and_fractional_hours(self):
        self.monitor.save_settings({"automatic_pause_enabled": False})
        self.assertFalse(self.paused_at(1))
        self.monitor.save_settings({"automatic_pause_enabled": True, "automatic_pause_start": "03:15", "automatic_pause_hours": 1.5})
        self.assertFalse(self.paused_at(3, 14, 59))
        self.assertTrue(self.paused_at(3, 15))
        self.assertTrue(self.paused_at(4, 44, 59))
        self.assertFalse(self.paused_at(4, 45))

    def test_cross_midnight_period_has_one_identity(self):
        self.monitor.save_settings({"automatic_pause_start": "22:30", "automatic_pause_hours": 8})
        self.assertFalse(self.paused_at(22, 29, 59))
        self.assertTrue(self.paused_at(22, 30))
        window = self.monitor._pause_window(self.now)
        self.monitor.toggle_pause(False)
        self.now += timedelta(hours=3)
        self.assertEqual(self.monitor._pause_window(self.now), window)
        self.assertFalse(self.monitor.snapshot()["paused"])
        self.assertFalse(self.paused_at(6, 30))
        self.assertTrue(self.paused_at(22, 30))

    def test_manual_resume_overrides_current_window_survives_restart_then_next_day_pauses(self):
        self.assertTrue(self.paused_at(1))
        self.monitor.toggle_pause(False)
        self.assertTrue(self.monitor.snapshot()["automatic_pause_overridden"])
        self.monitor = self.load()
        self.assertFalse(self.paused_at(6, 59))
        self.assertFalse(self.paused_at(7))
        self.now += timedelta(days=1)
        self.assertTrue(self.paused_at(0))
        self.assertEqual(self.monitor.snapshot()["pause_reason"], "automatic")

    def test_resume_outside_period_does_not_skip_next_pause(self):
        self.monitor.toggle_pause(True)
        self.monitor.toggle_pause(False)
        self.now += timedelta(days=1)
        self.assertTrue(self.paused_at(0))

    def test_manual_pause_survives_automatic_end_and_restart(self):
        self.assertTrue(self.paused_at(1))
        self.monitor.toggle_pause(True)
        self.monitor = self.load()
        self.assertTrue(self.paused_at(8))
        self.assertEqual(self.monitor.snapshot()["pause_reason"], "manual")
        self.monitor.toggle_pause(False)
        self.assertFalse(self.monitor.snapshot()["paused"])

    def test_manual_pause_after_resume_within_quiet_hours(self):
        self.assertTrue(self.paused_at(2))
        self.monitor.toggle_pause(False)
        self.assertFalse(self.monitor.snapshot()["paused"])
        self.monitor.toggle_pause(True)
        self.assertTrue(self.monitor.snapshot()["paused"])
        self.monitor.toggle_pause(False)
        self.assertFalse(self.monitor.snapshot()["paused"])

    def test_startup_inside_window_and_after_override_expired(self):
        self.paused_at(1)
        self.assertTrue(self.load().snapshot()["paused"])
        self.monitor.toggle_pause(False)
        self.now += timedelta(days=1)
        self.assertTrue(self.load().snapshot()["paused"])

    def test_twenty_four_hour_schedule_resume_until_next_start(self):
        self.monitor.save_settings({"automatic_pause_start": "12:00", "automatic_pause_hours": 24})
        self.assertTrue(self.monitor.snapshot()["paused"])
        self.monitor.toggle_pause(False)
        self.now += timedelta(hours=23, minutes=59)
        self.assertFalse(self.monitor.snapshot()["paused"])
        self.now += timedelta(minutes=1)
        self.assertTrue(self.monitor.snapshot()["paused"])

    def test_local_zone_is_used_even_for_utc_clock_and_utc_display(self):
        self.monitor.local_timezone = timezone(timedelta(hours=1))
        self.assertTrue(self.paused_at(23))  # midnight local
        self.assertFalse(self.paused_at(6))  # 7am local

    def test_clock_offset_changes_keep_local_end_time_and_override_identity(self):
        # Model a daylight-saving transition: the local clock jumps 1am -> 2am.
        self.now = self.now.replace(hour=1)
        self.assertTrue(self.monitor.snapshot()["paused"])
        self.monitor.toggle_pause(False)
        self.monitor.local_timezone = timezone(timedelta(hours=1))
        self.assertFalse(self.monitor.snapshot()["paused"])
        self.assertEqual(self.monitor.resume_override_window, "2026-10-07")
        self.assertFalse(self.paused_at(6))  # End is still 7am local.

    def test_schedule_changes_apply_immediately_clear_override_without_rebuilding(self):
        self.assertTrue(self.paused_at(1))
        self.monitor.toggle_pause(False)
        generation = self.monitor.generation
        self.monitor.save_settings({"automatic_pause_hours": 8})
        self.assertTrue(self.monitor.snapshot()["paused"])
        self.assertEqual(self.monitor.generation, generation)
        self.monitor.save_settings({"automatic_pause_enabled": False})
        self.assertFalse(self.monitor.snapshot()["paused"])

    def test_invalid_schedule_does_not_save(self):
        before = (self.root / "settings.json").read_bytes()
        for value in ({"automatic_pause_enabled": "true"}, {"automatic_pause_start": "24:00"}, {"automatic_pause_start": "0:00"}, {"automatic_pause_start": None}, {"automatic_pause_hours": 0}, {"automatic_pause_hours": 25}, {"automatic_pause_hours": True}, {"automatic_pause_hours": float("nan")}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.monitor.save_settings(value)
        self.assertEqual((self.root / "settings.json").read_bytes(), before)

    def test_legacy_settings_get_new_defaults(self):
        legacy = {k: v for k, v in self.monitor.settings.items() if not k.startswith("automatic_pause_")}
        updated = validate_settings(legacy)
        self.assertTrue(updated["automatic_pause_enabled"])
        self.assertEqual(updated["automatic_pause_hours"], 7)
        self.assertEqual(updated["latitude"], legacy["latitude"])

    def test_corrupt_control_recovers_to_schedule(self):
        atomic_json(self.root / "data/pause.json", {"manual_paused": "false", "resume_override_window": "invalid"})
        self.now = self.now.replace(hour=1)
        with patch("satellite_alert.LOG"):
            recovered = self.load()
        self.assertTrue(recovered.snapshot()["paused"])
        self.assertTrue(recovered.warnings)

    def test_paused_positions_and_downloads_continue_and_resume_catches_pass(self):
        self.monitor._refresh_once()
        self.monitor._rebuild()
        pass_ = self.monitor.passes[0]
        self.now = pass_.peak_time
        self.monitor.save_settings({"automatic_pause_hours": 24})
        self.monitor.settings["sound_enabled"] = True
        self.monitor.sound.play = Mock()
        with patch.object(self.monitor, "_popup") as popup:
            self.monitor.tick()
            self.assertEqual(self.monitor.alerts, [])
            self.assertEqual(self.monitor.ledger, [])
            self.monitor.sound.play.assert_not_called()
            popup.assert_not_called()
        self.assertEqual(self.monitor.last_tick, self.now)
        self.assertIn("elevation", self.monitor.satellite_status[0])
        generation = self.monitor.generation
        self.monitor.save_settings({"automatic_pause_hours": 23.75})
        self.assertEqual(self.monitor.generation, generation)
        self.assertEqual(self.monitor.passes[0], pass_)
        self.monitor._refresh_once()
        self.assertEqual(self.monitor.fetched_at, self.now)
        self.monitor.toggle_pause(False)
        self.monitor.tick()
        self.assertEqual(len(self.monitor.alerts), 1)
        self.monitor.sound.play.assert_called_once()
        self.monitor.toggle_pause(True)
        self.monitor.last_sound_at = 0
        self.monitor.tick()
        self.monitor.sound.play.assert_called_once()


class WatchListTests(unittest.TestCase):
    def test_order_uses_next_unalerted_pass_and_keeps_empty_rows_last(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime(2026, 10, 7, 12, tzinfo=UTC)
            monitor = Monitor(Path(folder), clock=lambda: now, local_timezone=UTC)
            monitor.satellite_status = [{"name": name} for name in ["EMPTY", "LATER", "FIRST", "TIE", "MISSING"]]
            rise = now + timedelta(hours=1)
            first = Pass("FIRST", 1, rise, rise + timedelta(minutes=3), rise + timedelta(minutes=5), 60,
                         rise + timedelta(minutes=7), rise + timedelta(minutes=10), 90, 180, [])
            later = replace(first, name="LATER", norad=2, horizon_rise=rise + timedelta(hours=1), threshold_set=first.threshold_set + timedelta(hours=1))
            tie = replace(first, name="TIE", norad=3)
            monitor.passes = [later, tie, first]  # Deliberately unsorted input.
            snapshot = monitor.snapshot()
            self.assertEqual([row["name"] for row in snapshot["satellites"]], ["FIRST", "TIE", "LATER", "EMPTY", "MISSING"])
            monitor.ledger = [dict(norad=first.norad, horizon_rise=iso(first.horizon_rise))]
            self.assertEqual([row["name"] for row in monitor.snapshot()["satellites"]], ["TIE", "LATER", "EMPTY", "FIRST", "MISSING"])
            # Original configured row order is preserved, and expired passes disappear.
            self.assertEqual(monitor.satellite_status[0]["name"], "EMPTY")
            now = first.threshold_set
            self.assertEqual(monitor.snapshot()["satellites"][0]["name"], "LATER")

    def test_next_later_pass_replaces_already_alerted_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime(2026, 10, 7, 12, tzinfo=UTC)
            monitor = Monitor(Path(folder), clock=lambda: now, local_timezone=UTC)
            monitor.satellite_status = [{"name": "ISS"}]
            first = Pass("ISS", 25544, now, now, now, 60, now + timedelta(minutes=5), now + timedelta(minutes=8), 0, 180, [])
            following = replace(first, horizon_rise=now + timedelta(hours=2), threshold_set=now + timedelta(hours=2, minutes=5))
            monitor.passes = [first, following]
            monitor.ledger = [dict(norad=25544, horizon_rise=iso(now))]
            self.assertEqual(monitor.snapshot()["satellites"][0]["next_pass"]["horizon_rise"], iso(following.horizon_rise))


if __name__ == "__main__":
    unittest.main()
