"""Validate audio and the Windows playback path without using real speakers."""
from pathlib import Path
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from satellite_alert import ROOT, SoundPlayer


class SoundTests(unittest.TestCase):
    def test_bundled_chime_is_valid_audible_pcm_without_clipping(self):
        with wave.open(str(ROOT / 'static/alert-chime.wav'), 'rb') as clip:
            self.assertEqual((clip.getnchannels(), clip.getsampwidth(), clip.getframerate()), (1, 2, 32000))
            self.assertAlmostEqual(clip.getnframes() / clip.getframerate(), 1.8)
            data = clip.readframes(clip.getnframes())
        samples = struct.unpack('<'+'h'*(len(data)//2), data)
        self.assertGreater(max(map(abs, samples)), 18000)
        self.assertLess(max(map(abs, samples)), 32767)

    def test_windows_plays_wav_and_reports_driver_failure_without_staying_busy(self):
        for failure in (None, RuntimeError('Device unavailable')):
            with self.subTest(failure=failure):
                win = SimpleNamespace(PlaySound=Mock(side_effect=failure), SND_FILENAME=0x20000, SND_NODEFAULT=2)
                player = SoundPlayer();player.windows = True
                with patch.dict('sys.modules', winsound=win), patch('satellite_alert.LOG'):
                    player.play()
                    self.assertTrue(player.busy.acquire(timeout=2));player.busy.release()
                win.PlaySound.assert_called_once_with(str(ROOT / 'static/alert-chime.wav'), 0x20002)
                self.assertIn('failed' if failure else 'completed', player.status)

    def test_second_ring_does_not_interrupt_a_playing_clip(self):
        started, release = threading.Event(), threading.Event()
        def playing(*args):
            started.set();release.wait(timeout=2)
        win = SimpleNamespace(PlaySound=Mock(side_effect=playing), SND_FILENAME=0x20000, SND_NODEFAULT=2)
        player = SoundPlayer();player.windows = True
        with patch.dict('sys.modules', winsound=win):
            player.play();self.assertTrue(started.wait(timeout=1))
            player.play();release.set()
            self.assertTrue(player.busy.acquire(timeout=2));player.busy.release()
        self.assertEqual(win.PlaySound.call_count, 1)


if __name__ == '__main__':
    unittest.main()
