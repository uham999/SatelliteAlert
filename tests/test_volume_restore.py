"""Whole alarm volume lifecycle using fake speakers and a fake playback device."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orbits import DEFAULT_SETTINGS
from satellite_alert import Monitor, atomic_json
from test_windows_audio import Endpoint


class VolumeRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        self.endpoint = Endpoint()  # Original output: 20%, muted.
        self.api_patch = patch('windows_audio._endpoint', side_effect=lambda: nullcontext(self.endpoint))
        self.api_patch.start()
        self.win = SimpleNamespace(PlaySound=Mock(), SND_FILENAME=0x20000, SND_NODEFAULT=2)
        self.sound_patch = patch.dict('sys.modules', winsound=self.win)
        self.sound_patch.start()
        atomic_json(self.root / 'settings.json', {**DEFAULT_SETTINGS, 'satellites': ['ISS'],
                    'windows_popup': False, 'automatic_pause_enabled': False,
                    'auto_acknowledge_seconds': 60, 'windows_volume_percent': 100})
        self.monitor = self.load()

    def load(self):
        monitor = Monitor(self.root, clock=lambda: self.now, local_timezone=timezone.utc)
        monitor.sound.windows = True
        return monitor

    def tearDown(self):
        self.monitor.close()
        self.sound_patch.stop()
        self.api_patch.stop()
        self.temp.cleanup()

    def alarm(self):
        alert = self.monitor.test_alert()
        self.assertTrue(self.monitor.sound.busy.acquire(timeout=2))
        self.monitor.sound.busy.release()
        return alert

    def restart_without_graceful_restore(self):
        old = self.monitor
        old.sound.volume_restore = None  # Simulated lost process memory.
        old.close()
        self.monitor = self.load()

    def test_manual_acknowledgement_restores_original_volume_and_mute(self):
        alert = self.alarm()
        self.assertEqual(self.endpoint.state(), {'level': 1, 'muted': False})
        self.monitor.acknowledge(alert['id'])
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})
        self.assertIsNone(self.monitor.sound.volume_restore)
        self.assertFalse((self.root / 'data/volume-restore.json').exists())
        self.assertIn('restored', self.monitor.snapshot()['volume_status'])

    def test_automatic_acknowledgement_restores_original_unmuted_level(self):
        self.endpoint.level, self.endpoint.muted = .37, False
        self.alarm()
        self.now += timedelta(seconds=60)
        self.monitor.tick()
        self.assertEqual(self.endpoint.state(), {'level': .37, 'muted': False})
        self.assertEqual(self.monitor.alerts[0]['acknowledgement_source'], 'automatic')

    def test_repeated_chimes_preserve_the_original_capture(self):
        alert = self.alarm()
        self.monitor._ring()
        self.assertTrue(self.monitor.sound.busy.acquire(timeout=2));self.monitor.sound.busy.release()
        saved = json.loads((self.root / 'data/volume-restore.json').read_text())
        self.assertEqual(saved['original'], {'level': .2, 'muted': True})
        self.monitor.acknowledge(alert['id'])
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})

    def test_overlapping_alarms_wait_for_the_last_manual_acknowledgement(self):
        first = self.alarm()
        self.now += timedelta(seconds=20)
        second = self.alarm()
        self.monitor.acknowledge(first['id'])
        self.assertEqual(self.endpoint.state(), {'level': 1, 'muted': False})
        self.monitor.acknowledge(second['id'])
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})

    def test_overlapping_automatic_deadlines_do_not_restore_while_an_alarm_remains(self):
        self.alarm()
        self.now += timedelta(seconds=20)
        self.alarm()
        self.now += timedelta(seconds=40)
        self.monitor.tick()
        self.assertEqual(self.endpoint.state(), {'level': 1, 'muted': False})
        self.now += timedelta(seconds=20)
        self.monitor.tick()
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})

    def test_restart_keeps_original_capture_and_restores_at_the_saved_deadline(self):
        self.alarm()
        self.now += timedelta(seconds=30)
        self.restart_without_graceful_restore()
        self.assertEqual(self.endpoint.state(), {'level': 1, 'muted': False})
        self.assertEqual(self.monitor.sound.volume_restore['original'], {'level': .2, 'muted': True})
        self.now += timedelta(seconds=30)
        self.monitor.tick()
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})

    def test_an_acknowledged_alarm_with_an_interrupted_restore_recovers_on_restart(self):
        alert = self.alarm()
        with patch('satellite_alert.restore_windows_volume', side_effect=OSError('Temporarily unavailable')), patch('satellite_alert.LOG'):
            self.monitor.acknowledge(alert['id'])
        self.assertTrue((self.root / 'data/volume-restore.json').exists())
        self.restart_without_graceful_restore()
        self.monitor.snapshot()
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})
        self.assertFalse((self.root / 'data/volume-restore.json').exists())

    def test_restore_failure_keeps_recovery_state_and_retries(self):
        alert = self.alarm()
        with patch('satellite_alert.restore_windows_volume', side_effect=OSError('Temporary error')), patch('satellite_alert.LOG'):
            self.monitor.acknowledge(alert['id'])
        self.assertIn('Could not restore', self.monitor.snapshot()['volume_status'])
        retry = self.monitor.sound.restore_retry_at
        with patch('satellite_alert.time.monotonic', return_value=retry + 1):
            self.monitor.snapshot()
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})

    def test_disabling_volume_override_leaves_the_pc_untouched(self):
        self.monitor.settings['windows_maximum_volume'] = False
        alert = self.alarm()
        self.monitor.acknowledge(alert['id'])
        self.assertEqual(self.endpoint.calls, [])
        self.assertIsNone(self.monitor.sound.volume_restore)

    def test_stopping_the_app_also_restores_the_original_volume(self):
        self.alarm()
        self.monitor.close()
        self.assertEqual(self.endpoint.state(), {'level': .2, 'muted': True})


if __name__ == '__main__':
    unittest.main()
