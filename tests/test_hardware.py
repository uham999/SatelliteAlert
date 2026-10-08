"""Offline serial-device simulators; never opens a physical port or keys PTT."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch
import numpy as np
from skyfield.api import wgs84

from hardware import (HardwareController, angle_change, civ_frame, decode_frequency,
                      frequency_bcd, parse_rotator_position, select_target, radio_band)
from orbits import DEFAULT_SETTINGS, OrbitCalculator, parse_tles, validate_settings, settings_for_startup
from profiles import DEFAULT_PROFILES, doppler
from satellite_alert import Monitor, atomic_json

FIXTURE = (Path(__file__).parent / "fixtures/iss.tle").read_text()


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Device:
    def __init__(self, clock, owner, **parameters):
        self.clock, self.owner, self.parameters = clock, owner, parameters
        self.port, self.dtr, self.rts = None, True, True
        self.opened, self.closed = False, False
        self.writes, self.write_times, self.buffer = [], [], bytearray()
        self.selected = "main"
        self.frequencies = dict(owner.initial_frequencies)

    def open(self):
        self.opened = True
        self.owner.open_states.append((self.dtr, self.rts, self.parameters))

    def close(self):
        self.closed = True

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, data):
        self.writes.append(data)
        self.write_times.append(self.clock())
        if self.owner.incomplete:
            return len(data) - 1
        if self.port == "COM10":
            if data == b"C2\r":
                self.buffer.extend(self.owner.rotator_reply)
            elif data.startswith(b"W"):
                self.buffer.extend(b"\r")
        elif self.port == "COM11":
            command, payload, address = data[4], data[5:-1], data[2]
            if self.owner.no_ack:
                return len(data)
            if command == self.owner.reject_command:
                self.buffer.extend(bytes((0xFE, 0xFE, 0xE0, address, 0xFA, 0xFD)))
                return len(data)
            if command == 0x07 and payload in (b"\xd0", b"\xd1"):
                self.selected = "main" if payload == b"\xd0" else "sub"
            if command == 0x07 and payload == b"\xb0":
                self.frequencies['main'], self.frequencies['sub'] = self.frequencies['sub'], self.frequencies['main']
            if command == 0x05:
                other = 'sub' if self.selected == 'main' else 'main'
                if self.owner.reject_same_band and radio_band(decode_frequency(payload)) == radio_band(self.frequencies[other]):
                    self.buffer.extend(bytes((0xFE, 0xFE, 0xE0, address, 0xFA, 0xFD)))
                    return len(data)
                self.frequencies[self.selected] = decode_frequency(payload)
            if self.owner.echo:
                self.buffer.extend(data)
                # An unsolicited broadcast from the radio to all stations.
                self.buffer.extend(bytes((0xFE, 0xFE, 0x00, address, 0x03)) + frequency_bcd(145_000_000) + b"\xfd")
            if command == 0x03:
                hz = self.frequencies[self.selected] + self.owner.readback_offset
                self.buffer.extend(bytes((0xFE, 0xFE, 0xE0, address, 0x03)) + frequency_bcd(hz) + b"\xfd")
            else:
                self.buffer.extend(bytes((0xFE, 0xFE, 0xE0, address, 0xFB, 0xFD)))
        return len(data)

    def read(self, size):
        self.clock.now += 0.001 if self.buffer else 0.1
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def read_until(self, terminator, size):
        self.clock.now += 0.1
        end = self.buffer.find(terminator)
        length = min(size, end + len(terminator) if end >= 0 else len(self.buffer))
        value = bytes(self.buffer[:length])
        del self.buffer[:length]
        return value


class Factory:
    def __init__(self, clock):
        self.clock = clock
        self.devices, self.open_states = [], []
        self.incomplete, self.echo, self.no_ack = False, False, False
        self.reject_command, self.readback_offset = None, 0
        self.initial_frequencies = {"main": 145_500_000, "sub": 435_000_000}
        self.reject_same_band = False
        self.rotator_reply = b"AZ=350 EL=020\r\n"

    def __call__(self, **parameters):
        device = Device(self.clock, self, **parameters)
        self.devices.append(device)
        return device


class ProtocolTests(unittest.TestCase):
    def test_correct_five_byte_frequency_example_and_roundtrip(self):
        self.assertEqual(civ_frame(0xA2, 0x05, frequency_bcd(145_500_000)), bytes.fromhex("FE FE A2 E0 05 00 00 50 45 01 FD"))
        for hz in (0, 145_990_123, 437_800_999, 1_296_123_456):
            self.assertEqual(decode_frequency(frequency_bcd(hz)), hz)
        for data in (b"\x00" * 4, bytes.fromhex("FA 00 00 00 00")):
            with self.assertRaises(ValueError):
                decode_frequency(data)

    def test_rotator_report_formats_and_limits(self):
        self.assertEqual(parse_rotator_position(b"\r\nAZ=123 EL=045\r"), {"azimuth": 123, "elevation": 45})
        self.assertEqual(parse_rotator_position(b"+0365+0080\r"), {"azimuth": 365, "elevation": 80})
        for data in (b"?>", b"AZ=999 EL=045", b"AZ=123 EL=999"):
            with self.assertRaises(OSError):
                parse_rotator_position(data)
        self.assertAlmostEqual(angle_change(359.8, .2), .4)

    def test_doppler_approaching_receding_and_zero(self):
        profile = DEFAULT_PROFILES["ISS"]
        approach, retreat, closest = (doppler(profile, rate) for rate in (-7, 7, 0))
        self.assertGreater(approach["rx_hz"], approach["rx_base_hz"])
        self.assertLess(approach["tx_hz"], approach["tx_base_hz"])
        self.assertLess(retreat["rx_hz"], retreat["rx_base_hz"])
        self.assertGreater(retreat["tx_hz"], retreat["tx_base_hz"])
        self.assertEqual(closest["rx_shift_hz"], 0)
        self.assertAlmostEqual(approach["rx_shift_hz"], 437_800_000 * 7 / 299792.458, delta=1)
        self.assertIsNone(doppler({"downlink_mhz": 145.935, "uplink_mhz": None}, 0)["tx_hz"])
        for rate in (float("nan"), 40):
            with self.assertRaises(ValueError):
                doppler(profile, rate)

    def test_skyfield_range_rate_matches_independent_range_difference(self):
        calculator = OrbitCalculator()
        satellite = calculator.satellite(parse_tles(FIXTURE)[0][25544])
        observer = calculator.observer(DEFAULT_SETTINGS)
        epoch = satellite.epoch.utc_datetime()
        for offset in (0, 600, 1800, 3600):
            now = epoch + timedelta(seconds=offset)
            measured = calculator.position(satellite, observer, now)["range_rate_km_s"]
            before = calculator.position(satellite, observer, now - timedelta(seconds=.5))["range_km"]
            after = calculator.position(satellite, observer, now + timedelta(seconds=.5))["range_km"]
            self.assertAlmostEqual(measured, after - before, delta=.002)

    def test_geographic_map_position_reconstructs_the_propagated_orbit(self):
        calculator = OrbitCalculator()
        satellite = calculator.satellite(parse_tles(FIXTURE)[0][25544])
        observer = calculator.observer(DEFAULT_SETTINGS)
        for offset in (0, 600, 3600):
            now = satellite.epoch.utc_datetime() + timedelta(seconds=offset)
            row = calculator.position(satellite, observer, now, ground=True)
            self.assertTrue(-90 <= row["ground_latitude"] <= 90)
            self.assertTrue(-180 <= row["ground_longitude"] <= 180)
            self.assertTrue(300 < row["satellite_height_km"] < 500)
            moment = calculator.ts.from_datetime(now)
            mapped = wgs84.latlon(row["ground_latitude"], row["ground_longitude"], row["satellite_height_km"] * 1000).at(moment)
            self.assertLess(np.linalg.norm(mapped.position.km - satellite.at(moment).position.km), .00001)

    def test_defaults_profile_crud_validation_and_hardware_settings(self):
        settings = validate_settings({})
        self.assertEqual(settings["rotator_baud"], 9600)
        self.assertEqual(settings["radio_port"], "COM6")
        self.assertEqual(settings["radio_baud"], 57600)
        self.assertEqual(settings["radio_address"], "A2")
        self.assertEqual(settings["satellite_profiles"]["AO-123"]["downlink_mhz"], 435.4)
        saved = validate_settings({"satellites": ["ISS", "12345"], "satellite_profiles": {"12345": {"downlink_mhz": 145.935, "uplink_mhz": None}}})
        self.assertEqual(set(saved["satellite_profiles"]), {"ISS", "12345"})
        self.assertEqual(saved["satellite_profiles"]["12345"]["downlink_mhz"], 145.935)
        for data in ({"radio_baud": 4800}, {"radio_address": "E0"}, {"radio_address": "FA"}, {"rotator_port": "COM4", "radio_port": "com4"}, {"satellite_profiles": {"ISS": {"downlink_mhz": float("nan")}}}, {"satellite_profiles": {"ISS": {"uplink_mode": "BAD"}}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_settings(data)

    def test_unset_serial_defaults_and_conditional_rotator_port(self):
        ports = [{"device": "COM3", "description": "Simulated rotator"}]
        for saved in ({}, {"radio_port": "", "rotator_port": "", "radio_baud": "", "rotator_baud": None}):
            with self.subTest(saved=saved):
                settings = settings_for_startup(saved, ports)
                self.assertEqual((settings["radio_port"], settings["radio_baud"]), ("COM6", 57600))
                self.assertEqual((settings["rotator_port"], settings["rotator_baud"]), ("COM3", 9600))
        self.assertEqual(settings_for_startup({}, [])["rotator_port"], "")
        self.assertEqual(settings_for_startup({"radio_port": "COM3"}, ports)["rotator_port"], "")

    def test_existing_port_and_baud_choices_are_preserved(self):
        saved = {"radio_port": "COM11", "radio_baud": 115200, "rotator_port": "COM10", "rotator_baud": 4800}
        settings = settings_for_startup(saved, [{"device": "COM3"}])
        self.assertTrue(all(settings[key] == value for key, value in saved.items()))
        old_defaults = settings_for_startup({"radio_port": "", "radio_baud": 9600}, [])
        self.assertEqual(old_defaults["radio_port"], "COM6")
        self.assertEqual(old_defaults["radio_baud"], 9600)

    def test_highest_current_target_ignores_stale_below_horizon_and_peak(self):
        settings = validate_settings({})
        rows = [dict(name=name, status=status, elevation=el, azimuth=20, range_rate_km_s=0, peak_elevation=peak) for name, status, el, peak in (("A", "tracking", 10, 89), ("B", "tracking", 20, 30), ("C", "stale", 70, 90), ("D", "tracking", -2, 90))]
        self.assertEqual(select_target(rows, settings)["name"], "B")
        settings["tracking_minimum_mode"] = "threshold"
        self.assertIsNone(select_target(rows, settings))
        rows[0]["elevation"] = rows[1]["elevation"] = 40
        self.assertEqual(select_target(rows, settings)["name"], "A")


class SerialTrackingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.factory = Factory(self.clock)
        self.settings = validate_settings({"rotator_port": "COM10", "radio_port": "COM11", "rotator_max_azimuth": 450})
        self.controller = HardwareController(self.factory, lambda: [{"device": "COM10"}, {"device": "COM11"}], self.clock)
        self.row = dict(name="ISS", status="tracking", elevation=20, azimuth=359.5, range_rate_km_s=-5)
        self.row["doppler"] = doppler(self.settings["satellite_profiles"]["ISS"], -5)

    def tearDown(self):
        self.controller.close()

    def offer(self):
        self.controller.offer(self.row, self.settings)

    def start(self):
        self.offer()
        self.controller.set_enabled(True, self.settings)
        self.controller.step()
        self.assertTrue(self.controller.snapshot()["enabled"], self.controller.snapshot()["error"])

    def directions(self):
        return [data for data in self.controller.rotator.writes if data.startswith(b"W")]

    def test_off_never_opens_serial_even_with_targets(self):
        self.offer()
        self.controller.step()
        self.assertEqual(self.factory.devices, [])
        with self.assertRaises(ValueError):
            self.controller.set_enabled(True, {**self.settings, "radio_port": "COM99"})

    def test_simulated_full_duplex_initial_tune_readback_and_modem_lines(self):
        self.start()
        state = self.controller.snapshot()
        self.assertEqual(state["radio_rx_hz"], self.row["doppler"]["rx_hz"])
        self.assertEqual(state["radio_tx_hz"], self.row["doppler"]["tx_hz"])
        self.assertEqual(self.directions(), [b"W360 020\r"])
        self.assertEqual(state["rotator_position"], {"azimuth": 350, "elevation": 20})
        self.assertTrue(all(not dtr and not rts and p["bytesize"] == 8 and p["parity"] == "N" and p["stopbits"] == 1 for dtr, rts, p in self.factory.open_states))
        self.assertFalse(any(frame[4] == 0x1C for frame in self.controller.radio.writes))  # No PTT command.

    def test_one_degree_deadband_either_axis_accumulates_and_wraps(self):
        self.settings["radio_enabled"] = False
        self.start()
        self.row["azimuth"] = .3  # .8 degrees across north, below threshold.
        self.row["elevation"] = 20.9
        self.offer(); self.controller.step()
        self.assertEqual(len(self.directions()), 1)
        self.row["elevation"] = 21
        self.offer(); self.controller.step()
        self.assertEqual(self.directions()[-1], b"W360 021\r")
        self.row["azimuth"] = 1.3
        self.offer(); self.controller.step()
        self.assertEqual(self.directions()[-1], b"W361 021\r")
        self.assertEqual(len(self.directions()), 3)

    def test_overlap_limit_selects_continuous_direction(self):
        self.settings["radio_enabled"] = False
        self.start()
        self.row["azimuth"] = 2
        self.offer(); self.controller.step()
        self.assertEqual(self.directions()[-1], b"W362 020\r")
        self.assertLessEqual(self.controller.snapshot()["commanded_position"]["azimuth"], 450)

    def test_frequency_deadband_and_target_handover(self):
        self.start()
        count = self.controller.snapshot()["frequency_updates"]
        self.row["doppler"]["rx_hz"] += 9
        self.row["doppler"]["tx_hz"] -= 9
        self.offer(); self.controller.step()
        self.assertEqual(self.controller.snapshot()["frequency_updates"], count)
        self.row["doppler"]["rx_hz"] += 1
        self.offer(); self.controller.step()
        self.assertEqual(self.controller.snapshot()["frequency_updates"], count + 1)
        self.row["name"] = "SO-50"
        self.row["doppler"] = doppler(self.settings["satellite_profiles"]["SO-50"], 2)
        self.offer(); self.controller.step()
        self.assertEqual(self.controller.snapshot()["radio_rx_hz"], self.row["doppler"]["rx_hz"])

    def test_echoes_broadcasts_and_fragmented_replies(self):
        self.factory.echo = True
        self.start()
        self.assertIn("verified", self.controller.snapshot()["radio_status"])

    def test_no_target_sends_one_stop_then_waits_without_frequency_writes(self):
        self.start()
        rotor, radio = self.controller.rotator, self.controller.radio
        count = len(radio.writes)
        self.controller.offer(None, self.settings)
        self.controller.step(); self.controller.step()
        self.assertEqual(rotor.writes.count(b"S\r"), 1)
        self.assertEqual(len(radio.writes), count)

    def test_stale_observation_stops_motion(self):
        self.start()
        rotor = self.controller.rotator
        self.clock.now += 7
        self.controller.step()
        self.assertIn(b"S\r", rotor.writes)
        self.assertIn("fresh", self.controller.snapshot()["rotator_status"])

    def test_switch_off_stops_and_closes_both_ports(self):
        self.start()
        rotor, radio = self.controller.rotator, self.controller.radio
        self.controller.set_enabled(False, self.settings)
        self.controller.step()
        self.assertIn(b"S\r", rotor.writes)
        self.assertTrue(rotor.closed and radio.closed)

    def test_failed_stop_closes_ports_and_resets_pointing_state_for_restart(self):
        self.start()
        rotor, radio = self.controller.rotator, self.controller.radio
        self.controller.set_enabled(False, self.settings)
        with patch.object(rotor, "write", side_effect=OSError("Disconnected")):
            self.controller.step()
        self.assertTrue(rotor.closed and radio.closed)
        self.assertFalse(self.controller.moving)
        self.assertIsNone(self.controller.last_direction)
        self.assertIn("Stop it at the controller", self.controller.snapshot()["error"])
        self.start()
        self.assertTrue(self.controller.snapshot()["enabled"])
        self.assertIsNotNone(self.controller.last_direction)

    def test_radio_rejection_timeout_and_wrong_readback_stop_both(self):
        for attribute, value in (("reject_command", 0x05), ("no_ack", True), ("readback_offset", 100)):
            with self.subTest(attribute=attribute):
                setattr(self.factory, attribute, value)
                self.offer(); self.controller.set_enabled(True, self.settings); self.controller.step()
                self.assertFalse(self.controller.snapshot()["enabled"])
                self.assertTrue(self.controller.snapshot()["error"])
                self.assertTrue(all(d.closed for d in self.factory.devices))
                setattr(self.factory, attribute, None if attribute == "reject_command" else False if attribute == "no_ack" else 0)

    def test_bad_rotator_reply_keeps_radio_but_incomplete_radio_write_stops_tracking(self):
        self.factory.rotator_reply = b"garbage\r"
        self.offer(); self.controller.set_enabled(True, self.settings); self.controller.step()
        self.assertTrue(self.controller.snapshot()["enabled"])
        self.assertIn("C2", self.controller.snapshot()["rotator_error"])
        self.assertIn("verified", self.controller.snapshot()["radio_status"])
        self.factory.rotator_reply = b"AZ=100 EL=020\r"
        self.factory.incomplete = True
        self.offer(); self.controller.set_enabled(True, self.settings); self.controller.step()
        self.assertFalse(self.controller.snapshot()["enabled"])

    def test_missing_c2_stops_rotator_and_keeps_both_radio_doppler_updates(self):
        self.start()
        rotator, radio = self.controller.rotator, self.controller.radio
        self.factory.rotator_reply = b''
        self.clock.now += 5
        self.row['doppler']['rx_hz'] += 200
        self.row['doppler']['tx_hz'] -= 200
        self.offer(); self.controller.step()
        self.assertTrue(rotator.closed)
        self.assertIn(b'S\r', rotator.writes)
        self.assertFalse(radio.closed)
        self.assertTrue(self.controller.snapshot()['enabled'])
        self.assertIn('C2', self.controller.snapshot()['rotator_error'])
        self.assertEqual(self.controller.snapshot()['radio_rx_hz'], self.row['doppler']['rx_hz'])
        self.assertEqual(self.controller.snapshot()['radio_tx_hz'], self.row['doppler']['tx_hz'])
        before = self.controller.snapshot()['frequency_updates']
        self.row['doppler']['rx_hz'] -= 500
        self.row['doppler']['tx_hz'] += 500
        self.offer(); self.controller.step()
        self.assertEqual(len(self.factory.devices), 2)  # No repeated attempts to reopen the failed rotator.
        self.assertEqual(self.controller.snapshot()['frequency_updates'], before + 1)
        self.assertEqual(self.controller.snapshot()['radio_rx_hz'], self.row['doppler']['rx_hz'])
        self.assertEqual(self.controller.snapshot()['radio_tx_hz'], self.row['doppler']['tx_hz'])
        self.factory.rotator_reply = b'AZ=100 EL=020\r'
        self.controller.set_enabled(False, self.settings);self.controller.step()
        self.start()
        self.assertIsNone(self.controller.snapshot()['rotator_error'])

    def test_rotator_only_c2_failure_stops_master_control(self):
        self.settings['radio_enabled'] = False
        self.factory.rotator_reply = b''
        self.offer();self.controller.set_enabled(True, self.settings);self.controller.step()
        self.assertFalse(self.controller.snapshot()['enabled'])
        self.assertTrue(all(device.closed for device in self.factory.devices))

    def test_c2_timeout_does_not_send_radio_an_observation_that_expired_during_the_query(self):
        self.factory.rotator_reply = b''
        self.offer();self.controller.set_enabled(True, self.settings)
        self.clock.now += 5.5
        self.controller.step()
        self.assertTrue(self.controller.snapshot()['enabled'])
        self.assertEqual(self.controller.snapshot()['frequency_updates'], 0)
        self.assertIn('fresh', self.controller.snapshot()['radio_status'])

    def test_receive_only_uses_main_vfo_and_no_sub_frequency(self):
        self.settings["satellite_profiles"]["ISS"]["uplink_mhz"] = None
        self.row["doppler"] = doppler(self.settings["satellite_profiles"]["ISS"], 0)
        self.start()
        self.assertIsNone(self.controller.snapshot()["radio_tx_hz"])
        self.assertNotIn(civ_frame(0xA2, 0x07, b"\xd1"), self.controller.radio.writes)

    def test_latest_target_replaces_pending_commands(self):
        self.offer()
        self.row["azimuth"] = 100
        self.offer()
        self.row["azimuth"] = 200
        self.start()
        self.assertEqual(self.directions(), [b"W200 020\r"])

    def test_unsupported_frequency_and_same_band_duplex_are_rejected(self):
        self.row["doppler"]["rx_hz"] = 29_400_000
        self.offer(); self.controller.set_enabled(True, self.settings); self.controller.step()
        self.assertFalse(self.controller.snapshot()["enabled"])
        self.assertIn("supported", self.controller.snapshot()["error"])
        self.row["doppler"]["rx_hz"] = 145_900_000
        self.row["doppler"]["tx_hz"] = 145_990_000
        self.offer(); self.controller.set_enabled(True, self.settings); self.controller.step()
        self.assertFalse(self.controller.snapshot()["enabled"])
        self.assertIn("different bands", self.controller.snapshot()["error"])


class MonitorEquipmentTests(unittest.TestCase):
    def test_startup_port_defaults_persist_and_keep_saved_baud_and_location(self):
        for initial in (None, {"radio_baud": 9600, "observer_height_m": 100}):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                if initial is not None:
                    atomic_json(root / "settings.json", initial)
                with patch("satellite_alert.available_ports", return_value=[{"device": "COM3"}, {"device": "COM6"}]):
                    monitor = Monitor(root)
                saved = json.loads((root / "settings.json").read_text())
                self.assertEqual(saved["radio_port"], "COM6")
                self.assertEqual(saved["rotator_port"], "COM3")
                self.assertEqual(saved["rotator_baud"], 9600)
                self.assertEqual(saved["radio_baud"], 57600 if initial is None else 9600)
                self.assertEqual(saved["observer_height_m"], 0 if initial is None else 100)
                self.assertFalse(monitor.hardware.snapshot()["enabled"])
                monitor.close()
                with patch("satellite_alert.available_ports", return_value=[]):
                    restarted = Monitor(root)
                self.assertEqual(restarted.settings, monitor.settings)
                restarted.close()

    def test_frequency_changes_preserve_predictions_and_stop_hardware(self):
        calculator = OrbitCalculator()
        now = calculator.satellite(parse_tles(FIXTURE)[0][25544]).epoch.utc_datetime()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            atomic_json(root / "settings.json", {**DEFAULT_SETTINGS, "satellites": ["ISS"], "sound_enabled": False, "windows_popup": False})
            monitor = Monitor(root, clock=lambda: now, fetcher=lambda: FIXTURE)
            monitor._refresh_once(); monitor._rebuild(); monitor.tick()
            windows, generation = monitor.passes[:], monitor.generation
            profile = deepcopy(monitor.settings["satellite_profiles"])
            profile["ISS"]["downlink_mhz"] = 437.801
            with patch.object(monitor.hardware, "set_enabled") as switch:
                monitor.save_settings({"satellite_profiles": profile})
                switch.assert_called_once()
                self.assertFalse(switch.call_args.args[0])
            self.assertEqual(monitor.passes, windows)
            self.assertEqual(monitor.generation, generation)
            restarted = Monitor(root, clock=lambda: now, fetcher=lambda: FIXTURE)
            self.assertEqual(restarted.settings["satellite_profiles"]["ISS"]["downlink_mhz"], 437.801)
            self.assertFalse(restarted.hardware.snapshot()["enabled"])
            monitor.close(); restarted.close()


if __name__ == "__main__":
    unittest.main()
