"""Automatic acknowledgement and notification-only defaults, without real audio."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from hardware import HardwareController
from orbits import DEFAULT_SETTINGS, iso, settings_for_startup, validate_settings
from satellite_alert import Monitor, atomic_json

FIXTURE = (Path(__file__).parent / 'fixtures/iss.tle').read_text()


class AlarmSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
        atomic_json(self.root / 'settings.json', {**DEFAULT_SETTINGS, 'satellites': ['ISS'],
                    'sound_enabled': False, 'windows_popup': False, 'automatic_pause_enabled': False,
                    'auto_acknowledge_seconds': 60, 'windows_volume_percent': 100, 'repeat_sound_seconds': 30})
        self.monitor = self.load()

    def load(self):
        return Monitor(self.root, clock=lambda: self.now, fetcher=lambda: FIXTURE, local_timezone=timezone.utc)

    def tearDown(self):
        self.monitor.close()
        self.temp.cleanup()

    def test_new_defaults_and_existing_enabled_choices_are_preserved(self):
        defaults = validate_settings({})
        self.assertTrue(defaults['auto_acknowledge_enabled'])
        self.assertEqual(defaults['auto_acknowledge_seconds'], 300)
        self.assertEqual(defaults['windows_volume_percent'], 75)
        self.assertEqual(defaults['repeat_sound_seconds'], 60)
        self.assertEqual(defaults['observer_height_m'], 100)
        self.assertEqual(defaults['tracking_minimum_mode'], 'horizon')
        self.assertFalse(defaults['rotator_enabled'] or defaults['radio_enabled'])
        existing = settings_for_startup({'rotator_enabled': True, 'radio_enabled': True,
                                        'windows_maximum_volume': False}, [])
        self.assertTrue(existing['rotator_enabled'] and existing['radio_enabled'])
        self.assertFalse(existing['windows_maximum_volume'])

    def test_five_minute_default_alarm_uses_75_percent_and_repeat_changes_do_not_rebuild_passes(self):
        defaults = validate_settings({})
        self.monitor.save_settings({key: defaults[key] for key in ('auto_acknowledge_seconds', 'windows_volume_percent', 'repeat_sound_seconds')})
        generation = self.monitor.generation
        self.monitor.settings['sound_enabled'] = True
        with patch.object(self.monitor.sound, 'play') as play:
            alert = self.monitor.test_alert()
            play.assert_called_once_with(volume_percent=75, alert_ids=[alert['id']])
        self.assertEqual(alert['auto_acknowledge_at'], iso(self.now + timedelta(seconds=300)))
        self.assertEqual(self.monitor.generation, generation)
        self.now += timedelta(seconds=299)
        self.assertFalse(self.monitor.snapshot()['alerts'][0]['acknowledged'])
        self.now += timedelta(seconds=1)
        self.assertTrue(self.monitor.snapshot()['alerts'][0]['acknowledged'])

    def test_exact_one_minute_boundary_and_history_remains_after_auto_acknowledgement(self):
        alert = self.monitor.test_alert()
        self.assertEqual(alert['auto_acknowledge_at'], iso(self.now + timedelta(seconds=60)))
        self.now += timedelta(seconds=59, milliseconds=999)
        self.assertFalse(self.monitor.snapshot()['alerts'][0]['acknowledged'])
        self.now += timedelta(milliseconds=1)
        state = self.monitor.snapshot()
        self.assertEqual(len(state['alerts']), 1)
        self.assertTrue(state['alerts'][0]['acknowledged'])
        self.assertFalse(state['alerts'][0]['active'])
        self.assertEqual(state['alerts'][0]['acknowledgement_source'], 'automatic')
        stored = json.loads((self.root / 'data/alerts.json').read_text())['alerts'][0]
        self.assertEqual(stored['acknowledged_at'], iso(self.now))
        self.assertTrue(stored['acknowledged'])

    def test_deadline_survives_restart_without_an_extra_minute(self):
        self.monitor.test_alert()
        self.now += timedelta(seconds=50)
        self.monitor.close()
        self.monitor = self.load()
        self.assertFalse(self.monitor.snapshot()['alerts'][0]['acknowledged'])
        self.now += timedelta(seconds=10)
        self.monitor.tick()
        self.assertTrue(self.monitor.alerts[0]['acknowledged'])
        self.monitor.close()
        self.now += timedelta(seconds=30)
        self.monitor = self.load()
        self.assertTrue(self.monitor.alerts[0]['acknowledged'])

    def test_old_history_without_a_deadline_is_migrated_on_startup(self):
        alert = self.monitor.test_alert()
        alert.pop('auto_acknowledge_at')
        self.monitor._persist_alerts()
        self.now += timedelta(seconds=75)
        self.monitor.close()
        self.monitor = self.load()
        self.assertTrue(self.monitor.alerts[0]['acknowledged'])
        self.assertEqual(self.monitor.alerts[0]['acknowledgement_source'], 'automatic')
        self.assertEqual(self.monitor.alerts[0]['acknowledged_at'], iso(self.now))

    def test_manual_mode_waits_for_acknowledgement_and_does_not_relabel_manual_history(self):
        self.monitor.save_settings({'auto_acknowledge_enabled': False})
        self.monitor.test_alert()
        self.now += timedelta(hours=1)
        self.monitor.tick()
        alert = self.monitor.snapshot()['alerts'][0]
        self.assertFalse(alert['acknowledged'])
        self.assertIsNone(alert['auto_acknowledge_at'])
        self.monitor.acknowledge(alert['id'])
        self.monitor.save_settings({'auto_acknowledge_enabled': True})
        self.monitor.tick()
        self.assertEqual(self.monitor.alerts[0]['acknowledgement_source'], 'manual')

    def test_overlapping_alerts_have_independent_deadlines_and_pause_does_not_reset_timers(self):
        first = self.monitor.test_alert()
        self.now += timedelta(seconds=30)
        second = self.monitor.test_alert()
        self.monitor.toggle_pause(True)
        self.now += timedelta(seconds=30)
        self.monitor.tick()
        self.assertTrue(first['acknowledged'])
        self.assertFalse(second['acknowledged'])
        self.now += timedelta(seconds=30)
        self.monitor.tick()
        self.assertTrue(second['acknowledged'])

    def test_setting_changes_preserve_predictions_and_update_pending_alarm_policy(self):
        self.monitor._refresh_once()
        self.monitor._rebuild()
        passes, generation = self.monitor.passes, self.monitor.generation
        self.monitor.test_alert()
        self.monitor.save_settings({'auto_acknowledge_seconds': 120, 'windows_volume_percent': 45})
        self.assertIs(self.monitor.passes, passes)
        self.assertEqual(self.monitor.generation, generation)
        self.now += timedelta(seconds=90)
        self.assertFalse(self.monitor.snapshot()['alerts'][0]['acknowledged'])
        self.monitor.save_settings({'auto_acknowledge_seconds': 30})
        self.assertTrue(self.monitor.alerts[0]['acknowledged'])

    def test_switching_to_manual_cancels_a_pending_deadline(self):
        self.monitor.test_alert()
        self.now += timedelta(seconds=20)
        self.monitor.save_settings({'auto_acknowledge_enabled': False})
        self.now += timedelta(minutes=2)
        self.assertFalse(self.monitor.snapshot()['alerts'][0]['acknowledged'])
        self.assertIsNone(self.monitor.alerts[0]['auto_acknowledge_at'])

    def test_real_pass_repeats_keep_original_deadline_and_do_not_realert_after_auto_ack(self):
        self.monitor._refresh_once()
        self.monitor._rebuild()
        self.now = self.monitor.passes[0].threshold_rise
        self.monitor.settings['alert_mode'] = 'threshold'
        self.monitor.settings['sound_enabled'] = True
        self.monitor.settings['windows_volume_percent'] = 55
        with patch.object(self.monitor.sound, 'play') as play, patch('satellite_alert.time.monotonic') as mono:
            mono.return_value = 100
            self.monitor.tick()
            self.assertEqual(len(self.monitor.alerts), 1)
            deadline = self.monitor.alerts[0]['auto_acknowledge_at']
            self.now += timedelta(seconds=30);mono.return_value = 130
            self.monitor.tick()
            self.assertEqual(self.monitor.alerts[0]['auto_acknowledge_at'], deadline)
            self.assertEqual(play.call_count, 2)
            play.assert_called_with(volume_percent=55, alert_ids=[self.monitor.alerts[0]['id']])
            self.now += timedelta(seconds=30);mono.return_value = 160
            self.monitor.tick()
            self.assertTrue(self.monitor.alerts[0]['acknowledged'])
            self.assertEqual(play.call_count, 2)
            self.assertEqual(len(self.monitor.alerts), 1)

    def test_muted_volume_override_can_be_disabled_without_acknowledging_the_alarm(self):
        self.monitor.settings['sound_enabled'] = True
        with patch.object(self.monitor.sound, 'play') as play:
            self.monitor.save_settings({'windows_volume_percent': 37})
            self.monitor.test_alert()
            play.assert_called_with(volume_percent=37, alert_ids=[self.monitor.alerts[0]['id']])
            self.monitor.save_settings({'windows_maximum_volume': False})
            self.monitor.test_alert()
            play.assert_called_with(volume_percent=None, alert_ids=[alert['id'] for alert in self.monitor.alerts])
        self.assertFalse(any(alert['acknowledged'] for alert in self.monitor.alerts))

    def test_notification_only_defaults_cannot_start_hardware_without_opt_in(self):
        factory = Mock()
        controller = HardwareController(serial_factory=factory, ports_provider=lambda: [])
        try:
            with self.assertRaisesRegex(ValueError, 'Enable a rotator or radio'):
                controller.set_enabled(True, validate_settings({}))
            controller.step()
            factory.assert_not_called()
        finally:
            controller.close()

    def test_popup_closes_when_its_saved_alert_is_automatically_acknowledged(self):
        alert = self.monitor.test_alert()
        with patch('satellite_alert.threading.Thread') as thread:
            self.monitor._popup([alert])
            display = thread.call_args.kwargs['target']
        with patch('satellite_alert.show_windows_popup') as popup:
            display()
        message, acknowledged, stop = popup.call_args.args
        self.assertIn('60 seconds', message)
        self.assertFalse(acknowledged())
        self.now += timedelta(seconds=60)
        self.monitor.tick()
        self.assertTrue(acknowledged())
        self.assertIs(stop, self.monitor.stop)

    def test_validation_rejects_bad_alarm_options_before_writing_settings(self):
        before = (self.root / 'settings.json').read_bytes()
        for data in ({'auto_acknowledge_enabled': 'true'}, {'auto_acknowledge_seconds': 0},
                     {'auto_acknowledge_seconds': 86401}, {'auto_acknowledge_seconds': float('nan')},
                     {'auto_acknowledge_seconds': True}, {'windows_volume_percent': 101},
                     {'windows_volume_percent': 0}, {'windows_volume_percent': float('inf')}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.monitor.save_settings(data)
            self.assertEqual((self.root / 'settings.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
