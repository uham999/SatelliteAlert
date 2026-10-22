"""One-shot C2 diagnostics with simulated serial ports; no physical hardware."""
from datetime import datetime, timezone
import threading
import unittest
from unittest.mock import patch

from hardware import HardwareController
from orbits import validate_settings
from profiles import doppler
from test_hardware import Clock, Factory


class RotatorConnectionTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.now = Clock(), datetime(2026, 10, 8, 19, 42, 16, tzinfo=timezone.utc)
        self.factory = Factory(self.clock)
        self.settings = validate_settings({'rotator_port': 'COM10', 'radio_port': 'COM11', 'rotator_enabled': True, 'radio_enabled': True})
        self.controller = HardwareController(self.factory, lambda: [{'device': 'COM10'}, {'device': 'COM11'}],
                                             self.clock, utc_clock=lambda: self.now)

    def tearDown(self):
        self.controller.close()

    def target(self, rate=-1, azimuth=100):
        return dict(name='ISS', status='tracking', elevation=20, azimuth=azimuth, range_rate_km_s=rate,
                    doppler=doppler(self.settings['satellite_profiles']['ISS'], rate))

    def start_tracking(self):
        self.controller.offer(self.target(), self.settings)
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.assertTrue(self.controller.snapshot()['enabled'], self.controller.snapshot()['error'])

    def test_one_c2_uses_saved_settings_reports_raw_reply_time_and_closes_temporary_port(self):
        self.settings.update(rotator_enabled=False, rotator_baud=4800)
        result = self.controller.test_rotator(self.settings)
        device = self.factory.devices[0]
        self.assertEqual(device.writes, [b'C2\r'])
        self.assertEqual((device.port, device.baudrate), ('COM10', 4800))
        self.assertTrue(device.closed)
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['command_sent'])
        self.assertEqual(result['response'], 'AZ=350 EL=020\r\n')
        self.assertEqual(result['tested_at'], self.now.isoformat())
        self.assertEqual(result['position'], {'azimuth': 350, 'elevation': 20})
        state = self.controller.snapshot()
        self.assertEqual(state['rotator_test'], result)
        self.assertEqual(state['rotator_position'], result['position'])
        self.assertFalse(state['enabled'] or state['radio_test_enabled'])
        self.assertIsNone(self.controller.rotator)
        self.assertTrue(all(not dtr and not rts for dtr, rts, _ in self.factory.open_states))
        result['message'] = 'Caller changed result'
        self.assertNotEqual(self.controller.snapshot()['rotator_test']['message'], result['message'])

    def test_missing_or_unavailable_port_warns_and_records_time_without_opening_or_sending(self):
        for port, message in (('', 'No rotator port is configured'), (' ', 'No rotator port is configured'),
                              ('COM99', 'COM99 is unavailable')):
            with self.subTest(port=port):
                result = self.controller.test_rotator({**self.settings, 'rotator_port': port})
                self.assertEqual(result['status'], 'warning')
                self.assertIn(message, result['message'])
                self.assertFalse(result['command_sent'])
                self.assertEqual(result['response'], '')
                self.assertEqual(result['tested_at'], self.now.isoformat())
                self.assertEqual(self.controller.snapshot()['rotator_test'], result)
        self.assertEqual(self.factory.devices, [])

    def test_acknowledgement_clears_only_that_result_and_keeps_live_tracking(self):
        self.start_tracking()
        result = self.controller.test_rotator(self.settings)
        rotator, radio = self.controller.rotator, self.controller.radio
        writes = (rotator.writes[:], radio.writes[:])
        self.controller.acknowledge_test_result('rotator', result['id'])
        self.assertIsNone(self.controller.snapshot()['rotator_test'])
        self.assertTrue(self.controller.snapshot()['enabled'])
        self.assertEqual((rotator.writes, radio.writes), writes)
        self.controller.offer(self.target(rate=1, azimuth=102), self.settings)
        self.controller.step()
        self.assertGreater(len(radio.writes), len(writes[1]))
        next_result = self.controller.test_rotator(self.settings)
        self.assertNotEqual(result['id'], next_result['id'])
        self.controller.acknowledge_test_result('rotator', result['id'])
        self.assertEqual(self.controller.snapshot()['rotator_test'], next_result)

    def test_disabling_rotator_during_c2_cannot_republish_the_old_result(self):
        reading, release = threading.Event(), threading.Event()
        read = self.factory.rotator_reply
        original_open = self.controller._open
        def open_waiting(*args):
            device = original_open(*args)
            original_read = device.read_until
            def wait_for_reply(*read_args):
                reading.set()
                if not release.wait(2):
                    raise OSError('Test fixture wait expired')
                return original_read(*read_args)
            device.read_until = wait_for_reply
            return device
        results = []
        with patch.object(self.controller, '_open', side_effect=open_waiting):
            worker = threading.Thread(target=lambda: results.append(self.controller.test_rotator(self.settings)))
            worker.start()
            try:
                self.assertTrue(reading.wait(1))
                self.controller.set_enabled(False, {**self.settings, 'rotator_enabled': False})
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]['response'], read.decode('ascii'))
        self.assertIsNone(self.controller.snapshot()['rotator_test'])
        self.assertIsNone(self.controller.snapshot()['rotator_position'])
        self.assertTrue(self.factory.devices[-1].closed)

    def test_compact_c2_reply_succeeds_in_connection_test_and_live_tracking(self):
        self.factory.rotator_reply = b'AZ=183EL=010\r\n'
        result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['response'], 'AZ=183EL=010\r\n')
        self.assertEqual(result['position'], {'azimuth': 183, 'elevation': 10})
        self.assertEqual(result['tested_at'], self.now.isoformat())
        self.start_tracking()
        state = self.controller.snapshot()
        self.assertEqual(state['rotator_position'], result['position'])
        self.assertEqual(state['rotator_status'], 'Tracking')
        self.assertIsNone(state['rotator_error'])
        self.assertIsNone(state['error'])
        self.assertFalse(self.controller.rotator.closed)
        self.assertEqual(self.controller.rotator.writes, [b'C2\r', b'W100 020\r'])

    def test_echo_and_split_legacy_reply_are_collected_without_discarding_the_raw_response(self):
        self.factory.rotator_reply = b'C2\r\n+0350+0020\r\n'
        result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['response'], self.factory.rotator_reply.decode('ascii'))
        self.assertEqual(result['position'], {'azimuth': 350, 'elevation': 20})
        # read_until returns the echo and position on separate calls.
        self.assertAlmostEqual(self.clock.now, 100.2)

    def test_timeout_bad_reply_missing_terminator_and_noise_keep_response_and_close_port(self):
        for reply, message in ((b'', 'No response to C2'), (b'?>\r\n', 'did not return'),
                               (b'AZ=999 EL=020\r\n', 'out-of-range'),
                               (b'AZ=350 EL=020', 'missing line ending'), (b'x' * 300, '256-byte limit')):
            with self.subTest(reply=reply):
                self.factory.rotator_reply = reply
                start = self.clock.now
                result = self.controller.test_rotator(self.settings)
                self.assertEqual(result['status'], 'error')
                self.assertIn(message, result['message'])
                self.assertTrue(result['command_sent'])
                self.assertEqual(result['response'], reply[:256].decode('ascii'))
                self.assertLessEqual(self.clock.now - start, 0.9 + 1e-8)
                self.assertIsNone(result['position'])
                self.assertTrue(self.factory.devices[-1].closed)
                self.assertEqual(self.factory.devices[-1].writes, [b'C2\r'])
        self.assertIsNone(self.controller.snapshot()['error'])

    def test_open_write_and_read_failures_are_reported_with_cleanup_and_partial_response(self):
        with patch.object(self.controller, '_open', side_effect=OSError('Port in use')):
            result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['status'], 'error')
        self.assertIn('Port in use', result['message'])
        self.assertFalse(result['command_sent'])
        self.factory.incomplete = True
        result = self.controller.test_rotator(self.settings)
        self.assertIn('incomplete', result['message'])
        self.assertTrue(result['command_attempted'])
        self.assertFalse(result['command_sent'])
        self.assertTrue(self.factory.devices[-1].closed)
        self.factory.incomplete = False
        self.factory.rotator_reply = b'partial\r\n'
        device = self.factory(port=None, baudrate=9600)
        device.port = 'COM10'
        read = device.read_until
        calls = []
        def fail_after_partial(*args):
            if calls:
                raise OSError('Disconnected during read')
            calls.append(True)
            return read(*args)
        with patch.object(self.controller, '_open', return_value=device), patch.object(device, 'read_until', side_effect=fail_after_partial):
            result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['response'], 'partial\r\n')
        self.assertIn('Disconnected during read', result['message'])
        self.assertTrue(device.closed)

    def test_reuses_live_rotator_without_extra_motion_and_radio_doppler_updates_continue(self):
        self.start_tracking()
        rotator, radio = self.controller.rotator, self.controller.radio
        old_rotator_writes, old_radio_writes = rotator.writes[:], radio.writes[:]
        previous_rx, previous_tx = radio.frequencies['main'], radio.frequencies['sub']
        result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(rotator.writes[len(old_rotator_writes):], [b'C2\r'])
        self.assertEqual(radio.writes, old_radio_writes)
        self.assertEqual(len(self.factory.devices), 2)
        self.assertFalse(rotator.closed or radio.closed)
        self.assertTrue(self.controller.snapshot()['enabled'])
        self.controller.offer(self.target(rate=1, azimuth=102), self.settings)
        self.controller.step()
        self.assertLess(radio.frequencies['main'], previous_rx)
        self.assertGreater(radio.frequencies['sub'], previous_tx)
        self.assertIs(self.controller.rotator, rotator)
        self.assertEqual(self.controller.snapshot()['rotator_test'], result)

    def test_diagnostic_after_rotator_fault_does_not_restart_tracking_or_change_radio_session(self):
        self.factory.rotator_reply = b''
        self.start_tracking()
        radio, fault = self.controller.radio, self.controller.snapshot()['rotator_error']
        self.assertTrue(fault)
        self.factory.rotator_reply = b'AZ=350 EL=020\r\n'
        result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['status'], 'success')
        self.assertTrue(self.factory.devices[-1].closed)
        self.assertIsNone(self.controller.rotator)
        self.assertEqual(self.controller.snapshot()['rotator_error'], fault)
        self.assertIs(self.controller.radio, radio)
        self.controller.offer(self.target(rate=1), self.settings)
        self.controller.step()
        self.assertEqual(len(self.factory.devices), 3)
        self.assertTrue(self.controller.snapshot()['enabled'])
        self.assertIsNone(self.controller.rotator)

    def test_radio_test_continues_and_its_port_cannot_be_used_for_c2(self):
        self.controller.set_radio_test(True, self.settings)
        self.controller.step()
        radio, previous = self.controller.radio, self.controller.radio.writes[:]
        result = self.controller.test_rotator({**self.settings, 'rotator_port': 'COM11'})
        self.assertEqual(result['status'], 'warning')
        self.assertIn('in use by radio', result['message'])
        self.assertFalse(result['command_sent'])
        self.assertEqual(radio.writes, previous)
        result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['status'], 'success')
        self.assertTrue(self.controller.snapshot()['radio_test_enabled'])
        self.assertFalse(radio.closed)
        self.clock.now += 2
        self.controller.step()
        self.assertEqual(self.controller.snapshot()['radio_test_frequency_hz'], 145_555_000)

    def test_c2_does_not_interleave_with_live_pointing_while_waiting_for_a_reply(self):
        self.start_tracking()
        rotator = self.controller.rotator
        rotator.writes.clear()
        reading, release, worker_started = threading.Event(), threading.Event(), threading.Event()
        results, read = [], rotator.read_until
        def wait_for_reply(*args):
            reading.set()
            if not release.wait(2):
                raise OSError('Test fixture wait expired')
            return read(*args)
        def track():
            worker_started.set()
            self.controller.step()
        self.controller.offer(self.target(azimuth=103), self.settings)
        with patch.object(rotator, 'read_until', side_effect=wait_for_reply):
            query = threading.Thread(target=lambda: results.append(self.controller.test_rotator(self.settings)))
            worker = threading.Thread(target=track)
            query.start()
            try:
                self.assertTrue(reading.wait(1))
                worker.start()
                self.assertTrue(worker_started.wait(1))
                self.assertEqual(rotator.writes, [b'C2\r'])
            finally:
                release.set()
                query.join(2)
                if worker.ident is not None:
                    worker.join(2)
        self.assertFalse(query.is_alive() or worker.is_alive())
        self.assertEqual(results[0]['status'], 'success')
        self.assertEqual(rotator.writes, [b'C2\r', b'W103 020\r'])

    def test_stopping_app_does_not_open_a_new_test_port(self):
        self.controller.close()
        result = self.controller.test_rotator(self.settings)
        self.assertEqual(result['status'], 'error')
        self.assertIn('cancelled', result['message'])
        self.assertEqual(self.factory.devices, [])


if __name__ == '__main__':
    unittest.main()
