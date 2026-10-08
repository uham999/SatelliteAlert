"""Volume-control behaviour using a fake endpoint; does not alter PC volume."""
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from windows_audio import GUID, _check, maximize_windows_volume


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
