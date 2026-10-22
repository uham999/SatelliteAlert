"""Exercise radio connection testing with simulated CI-V devices only."""
from unittest.mock import patch
import unittest

from hardware import HardwareController, civ_frame, decode_frequency
from orbits import validate_settings
from profiles import doppler
from test_hardware import Clock, Factory


class RadioConnectionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.factory = Factory(self.clock)
        self.settings = validate_settings({'radio_port': 'COM11', 'rotator_port': 'COM10', 'radio_baud': 57600, 'rotator_enabled': True, 'radio_enabled': True})
        self.controller = HardwareController(self.factory, lambda: [{'device': 'COM10'}, {'device': 'COM11'}], self.clock)

    def tearDown(self):
        self.controller.close()

    def start_test(self):
        self.controller.set_radio_test(True, self.settings)
        self.controller.step()
        self.assertTrue(self.controller.snapshot()['radio_test_enabled'], self.controller.snapshot()['error'])
        return self.controller.radio

    @staticmethod
    def tunes(device):
        return [(when, decode_frequency(frame[5:-1])) for when, frame in zip(device.write_times, device.writes) if frame[4] == 0x05]

    def test_alternates_exact_main_frequencies_every_two_seconds_without_target_or_rotator(self):
        self.factory.echo = True
        self.settings['radio_address'] = 'A4'
        radio = self.start_test()
        first_time = self.tunes(radio)[0][0]
        self.assertEqual(self.tunes(radio)[0][1], 145_000_000)
        for elapsed in (.5, 1, 1.99):
            self.clock.now = first_time + elapsed
            self.controller.offer(None, self.settings)
            self.controller.step()
            self.assertEqual(len(self.tunes(radio)), 1)
        for index in range(1, 4):
            self.clock.now = first_time + 2 * index
            self.controller.step()
        self.assertEqual([hz for _, hz in self.tunes(radio)], [145_000_000, 145_555_000, 145_000_000, 145_555_000])
        for (before, _), (after, _) in zip(self.tunes(radio), self.tunes(radio)[1:]):
            self.assertAlmostEqual(after - before, 2.0, places=6)
        self.assertEqual(len(self.factory.devices), 1)
        self.assertEqual((radio.port, radio.parameters['baudrate']), ('COM11', 57600))
        self.assertTrue(all(frame[2] == 0xA4 for frame in radio.writes))
        self.assertIn(civ_frame(0xA4, 0x07, b'\xd0'), radio.writes)
        self.assertNotIn(civ_frame(0xA4, 0x07, b'\xd1'), radio.writes)
        self.assertFalse(any(frame[4] in (0x06, 0x09, 0x1C) for frame in radio.writes))
        self.assertTrue(all(not dtr and not rts for dtr, rts, _ in self.factory.open_states))
        state = self.controller.snapshot()
        self.assertFalse(state['enabled'])
        self.assertEqual(state['radio_test_frequency_hz'], 145_555_000)
        self.assertEqual(state['radio_rx_hz'], 145_555_000)
        self.assertIsNone(state['radio_tx_hz'])

    def test_stop_closes_port_and_sends_no_later_frequency_commands(self):
        radio = self.start_test()
        self.controller.set_radio_test(False, self.settings)
        self.assertFalse(self.controller.snapshot()['radio_test_enabled'])
        self.controller.step()
        writes = radio.writes[:]
        self.clock.now += 20
        self.controller.step()
        self.assertEqual(radio.writes, writes)
        self.assertTrue(radio.closed)
        self.assertIsNone(self.controller.snapshot()['radio_test_frequency_hz'])

    def test_completed_test_can_be_acknowledged_without_serial_commands_or_returning_on_refresh(self):
        radio = self.start_test()
        self.clock.now += 2
        self.controller.step()
        self.controller.set_radio_test(False, self.settings)
        self.controller.step()
        result = self.controller.snapshot()['radio_test_result']
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['frequency_hz'], 145_555_000)
        self.assertEqual(result['updates'], 2)
        writes = radio.writes[:]
        self.controller.acknowledge_test_result('radio', result['id'])
        self.controller.step()
        state = self.controller.snapshot()
        self.assertIsNone(state['radio_test_result'])
        self.assertIsNone(state['radio_rx_hz'])
        self.assertIsNone(state['radio_verified_target'])
        self.assertEqual(radio.writes, writes)
        self.assertFalse(state['enabled'] or state['radio_test_enabled'])

    def test_running_test_and_newer_test_cannot_be_dismissed_by_an_old_acknowledgement(self):
        radio = self.start_test()
        first = self.controller.snapshot()['radio_test_result']
        with self.assertRaisesRegex(ValueError, 'Stop the radio connection test'):
            self.controller.acknowledge_test_result('radio', first['id'])
        self.controller.set_radio_test(False, self.settings)
        self.controller.set_radio_test(True, self.settings)
        second = self.controller.snapshot()['radio_test_result']
        self.assertNotEqual(first['id'], second['id'])
        self.controller.acknowledge_test_result('radio', first['id'])
        self.assertEqual(self.controller.snapshot()['radio_test_result'], second)
        self.assertTrue(self.controller.snapshot()['radio_test_enabled'])
        self.controller.step()
        self.assertTrue(radio.closed)
        for device, result_id in (('unknown', first['id']), ('radio', None), ('rotator', ''), (None, 'test')):
            with self.assertRaises(ValueError):
                self.controller.acknowledge_test_result(device, result_id)

    def test_acknowledging_old_radio_test_preserves_current_satellite_readback(self):
        self.start_test()
        self.controller.set_radio_test(False, self.settings)
        result = self.controller.snapshot()['radio_test_result']
        row = dict(name='ISS', status='tracking', elevation=20, azimuth=100, range_rate_km_s=-1)
        row['doppler'] = doppler(self.settings['satellite_profiles']['ISS'], -1)
        self.controller.offer(row, self.settings)
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        before = self.controller.snapshot()
        self.controller.acknowledge_test_result('radio', result['id'])
        after = self.controller.snapshot()
        self.assertTrue(after['enabled'])
        self.assertEqual(after['radio_verified_target'], 'ISS')
        self.assertEqual(after['radio_rx_hz'], before['radio_rx_hz'])
        self.assertEqual(after['radio_tx_hz'], before['radio_tx_hz'])

    def test_disabling_devices_clears_both_results_and_readbacks_until_new_tests(self):
        radio = self.start_test()
        self.controller.set_radio_test(False, self.settings)
        self.controller.test_rotator(self.settings)
        disabled = {**self.settings, 'rotator_enabled': False, 'radio_enabled': False}
        self.controller.set_enabled(False, disabled)
        self.controller.step()
        state = self.controller.snapshot()
        for key in ('rotator_test', 'radio_test_result', 'rotator_position', 'commanded_position',
                    'radio_rx_hz', 'radio_tx_hz', 'radio_verified_at', 'radio_verified_target'):
            self.assertIsNone(state[key], key)
        self.assertTrue(radio.closed)
        self.controller.set_enabled(False, self.settings)
        self.assertIsNone(self.controller.snapshot()['rotator_test'])
        self.assertIsNone(self.controller.snapshot()['radio_test_result'])

    def test_failed_radio_test_has_dismissible_result_and_clears_its_own_error(self):
        self.factory.no_ack = True
        self.controller.set_radio_test(True, self.settings)
        self.controller.step()
        state = self.controller.snapshot()
        result = state['radio_test_result']
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['message'], state['error'])
        self.assertFalse(state['radio_test_enabled'])
        self.controller.acknowledge_test_result('radio', result['id'])
        self.assertIsNone(self.controller.snapshot()['radio_test_result'])
        self.assertIsNone(self.controller.snapshot()['error'])

    def test_vhf_on_sub_is_exchanged_onto_main_before_test_tuning(self):
        self.factory.initial_frequencies = {'main': 435_000_000, 'sub': 145_500_000}
        self.factory.reject_same_band = True
        radio = self.start_test()
        self.assertIn(civ_frame(0xA2, 0x07, b'\xb0'), radio.writes)
        self.assertEqual(radio.selected, 'main')
        self.assertEqual(radio.frequencies['main'], 145_000_000)
        self.assertEqual(radio.frequencies['sub'], 435_000_000)

    def test_starting_test_stops_tracking_and_rotator_without_automatic_resume(self):
        row = dict(name='ISS', status='tracking', elevation=20, azimuth=100, range_rate_km_s=-1)
        row['doppler'] = doppler(self.settings['satellite_profiles']['ISS'], -1)
        self.controller.offer(row, self.settings)
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        rotator, previous_radio = self.controller.rotator, self.controller.radio
        self.start_test()
        self.assertTrue(rotator.closed and previous_radio.closed)
        self.assertIn(b'S\r', rotator.writes)
        with self.assertRaisesRegex(ValueError, 'Stop the radio connection test'):
            self.controller.set_enabled(True, self.settings)
        self.assertTrue(self.controller.snapshot()['radio_test_enabled'])
        self.controller.set_radio_test(False, self.settings)
        self.controller.step()
        self.assertFalse(self.controller.snapshot()['enabled'])
        self.controller.offer(row, self.settings)
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.assertTrue(self.controller.snapshot()['enabled'])

    def test_acknowledgement_errors_bad_readback_and_open_failures_stop_test(self):
        for attribute, value in (('no_ack', True), ('reject_command', 0x05), ('readback_offset', 100), ('incomplete', True)):
            with self.subTest(attribute=attribute):
                setattr(self.factory, attribute, value)
                self.controller.set_radio_test(True, self.settings)
                self.controller.step()
                state = self.controller.snapshot()
                self.assertFalse(state['radio_test_enabled'])
                self.assertTrue(state['error'])
                self.assertTrue(all(device.closed for device in self.factory.devices))
                setattr(self.factory, attribute, None if attribute == 'reject_command' else 0 if attribute == 'readback_offset' else False)
        with patch.object(self.controller, '_open', side_effect=OSError('Port in use')):
            self.controller.set_radio_test(True, self.settings)
            self.controller.step()
        self.assertFalse(self.controller.snapshot()['radio_test_enabled'])
        self.assertIn('Port in use', self.controller.snapshot()['error'])

    def test_missing_port_invalid_toggle_and_repeated_start(self):
        with self.assertRaises(ValueError):
            self.controller.set_radio_test(True, {**self.settings, 'radio_port': 'COM99'})
        for value in (None, 1, 'true'):
            with self.assertRaises(ValueError):
                self.controller.set_radio_test(value, self.settings)
        self.assertEqual(self.factory.devices, [])
        self.settings['radio_enabled'] = False  # Explicit testing is independent of live tracking choices.
        radio = self.start_test()
        self.controller.set_radio_test(True, self.settings)
        self.controller.step()
        self.assertEqual(len(self.factory.devices), 1)
        self.assertEqual(len(self.tunes(radio)), 1)

    def test_stop_while_waiting_for_reply_cancels_remaining_commands(self):
        radio = self.start_test()
        write = radio.write
        def stop_after_tune(frame):
            count = write(frame)
            if frame[4] == 0x05:
                self.controller.set_radio_test(False, self.settings)
            return count
        with patch.object(radio, 'write', side_effect=stop_after_tune):
            previous_reads = sum(frame[4] == 0x03 for frame in radio.writes)
            self.clock.now += 2
            self.controller.step()
        self.assertEqual(sum(frame[4] == 0x03 for frame in radio.writes), previous_reads)
        self.assertFalse(self.controller.snapshot()['radio_test_enabled'])
        self.assertTrue(radio.closed)

    def test_rapid_stop_restart_cancels_old_session_and_starts_new_one(self):
        radio = self.start_test()
        write = radio.write
        def restart_after_tune(frame):
            count = write(frame)
            if frame[4] == 0x05:
                self.controller.set_radio_test(False, self.settings)
                self.controller.set_radio_test(True, self.settings)
            return count
        with patch.object(radio, 'write', side_effect=restart_after_tune):
            self.clock.now += 2
            self.controller.step()
        self.assertTrue(radio.closed)
        self.assertTrue(self.controller.snapshot()['radio_test_enabled'])
        self.controller.step()
        self.assertIsNot(self.controller.radio, radio)
        self.assertEqual(self.tunes(self.controller.radio)[0][1], 145_000_000)

    def test_close_stops_test_and_a_new_controller_never_resumes_it(self):
        radio = self.start_test()
        self.controller.close()
        self.assertTrue(radio.closed)
        self.assertFalse(self.controller.snapshot()['radio_test_enabled'])
        fresh = HardwareController(self.factory, lambda: [{'device': 'COM11'}], self.clock)
        fresh.offer(None, self.settings)
        fresh.step()
        self.assertFalse(fresh.snapshot()['radio_test_enabled'])
        self.assertEqual(len(self.factory.devices), 1)
        fresh.close()


if __name__ == '__main__':
    unittest.main()
