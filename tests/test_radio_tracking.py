"""IC-9700 band constraints during actual-pass tuning, using simulated CI-V."""
from datetime import datetime, timezone
from itertools import permutations
import unittest

from hardware import HardwareController, RADIO_MANUAL_SETTLE_SECONDS, civ_frame, decode_frequency
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

    @staticmethod
    def tune_count(radio):
        return sum(frame[4] == 0x05 for frame in radio.writes)

    def start_pass(self):
        row = self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.verify(row)
        return self.controller.radio

    def test_manual_tuning_pauses_both_frequencies_and_resumes_latest_doppler_after_five_stable_seconds(self):
        self.assertEqual(RADIO_MANUAL_SETTLE_SECONDS, 5)
        radio = self.start_pass()
        count = self.tune_count(radio)
        radio.frequencies['main'] += 3500
        self.offer(rate=2)
        self.controller.step()
        started = self.controller.radio_manual_changed_at
        state = self.controller.snapshot()
        self.assertTrue(state['enabled'])
        self.assertIsNone(state['error'])
        self.assertTrue(state['radio_manual_override'])
        self.assertEqual(state['radio_manual_resume_seconds'], 5)
        self.assertEqual(state['radio_observed_rx_hz'], radio.frequencies['main'])
        for elapsed in (1, 2, 3, 4, 4.7):
            self.clock.now = started + elapsed
            self.offer(rate=elapsed)
            self.controller.step()
            self.assertEqual(self.tune_count(radio), count)
            self.assertTrue(self.controller.snapshot()['radio_manual_override'])
        self.clock.now = started + 5
        latest = self.offer(rate=3)
        self.controller.step()
        self.verify(latest)
        self.assertEqual(self.tune_count(radio), count + 2)
        self.assertFalse(self.controller.snapshot()['radio_manual_override'])
        self.assertEqual(self.controller.snapshot()['radio_manual_resume_seconds'], 0)
        self.assertIs(self.controller.radio, radio)

    def test_each_change_to_either_band_restarts_timer_and_sub_selection_is_preserved(self):
        radio = self.start_pass()
        count = self.tune_count(radio)
        radio.selected = 'sub'
        radio.frequencies['sub'] -= 1000
        self.offer();self.controller.step()
        self.assertEqual(radio.selected, 'sub')
        first = self.controller.radio_manual_changed_at
        self.clock.now = first + 4
        radio.frequencies['main'] += 100
        self.offer();self.controller.step()
        second = self.controller.radio_manual_changed_at
        self.assertGreater(second, first)
        self.clock.now = second + 4
        # Returning to the original pair is still a change: it must not resume early.
        radio.frequencies['main'], radio.frequencies['sub'] = self.controller.last_radio[1:]
        self.offer();self.controller.step()
        third = self.controller.radio_manual_changed_at
        self.assertGreater(third, second)
        self.assertEqual(self.tune_count(radio), count)
        self.clock.now = third + 4
        self.offer();self.controller.step()
        self.assertTrue(self.controller.snapshot()['radio_manual_override'])
        self.assertEqual(radio.selected, 'sub')
        self.clock.now = third + 5
        latest = self.offer(rate=2)
        self.controller.step()
        self.verify(latest)
        self.assertEqual(radio.selected, 'main')

    def test_manual_change_is_detected_even_when_doppler_is_inside_command_deadband(self):
        radio = self.start_pass()
        count = self.tune_count(radio)
        radio.frequencies['main'] += 20
        self.offer();self.controller.step()
        self.assertTrue(self.controller.snapshot()['radio_manual_override'])
        self.assertEqual(self.tune_count(radio), count)
        self.clock.now = self.controller.radio_manual_changed_at + 5
        row = self.offer();self.controller.step()
        self.verify(row)
        self.assertEqual(self.tune_count(radio), count + 2)

    def test_receive_only_polling_does_not_write_an_uplink_or_steal_sub_selection_during_pause(self):
        self.settings['satellite_profiles']['ISS']['uplink_mhz'] = None
        radio = self.start_pass()
        radio.selected = 'sub'
        radio.frequencies['main'] += 500
        self.offer();self.controller.step()
        self.assertEqual(radio.selected, 'sub')
        self.assertIsNone(self.controller.snapshot()['radio_observed_tx_hz'])
        count = self.tune_count(radio)
        self.clock.now = self.controller.radio_manual_changed_at + 5
        row = self.offer(rate=1);self.controller.step()
        self.verify(row)
        self.assertEqual(self.tune_count(radio), count + 1)

    def test_rotator_continues_pointing_while_radio_frequency_writes_are_paused(self):
        self.settings.update(rotator_enabled=True, rotator_port='COM10')
        self.controller.ports_provider = lambda: [{'device': 'COM10'}, {'device': 'COM11'}]
        radio = self.start_pass()
        rotator = self.controller.rotator
        initial_directions = len([command for command in rotator.writes if command.startswith(b'W')])
        count = self.tune_count(radio)
        radio.frequencies['main'] += 1000
        self.offer();self.controller.step()
        started = self.controller.radio_manual_changed_at
        for elapsed in (1, 2, 3):
            self.clock.now = started + elapsed
            row = self.offer(rate=elapsed)
            row['azimuth'] += elapsed * 2
            self.controller.offer(row, self.settings)
            self.controller.step()
        self.assertEqual(self.tune_count(radio), count)
        self.assertGreater(len([command for command in rotator.writes if command.startswith(b'W')]), initial_directions)
        self.assertFalse(rotator.closed or radio.closed)
        self.assertTrue(self.controller.snapshot()['enabled'])

    def test_target_handover_during_manual_pause_waits_then_tunes_the_latest_satellite(self):
        radio = self.start_pass()
        radio.frequencies['main'] += 1000
        self.offer();self.controller.step()
        started, count = self.controller.radio_manual_changed_at, self.tune_count(radio)
        self.clock.now = started + 3
        self.offer('AO-73');self.controller.step()
        self.assertEqual(self.tune_count(radio), count)
        self.assertTrue(self.controller.snapshot()['radio_manual_override'])
        self.clock.now = started + 5
        latest = self.offer('AO-73', rate=2)
        self.controller.step()
        self.verify(latest)

    def test_manual_change_during_an_inflight_tune_enters_pause_instead_of_stopping_control(self):
        radio = self.start_pass()
        original_write = radio.write
        changed = []
        def tune_manually(frame):
            length = original_write(frame)
            if frame[4] == 0x05 and not changed:
                radio.frequencies[radio.selected] += 1000
                changed.append(True)
            return length
        radio.write = tune_manually
        self.offer(rate=2);self.controller.step()
        self.assertTrue(changed)
        self.assertTrue(self.controller.snapshot()['enabled'])
        self.assertTrue(self.controller.snapshot()['radio_manual_override'])
        self.assertIsNone(self.controller.snapshot()['error'])
        radio.write = original_write
        self.clock.now = self.controller.radio_manual_changed_at + 5
        row = self.offer(rate=3);self.controller.step()
        self.verify(row)

    def test_no_retune_after_pass_ends_or_positions_go_stale_and_explicit_stop_clears_pause(self):
        radio = self.start_pass()
        radio.frequencies['main'] += 500
        self.offer();self.controller.step()
        started, count = self.controller.radio_manual_changed_at, self.tune_count(radio)
        self.clock.now = started + 10
        self.controller.step()  # No fresh orbit offer: the previous position is stale.
        self.assertEqual(self.tune_count(radio), count)
        self.assertFalse(self.controller.snapshot()['radio_manual_override'])
        row = self.offer();self.controller.step()
        self.verify(row)
        radio.frequencies['main'] += 500
        self.offer();self.controller.step()
        self.controller.set_enabled(False, self.settings)
        self.controller.step()
        self.assertFalse(self.controller.snapshot()['radio_manual_override'])
        self.assertEqual(self.controller.snapshot()['radio_manual_resume_seconds'], 0)
        self.assertTrue(radio.closed)

    def test_lost_ci_v_reply_during_manual_pause_still_reports_connection_failure(self):
        radio = self.start_pass()
        radio.frequencies['main'] += 500
        self.offer();self.controller.step()
        self.factory.no_ack = True
        self.offer();self.controller.step()
        self.assertFalse(self.controller.snapshot()['enabled'])
        self.assertFalse(self.controller.snapshot()['radio_manual_override'])
        self.assertIn('did not acknowledge', self.controller.snapshot()['error'])
        self.assertTrue(radio.closed)


if __name__ == '__main__':
    unittest.main()
