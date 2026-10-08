"""GS-232B and IC-9700 serial tracking, isolated from the orbit/alert threads."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import re
import threading
import time

import serial
from serial.tools import list_ports

from profiles import MODES

RADIO_TEST_FREQUENCIES = (145_000_000, 145_555_000)
RADIO_TEST_INTERVAL = 2.0


def available_ports():
    return [dict(device=p.device, description=p.description) for p in sorted(list_ports.comports())]


def select_target(rows, settings):
    minimum = settings["minimum_elevation"] if settings["tracking_minimum_mode"] == "threshold" else 0
    candidates = [row for row in rows if row.get("status") == "tracking"
                  and all(isinstance(row.get(key), (int, float)) and math.isfinite(row[key]) for key in ("azimuth", "elevation", "range_rate_km_s"))
                  and row["elevation"] >= minimum]
    return deepcopy(max(candidates, key=lambda row: row["elevation"])) if candidates else None


def angle_change(a, b):
    return abs((a - b + 180) % 360 - 180)


def frequency_bcd(hz):
    if isinstance(hz, bool) or not isinstance(hz, int) or not 0 <= hz < 10_000_000_000:
        raise ValueError("CI-V frequency must be an integer in Hz with at most ten digits.")
    digits = f"{hz:010d}"
    return bytes(int(digits[i:i + 2], 16) for i in range(8, -1, -2))


def decode_frequency(data):
    if len(data) != 5 or any((b >> 4) > 9 or (b & 15) > 9 for b in data):
        raise ValueError("Invalid CI-V frequency reply.")
    return int("".join(f"{b:02X}" for b in data[::-1]))


def civ_frame(address, command, data=b""):
    return b"\xfe\xfe" + bytes((address, 0xE0, command)) + data + b"\xfd"


def parse_rotator_position(reply):
    # Accept AZ=aaaEL=eee with or without a gap between the two angles.
    # Older compatible controllers use +aaaa+eeee.
    text = reply.decode("ascii", errors="replace")
    match = re.search(r"AZ\s*=\s*(\d{1,3})\s*EL\s*=\s*(\d{1,3})", text, re.I)
    if not match:
        match = re.search(r"\+(\d{4})\+(\d{4})", text)
    if not match:
        raise OSError("Rotator did not return an azimuth/elevation reply to C2. Check port, baud and GS-232B mode.")
    az, el = map(int, match.groups())
    if not 0 <= az <= 450 or not 0 <= el <= 180:
        raise OSError("Rotator returned an out-of-range position.")
    return dict(azimuth=az, elevation=el)


def radio_band(hz):
    for index, (lower, upper) in enumerate(((144_000_000, 148_000_000), (430_000_000, 450_000_000), (1_240_000_000, 1_300_000_000))):
        if lower <= hz <= upper:
            return index
    raise ValueError("Frequency is outside the IC-9700's supported VHF/UHF/1.2 GHz bands.")


class TrackingStopped(Exception):
    pass


class HardwareController:
    def __init__(self, serial_factory=serial.Serial, ports_provider=available_ports, monotonic=time.monotonic, utc_clock=None):
        self.serial_factory, self.ports_provider, self.monotonic = serial_factory, ports_provider, monotonic
        self.utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.io_lock = threading.RLock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.thread = None
        self.enabled = False  # Never resume hardware motion automatically on process restart.
        self.radio_test_enabled = False
        self.generation, self.io_generation, self.connection_generation = 0, None, None
        self.radio_test_ready, self.radio_test_index, self.next_radio_test_at = False, 0, None
        self.settings, self.target, self.offered_at = None, None, None
        self.rotator, self.radio = None, None
        self.last_direction, self.last_radio = None, None
        self.last_position_query = None
        self.rotator_fault = None
        self.moving = False
        self.state = dict(enabled=False, radio_test_enabled=False, radio_test_frequency_hz=None, radio_test_updates=0,
                          error=None, rotator_error=None, target=None, rotator_status="Off", radio_status="Off",
                          rotator_position=None, commanded_position=None, radio_rx_hz=None, radio_tx_hz=None,
                          radio_verified_at=None, radio_verified_target=None,
                          direction_updates=0, frequency_updates=0, rotator_test=None)

    def start(self):
        self.thread = threading.Thread(target=self._run, name="serial-tracking", daemon=True)
        self.thread.start()

    def offer(self, target, settings):
        # One latest observation, never a queue of stale pointing commands.
        with self.lock:
            self.target, self.settings, self.offered_at = deepcopy(target), deepcopy(settings), self.monotonic()
            self.state["target"] = target["name"] if target else None
        self.wake.set()

    def set_enabled(self, enabled, settings):
        if not isinstance(enabled, bool):
            raise ValueError("Live tracking must be true or false.")
        if enabled:
            if not settings["rotator_enabled"] and not settings["radio_enabled"]:
                raise ValueError("Enable a rotator or radio in Settings first.")
            available = {p["device"] for p in self.ports_provider()}
            for device in ("rotator", "radio"):
                if settings[device + "_enabled"] and settings[device + "_port"] not in available:
                    raise ValueError(f"Select an available {device} serial port in Settings, then save.")
        with self.lock:
            if enabled and self.radio_test_enabled:
                raise ValueError("Stop the radio connection test before starting live tracking.")
            self.enabled = enabled
            self.radio_test_enabled = False
            self.generation += 1
            self.settings = deepcopy(settings)
            self.state.update(enabled=enabled, radio_test_enabled=False, radio_test_frequency_hz=None, error=None, rotator_error=None)
            if enabled:
                self.state.update(rotator_status="Connecting" if settings["rotator_enabled"] else "Not enabled",
                                  radio_status="Connecting" if settings["radio_enabled"] else "Not enabled")
            else:
                self.state.update(rotator_status="Off", radio_status="Off")
        self.wake.set()

    def set_radio_test(self, enabled, settings):
        if not isinstance(enabled, bool):
            raise ValueError("Radio connection test must be true or false.")
        if enabled and settings["radio_port"] not in {p["device"] for p in self.ports_provider()}:
            raise ValueError("Select an available radio serial port in Settings, then save.")
        with self.lock:
            if enabled == self.radio_test_enabled:
                return
            self.enabled = False
            self.radio_test_enabled = enabled
            self.generation += 1
            self.settings = deepcopy(settings)
            self.state.update(enabled=False, radio_test_enabled=enabled, radio_test_frequency_hz=None,
                              error=None, rotator_error=None, rotator_status="Off", radio_status="Connecting for test" if enabled else "Off")
            if enabled:
                self.state.update(radio_rx_hz=None, radio_tx_hz=None, radio_test_updates=0)
        self.wake.set()

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

    def test_rotator(self, settings):
        # Serialize with tracking so a C2 reply cannot be consumed by another query.
        # A diagnostic query does not change the live tracking or radio-test session.
        with self.io_lock:
            port, baud = settings["rotator_port"], settings["rotator_baud"]
            result = dict(tested_at=self.utc_clock().isoformat(), port=port or None, baud=baud,
                          command_attempted=False, command_sent=False, response="", position=None, status="warning", message="")
            connection, temporary, reply = None, False, bytearray()
            try:
                if not port.strip():
                    result["message"] = "No rotator port is configured. Select a rotator serial port in Settings, then save."
                    return result
                if self.stop.is_set():
                    raise OSError("The application is stopping; the rotator test was cancelled.")
                with self.lock:
                    radio_port = self.settings["radio_port"] if self.settings and (self.radio_test_enabled or
                        (self.enabled and self.settings["radio_enabled"])) else None
                if any(p and p.casefold() == port.casefold() for p in (radio_port, self.radio.port if self.radio else None)):
                    result["message"] = "The configured rotator port is in use by radio control. Select a different rotator port in Settings."
                    return result
                if self.rotator and self.rotator.port.casefold() == port.casefold():
                    if self.rotator.baudrate != baud:
                        result["message"] = "The rotator connection is still using the previous baud rate. Try again after live tracking stops."
                        return result
                    connection = self.rotator
                else:
                    if port.casefold() not in {p["device"].casefold() for p in self.ports_provider()}:
                        result["message"] = f"Configured rotator port {port} is unavailable. Connect the interface or choose an available port in Settings."
                        return result
                    connection = self._open(port, baud)
                    temporary = True
                connection.reset_input_buffer()
                result["tested_at"] = self.utc_clock().isoformat()
                result["command_attempted"] = True
                self._write(connection, b"C2\r")
                result["command_sent"] = True
                deadline, position = self.monotonic() + 0.8, None
                while self.monotonic() < deadline and len(reply) < 256 and not self.stop.is_set():
                    reply.extend(connection.read_until(b"\n", 256 - len(reply)))
                    if reply.endswith((b"\r", b"\n")):
                        try:
                            position = parse_rotator_position(bytes(reply))
                            break
                        except OSError:
                            pass
                if self.stop.is_set():
                    raise OSError("The application is stopping; the rotator test was cancelled.")
                if not reply:
                    raise OSError("No response to C2 within 0.8 seconds. Check the rotator port, baud rate and controller.")
                if position is None:
                    if len(reply) >= 256:
                        raise OSError("C2 response reached the 256-byte limit. Check the rotator port and baud rate.")
                    parse_rotator_position(bytes(reply))  # Keep the parser's specific diagnostic.
                    raise OSError("Incomplete C2 response (missing line ending). Check the rotator port and baud rate.")
                result.update(status="success", position=position,
                              message=f"Reported azimuth {position['azimuth']}° · elevation {position['elevation']}°.")
                self._state(rotator_position=position)
                if connection is self.rotator:
                    self.last_position_query = self.monotonic()
                return result
            except Exception as error:
                result.update(status="error", message=str(error))
                return result
            finally:
                result["response"] = bytes(reply).decode("ascii", errors="backslashreplace")
                if temporary:
                    try:
                        connection.close()
                    except Exception as error:
                        result.update(status="error", message=f"{result['message']} Could not close the test port: {error}")
                self._state(rotator_test=deepcopy(result))

    def _state(self, **values):
        with self.lock:
            self.state.update(values)

    def _check_enabled(self):
        with self.lock:
            if not (self.enabled or self.radio_test_enabled) or self.stop.is_set() or self.io_generation != self.generation:
                raise TrackingStopped()

    def _open(self, port, baud):
        # Set modem-control lines before opening: CI-V is data, never USB SEND/PTT.
        connection = self.serial_factory(port=None, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                                         timeout=0.1, write_timeout=0.5, xonxoff=False, rtscts=False, dsrdtr=False)
        try:
            connection.dtr = False
            connection.rts = False
            connection.port = port
            connection.open()
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _write(connection, frame):
        if connection.write(frame) != len(frame):
            raise OSError("Serial write was incomplete.")

    def _civ(self, command, data=b"", reply_command=None):
        self._check_enabled()
        address = int(self.settings["radio_address"], 16)
        self.radio.reset_input_buffer()
        self._write(self.radio, civ_frame(address, command, data))
        deadline = self.monotonic() + 0.8
        buffer = bytearray()
        # Echoes, transceive broadcasts, split frames and unrelated addresses are ignored.
        while self.monotonic() < deadline:
            self._check_enabled()
            chunk = self.radio.read(1)
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) > 1024:
                raise OSError("Excessive CI-V noise. Check interface and baud rate.")
            if chunk != b"\xfd":
                continue
            start = buffer.rfind(b"\xfe\xfe")
            frame = bytes(buffer[start:]) if start >= 0 else b""
            buffer.clear()
            if len(frame) < 6 or frame[2:4] != bytes((0xE0, address)):
                continue
            if frame[4] == 0xFA:
                raise OSError(f"IC-9700 rejected command {command:02X} (FA). Check satellite/VFO mode and supported frequency.")
            if reply_command is None and frame[4:6] == b"\xfb\xfd":
                return b""
            if reply_command is not None and frame[4] == reply_command:
                return frame[5:-1]
        raise OSError("IC-9700 did not acknowledge/respond. Check CI-V address, port and baud rate.")

    def _point(self, target, settings):
        az, el = target["azimuth"] % 360, target["elevation"]
        if not 0 <= el <= 90:
            raise ValueError("Refusing an elevation outside the visible sky.")
        reported = self.snapshot()["rotator_position"]
        reference = self.last_direction[0] if self.last_direction else reported["azimuth"] if reported else az
        possibilities = [az] + ([az + 360] if az + 360 <= settings["rotator_max_azimuth"] else [])
        physical_az = min(possibilities, key=lambda value: abs(value - reference))
        changed = self.last_direction is None or angle_change(az, self.last_direction[0]) >= 1 - 1e-9 or abs(el - self.last_direction[1]) >= 1 - 1e-9
        if changed:
            self._check_enabled()
            integer_az, integer_el = int(math.floor(physical_az + 0.5)), int(math.floor(el + 0.5))
            self.rotator.reset_input_buffer()
            self._write(self.rotator, f"W{integer_az:03d} {integer_el:03d}\r".encode("ascii"))
            reply = self.rotator.read_until(b"\r", 128)
            if b"?>" in reply:
                raise OSError("Rotator rejected the pointing command.")
            self.last_direction = (physical_az, el)
            self.moving = True
            self._state(commanded_position=dict(azimuth=integer_az, elevation=integer_el),
                        direction_updates=self.snapshot()["direction_updates"] + 1)
        self._state(rotator_status="Tracking")

    def _query_position(self):
        self._check_enabled()
        self.rotator.reset_input_buffer()
        self._write(self.rotator, b"C2\r")
        deadline = self.monotonic() + 0.8
        reply = bytearray()
        while self.monotonic() < deadline and len(reply) < 256:
            self._check_enabled()
            reply.extend(self.rotator.read_until(b"\r", 128))
            try:
                position = parse_rotator_position(bytes(reply))
            except OSError:
                continue
            self._state(rotator_position=position)
            self.last_position_query = self.monotonic()
            return
        raise OSError("No valid GS-232B C2 position reply. Check port, baud and controller.")

    def _prepare_main_band(self, downlink_hz):
        # IC-9700 MAIN and SUB cannot occupy the same band. Frequency command 05
        # cannot move MAIN onto a band already assigned to SUB; exchange them first.
        self._civ(0x07, b"\xd0")
        main_hz = decode_frequency(self._civ(0x03, reply_command=0x03))
        wanted = radio_band(downlink_hz)
        if radio_band(main_hz) != wanted:
            self._civ(0x07, b"\xd1")
            sub_hz = decode_frequency(self._civ(0x03, reply_command=0x03))
            self._civ(0x07, b"\xd0")
            if radio_band(sub_hz) == wanted:
                self._civ(0x07, b"\xb0")

    def _tune(self, target, settings):
        freq, profile_ = target["doppler"], settings["satellite_profiles"][target["name"]]
        rx, tx = freq["rx_hz"], freq["tx_hz"]
        if rx is None:
            self._state(radio_status="Set a downlink frequency for this satellite")
            return
        if tx is not None and radio_band(rx) == radio_band(tx):
            raise ValueError("IC-9700 satellite mode needs uplink and downlink in different bands. Leave uplink empty for receive-only tracking.")
        radio_band(rx)
        if tx is not None:
            radio_band(tx)
        modes = (profile_["downlink_mode"], profile_["uplink_mode"])
        identity = (target["name"], modes, tx is not None)
        first = self.last_radio is None or self.last_radio[0] != identity
        previous_rx, previous_tx = (None, None) if first else self.last_radio[1:]
        change_rx = first or abs(rx - previous_rx) >= settings["radio_step_hz"]
        change_tx = tx is not None and (first or abs(tx - previous_tx) >= settings["radio_step_hz"])
        if not change_rx and not change_tx:
            return
        if first:
            self._civ(0x16, bytes((0x5A, int(tx is not None))))
            self._civ(0x07)  # VFO mode; satellite memories must not be overwritten.
            self._prepare_main_band(rx)
        else:
            self._civ(0x07, b"\xd0")
        # MAIN is downlink/RX and SUB is uplink/TX in IC-9700 satellite mode.
        # Command 25 cannot address SUB; use 07 D0/D1 followed by 05 instead.
        if first:
            self._civ(0x06, bytes((MODES[modes[0]], 1)))
        if change_rx:
            self._civ(0x05, frequency_bcd(rx))
        if tx is not None:
            self._civ(0x07, b"\xd1")
            if first:
                self._civ(0x06, bytes((MODES[modes[1]], 1)))
            if change_tx:
                self._civ(0x05, frequency_bcd(tx))
            verified_tx = decode_frequency(self._civ(0x03, reply_command=0x03))
            if abs(verified_tx - (tx if change_tx else previous_tx)) > 1:
                raise OSError("Uplink frequency readback differs. Check MAIN/SUB CI-V operation, turn AFC off and avoid manual tuning during live tracking.")
        self._civ(0x07, b"\xd0")
        verified_rx = decode_frequency(self._civ(0x03, reply_command=0x03))
        if abs(verified_rx - (rx if change_rx else previous_rx)) > 1:
            raise OSError("Downlink frequency readback differs. Check MAIN/SUB CI-V operation, turn AFC off and avoid manual tuning during live tracking.")
        self.last_radio = (identity, rx if change_rx else previous_rx, tx if change_tx else previous_tx)
        self._state(radio_status="Tracking · frequencies verified", radio_rx_hz=verified_rx,
                    radio_tx_hz=verified_tx if tx is not None else None,
                    radio_verified_at=self.utc_clock().isoformat(), radio_verified_target=target["name"],
                    frequency_updates=self.snapshot()["frequency_updates"] + 1)

    def _test_radio(self, settings):
        if self.radio is None:
            self._check_enabled()
            self.radio = self._open(settings["radio_port"], settings["radio_baud"])
        if not self.radio_test_ready:
            self._civ(0x16, b"\x5a\x00")  # Test the MAIN VFO independently of satellite mode.
            self._civ(0x07)
            self._prepare_main_band(RADIO_TEST_FREQUENCIES[0])
            self.radio_test_ready = True
        sent_at = self.monotonic()
        if self.next_radio_test_at is not None and sent_at < self.next_radio_test_at:
            return
        hz = RADIO_TEST_FREQUENCIES[self.radio_test_index]
        self._civ(0x05, frequency_bcd(hz))
        verified = decode_frequency(self._civ(0x03, reply_command=0x03))
        if abs(verified - hz) > 1:
            raise OSError("Radio connection test frequency readback differs. Turn AFC off and avoid manual tuning during the test.")
        self._check_enabled()
        self.radio_test_index = (self.radio_test_index + 1) % len(RADIO_TEST_FREQUENCIES)
        # Orbit updates can wake the worker early; only send a tune when it is due.
        # Schedule from the write, so acknowledgement time does not extend each interval.
        self.next_radio_test_at = sent_at + RADIO_TEST_INTERVAL
        self._state(radio_status="Connection test · frequency verified", radio_rx_hz=verified, radio_tx_hz=None,
                    radio_verified_at=self.utc_clock().isoformat(), radio_verified_target="Radio connection test",
                    radio_test_frequency_hz=verified, radio_test_updates=self.snapshot()["radio_test_updates"] + 1)

    def _halt_rotator(self):
        if self.rotator and self.moving:
            self._write(self.rotator, b"S\r")
        self.moving = False
        self.last_direction = None

    def _stop_rotator_after_error(self, error):
        message = str(error)
        try:
            self._halt_rotator()
        except Exception as failure:
            message += f" Could not send rotator stop: {failure}. Stop it at the controller."
        finally:
            if self.rotator:
                try:
                    self.rotator.close()
                except Exception:
                    pass
            self.rotator = None
            self.moving, self.last_direction, self.last_position_query = False, None, None
        self.rotator_fault = message
        self._state(rotator_error=message, rotator_status="Stopped after error")

    def _shutdown(self):
        error = None
        try:
            self._halt_rotator()
        except Exception as failure:
            error = f"Could not send rotator stop: {failure}. Stop it at the controller."
        for device in (self.rotator, self.radio):
            if device:
                try:
                    device.close()
                except Exception:
                    pass
        self.rotator, self.radio, self.last_radio, self.last_position_query = None, None, None, None
        self.moving, self.last_direction = False, None
        self.connection_generation = None
        self.rotator_fault = None
        self.radio_test_ready, self.radio_test_index, self.next_radio_test_at = False, 0, None
        if error:
            self._state(error=error)

    def step(self):
        with self.io_lock:
            with self.lock:
                enabled, target, settings, offered = self.enabled, deepcopy(self.target), deepcopy(self.settings), self.offered_at
                radio_test_enabled, generation = self.radio_test_enabled, self.generation
            if not enabled and not radio_test_enabled:
                self._shutdown()
                return
            try:
                self.io_generation = generation
                if self.connection_generation != generation:
                    self._shutdown()
                    self.connection_generation = generation
                if radio_test_enabled:
                    self._test_radio(settings)
                    return
                if settings["rotator_enabled"] and not self.rotator_fault:
                    try:
                        if self.rotator is None:
                            self._check_enabled()
                            self.rotator = self._open(settings["rotator_port"], settings["rotator_baud"])
                        if self.last_position_query is None or self.monotonic() - self.last_position_query >= 5:
                            self._query_position()
                        if offered is not None and self.monotonic() - offered <= 6 and target is not None:
                            self._point(target, settings)
                        else:
                            self._halt_rotator()
                            self._state(rotator_status="Waiting for a fresh in-range satellite")
                    except TrackingStopped:
                        raise
                    except Exception as error:
                        self._check_enabled()
                        self._stop_rotator_after_error(error)
                        if not settings["radio_enabled"]:
                            raise
                if settings["radio_enabled"]:
                    if self.radio is None:
                        self._check_enabled()
                        self.radio = self._open(settings["radio_port"], settings["radio_baud"])
                if offered is None or self.monotonic() - offered > 6 or target is None:
                    self.last_radio = None
                    self._state(radio_status="Waiting for a fresh in-range satellite" if self.radio else "Not enabled")
                    return
                if self.radio:
                    self._tune(target, settings)
            except TrackingStopped:
                self._shutdown()
            except Exception as error:
                with self.lock:
                    if self.generation == generation:
                        self.enabled = self.radio_test_enabled = False
                        self.generation += 1
                        self.state.update(enabled=False, radio_test_enabled=False, radio_test_frequency_hz=None,
                                          error=str(error), rotator_status="Stopped after error", radio_status="Stopped after error")
                self._shutdown()

    def _run(self):
        while not self.stop.is_set():
            wait_seconds = min(2, max(0, self.next_radio_test_at - self.monotonic())) if self.radio_test_enabled and self.next_radio_test_at is not None else 2
            self.wake.wait(wait_seconds)
            self.wake.clear()
            self.step()
        with self.io_lock:
            self._shutdown()

    def close(self):
        with self.lock:
            self.enabled = self.radio_test_enabled = False
            self.generation += 1
            self.state.update(enabled=False, radio_test_enabled=False, radio_test_frequency_hz=None)
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=3)
        with self.io_lock:
            self._shutdown()
