"""IC-9700 band constraints during actual-pass tuning, using simulated CI-V."""
from datetime import datetime, timezone
from itertools import permutations
import unittest

from hardware import HardwareController, civ_frame, decode_frequency
from orbits import validate_settings
from profiles import doppler, profile
from test_hardware import Clock, Factory


class PassRadioTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.factory = Factory(self.clock)
        self.settings = validate_settings({'rotator_enabled': False, 'radio_enabled': True, 'radio_port': 'COM11'})
        self.now = datetime(2026, 10, 8, 12, 35, tzinfo=timezone.utc)
        self.controller = HardwareController(self.factory, lambda: [{'device': 'COM11'}], self.clock,
                                             utc_clock=lambda: self.now)

    def tearDown(self):
        self.controller.close()

    def offer(self, name='ISS', rate=-1):
        row = dict(name=name, status='tracking', elevation=30, azimuth=100, range_rate_km_s=rate,
                   doppler=doppler(self.settings['satellite_profiles'][name], rate))
        self.controller.offer(row, self.settings)
        return row

    def verify(self, row):
        state = self.controller.snapshot()
        self.assertTrue(state['enabled'], state['error'])
        self.assertEqual(state['radio_rx_hz'], row['doppler']['rx_hz'])
        self.assertEqual(state['radio_tx_hz'], row['doppler']['tx_hz'])
        self.assertEqual(state['radio_verified_target'], row['name'])
        self.assertEqual(state['radio_verified_at'], self.now.isoformat())
        self.assertEqual(self.controller.radio.frequencies['main'], row['doppler']['rx_hz'])
        if row['doppler']['tx_hz'] is not None:
            self.assertEqual(self.controller.radio.frequencies['sub'], row['doppler']['tx_hz'])

    def test_radio_connection_test_then_uhf_iss_pass_exchanges_bands_before_command_05(self):
        self.controller.set_radio_test(True, self.settings)
        self.controller.step()
        self.clock.now += 2
        self.controller.step()
        old_radio = self.controller.radio
        self.assertEqual(old_radio.frequencies['main'], 145_555_000)
        self.factory.initial_frequencies = dict(old_radio.frequencies)
        self.controller.set_radio_test(False, self.settings)
        self.controller.step()
        row = self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.verify(row)
        radio = self.controller.radio
        exchange = radio.writes.index(civ_frame(0xA2, 0x07, b'\xb0'))
        first_tune = next(i for i, frame in enumerate(radio.writes) if frame[4] == 0x05)
        self.assertLess(exchange, first_tune)
        self.assertEqual(radio.selected, 'main')
        self.assertTrue(radio.satellite)

    def test_handover_between_uhf_and_vhf_downlinks_and_doppler_both_directions(self):
        row = self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.verify(row)
        radio = self.controller.radio
        for name in ('AO-73', 'JO-97', 'SO-50', 'ISS'):
            with self.subTest(satellite=name):
                for rate in (-3, 0, 3):
                    row = self.offer(name, rate)
                    self.controller.step()
                    self.verify(row)
        self.assertIs(self.controller.radio, radio)
        self.assertEqual(radio.writes.count(civ_frame(0xA2, 0x07, b'\xb0')), 3)
        self.assertEqual(self.controller.snapshot()['frequency_updates'], 13)
        self.assertFalse(any(frame[4] in (0x09, 0x1C) for frame in radio.writes))

    def test_all_different_band_pairs_from_all_initial_main_sub_arrangements(self):
        bands = (145_500_000, 435_000_000, 1_270_000_000)
        for main, sub in permutations(bands, 2):
            for rx, tx in permutations(bands, 2):
                with self.subTest(main=main, sub=sub, rx=rx, tx=tx):
                    self.controller.set_enabled(False, self.settings)
                    self.controller.step()
                    self.factory.initial_frequencies = {'main': main, 'sub': sub}
                    self.settings['satellite_profiles']['ISS'] = profile(rx / 1e6, tx / 1e6, 'FM', 'FM')
                    row = self.offer(rate=0)
                    self.controller.set_enabled(True, self.settings)
                    self.controller.step()
                    self.verify(row)

    def test_satellite_mode_restores_its_own_bands_before_band_selection_is_checked(self):
        self.factory.initial_frequencies = {'main': 435_000_000, 'sub': 145_500_000}
        self.factory.initial_satellite_frequencies = {'main': 145_500_000, 'sub': 435_000_000}
        row = self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.verify(row)
        self.assertIn(civ_frame(0xA2, 0x07, b'\xb0'), self.controller.radio.writes)

    def test_receive_only_downlink_on_sub_is_exchanged_without_setting_an_uplink(self):
        self.settings['satellite_profiles']['ISS']['uplink_mhz'] = None
        row = self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.verify(row)
        radio = self.controller.radio
        self.assertFalse(radio.satellite)
        tunes = [decode_frequency(frame[5:-1]) for frame in radio.writes if frame[4] == 0x05]
        self.assertEqual(tunes, [row['doppler']['rx_hz']])
        self.assertIsNone(self.controller.snapshot()['radio_tx_hz'])

    def test_same_band_duplex_is_rejected_before_any_tune_or_band_exchange(self):
        self.settings['satellite_profiles']['ISS']['uplink_mhz'] = 435.1
        self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        state = self.controller.snapshot()
        self.assertFalse(state['enabled'])
        self.assertIn('different bands', state['error'])
        self.assertEqual(self.factory.devices[0].writes, [])
        self.assertIsNone(state['radio_verified_at'])

    def test_configuration_rejection_stops_before_any_frequency_write(self):
        self.factory.reject_command = 0x07
        self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.assertFalse(self.controller.snapshot()['enabled'])
        self.assertIsNone(self.controller.snapshot()['radio_verified_at'])
        self.assertFalse(any(frame[4] == 0x05 for frame in self.factory.devices[0].writes))


if __name__ == '__main__':
    unittest.main()
