"""GS-232B and IC-9700 serial tracking, isolated from the orbit/alert threads."""
from copy import deepcopy
import math
import re
import threading
import time

import serial
from serial.tools import list_ports

from profiles import MODES


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
    # GS-232B returns AZ=aaa EL=eee. Older compatible controllers use +aaaa+eeee.
    text = reply.decode("ascii", errors="replace")
    match = re.search(r"AZ\s*=\s*(\d{1,3})\s+EL\s*=\s*(\d{1,3})", text, re.I)
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
    def __init__(self, serial_factory=serial.Serial, ports_provider=available_ports, monotonic=time.monotonic):
        self.serial_factory, self.ports_provider, self.monotonic = serial_factory, ports_provider, monotonic
        self.lock = threading.RLock()
        self.io_lock = threading.RLock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.thread = None
        self.enabled = False  # Never resume hardware motion automatically on process restart.
        self.settings, self.target, self.offered_at = None, None, None
        self.rotator, self.radio = None, None
        self.last_direction, self.last_radio = None, None
        self.last_position_query = None
        self.moving = False
        self.state = dict(enabled=False, error=None, target=None, rotator_status="Off", radio_status="Off",
                          rotator_position=None, commanded_position=None, radio_rx_hz=None, radio_tx_hz=None,
                          direction_updates=0, frequency_updates=0)

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
            self.enabled = enabled
            self.settings = deepcopy(settings)
            self.state.update(enabled=enabled, error=None)
            if enabled:
                self.state.update(rotator_status="Connecting" if settings["rotator_enabled"] else "Not enabled",
                                  radio_status="Connecting" if settings["radio_enabled"] else "Not enabled")
            else:
                self.state.update(rotator_status="Off", radio_status="Off")
        self.wake.set()

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

    def _state(self, **values):
        with self.lock:
            self.state.update(values)

    def _check_enabled(self):
        with self.lock:
            if not self.enabled or self.stop.is_set():
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
            reply.extend(self.rotator.read_until(b"\r", 128))
            try:
                position = parse_rotator_position(bytes(reply))
            except OSError:
                continue
            self._state(rotator_position=position)
            self.last_position_query = self.monotonic()
            return
        raise OSError("No valid GS-232B C2 position reply. Check port, baud and controller.")

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
        # MAIN is downlink/RX and SUB is uplink/TX in IC-9700 satellite mode.
        # Command 25 cannot address SUB; use 07 D0/D1 followed by 05 instead.
        self._civ(0x07, b"\xd0")
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
                    frequency_updates=self.snapshot()["frequency_updates"] + 1)

    def _halt_rotator(self):
        if self.rotator and self.moving:
            self._write(self.rotator, b"S\r")
        self.moving = False
        self.last_direction = None

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
        if error:
            self._state(error=error)

    def step(self):
        with self.io_lock:
            with self.lock:
                enabled, target, settings, offered = self.enabled, deepcopy(self.target), deepcopy(self.settings), self.offered_at
            if not enabled:
                self._shutdown()
                return
            try:
                for device in ("rotator", "radio"):
                    if settings[device + "_enabled"] and getattr(self, device) is None:
                        self._check_enabled()
                        setattr(self, device, self._open(settings[device + "_port"], settings[device + "_baud"]))
                if self.rotator and (self.last_position_query is None or self.monotonic() - self.last_position_query >= 5):
                    self._query_position()
                if offered is None or self.monotonic() - offered > 6 or target is None:
                    self._halt_rotator()
                    self.last_radio = None
                    self._state(rotator_status="Waiting for a fresh in-range satellite" if self.rotator else "Not enabled",
                                radio_status="Waiting for a fresh in-range satellite" if self.radio else "Not enabled")
                    return
                if self.rotator:
                    self._point(target, settings)
                if self.radio:
                    self._tune(target, settings)
            except TrackingStopped:
                self._shutdown()
            except Exception as error:
                with self.lock:
                    self.enabled = False
                    self.state.update(enabled=False, error=str(error), rotator_status="Stopped after error", radio_status="Stopped after error")
                self._shutdown()

    def _run(self):
        while not self.stop.is_set():
            self.wake.wait(2)
            self.wake.clear()
            self.step()
        with self.io_lock:
            self._shutdown()

    def close(self):
        with self.lock:
            self.enabled = False
            self.state["enabled"] = False
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=3)
        with self.io_lock:
            self._shutdown()
