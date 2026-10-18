"""Volume-control behaviour using a fake endpoint; does not alter PC volume."""
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from windows_audio import GUID, _check, maximize_windows_volume, restore_windows_volume, set_windows_volume


class Endpoint:
    def __init__(self):
        self.level, self.muted, self.calls = .2, True, []

    def set_level(self, level):
        self.level = level;self.calls.append(('level', level))

    def set_mute(self, muted):
        self.muted = muted;self.calls.append(('mute', muted))

    def state(self):
        return dict(level=self.level, muted=self.muted)


class VolumeTests(unittest.TestCase):
    def test_restore_exact_original_level_and_mute_state_including_zero(self):
        for level, muted in ((0, True), (.23, True), (.37, False), (1, False)):
            with self.subTest(level=level, muted=muted):
                endpoint = Endpoint()
                endpoint.level, endpoint.muted = 1, False
                saved = dict(level=level, muted=muted)
                with patch('windows_audio._endpoint', return_value=nullcontext(endpoint)):
                    self.assertEqual(restore_windows_volume(saved), saved)
                self.assertEqual(endpoint.calls[-2:], [('level', level), ('mute', muted)])
                if muted:
                    self.assertEqual(endpoint.calls[0], ('mute', True))

    def test_invalid_saved_state_and_unconfirmed_restore_do_not_claim_success(self):
        with patch('windows_audio._endpoint') as endpoint:
            for saved in (None, {}, {'level': 1.1, 'muted': True}, {'level': .2, 'muted': 'true'}, {'level': float('nan'), 'muted': False}):
                with self.subTest(saved=saved), self.assertRaises(ValueError):
                    restore_windows_volume(saved)
            endpoint.assert_not_called()
        endpoint = Endpoint()
        with patch('windows_audio._endpoint', return_value=nullcontext(endpoint)), patch.object(endpoint, 'state', return_value={'level': 1, 'muted': False}):
            with self.assertRaises(OSError):
                restore_windows_volume({'level': .2, 'muted': True})

    def test_specified_level_unmutes_before_playback_and_confirms_readback(self):
        for percent in (1, 35, 65.5, 100):
            with self.subTest(percent=percent):
                endpoint = Endpoint()
                with patch('windows_audio._endpoint', return_value=nullcontext(endpoint)):
                    self.assertEqual(set_windows_volume(percent), {'level': percent / 100, 'muted': False})
                self.assertEqual(endpoint.calls, [('level', percent / 100), ('mute', False)])

    def test_invalid_levels_never_touch_the_playback_device(self):
        with patch('windows_audio._endpoint') as endpoint:
            for percent in (-1, 0, 101, True, None, '50', float('nan'), float('inf')):
                with self.subTest(percent=percent), self.assertRaises(ValueError):
                    set_windows_volume(percent)
            endpoint.assert_not_called()

    def test_rejected_custom_level_or_remaining_mute_is_an_error(self):
        for state in ({'level': .65, 'muted': True}, {'level': 1, 'muted': False}):
            endpoint = Endpoint()
            with patch('windows_audio._endpoint', return_value=nullcontext(endpoint)), patch.object(endpoint, 'state', return_value=state):
                with self.assertRaises(OSError):
                    set_windows_volume(65)

    def test_sets_maximum_unmutes_and_confirms_readback(self):
        endpoint = Endpoint()
        with patch('windows_audio._endpoint', return_value=nullcontext(endpoint)):
            self.assertEqual(maximize_windows_volume(), {'level': 1.0, 'muted': False})
        self.assertEqual(endpoint.calls, [('level', 1.0), ('mute', False)])

    def test_missing_device_or_rejected_volume_does_not_claim_success(self):
        with patch('windows_audio._endpoint', side_effect=OSError('No device')):
            with self.assertRaises(OSError):
                maximize_windows_volume()
        endpoint = Endpoint()
        with patch('windows_audio._endpoint', return_value=nullcontext(endpoint)), patch.object(endpoint, 'state', return_value={'level': .2, 'muted': True}):
            with self.assertRaises(OSError):
                maximize_windows_volume()

    def test_hresult_failures_are_reported_and_guid_layout_matches_windows(self):
        _check(0);_check(1)
        with self.assertRaisesRegex(OSError, '80004005'):
            _check(-2147467259)
        self.assertEqual(bytes(GUID.parse('5CDF2C82-841E-4546-9722-0CF74078229A')).hex(), '822cdf5c1e84464597220cf74078229a')
