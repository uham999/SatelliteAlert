"""Run a local satellite dashboard with a continuous Python alert monitor.

Usage: python satellite_alert.py [--no-browser] [--port 8765]
The web interface is local; network access is only needed to refresh AMSAT TLEs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import ssl
import sys
import threading
import time
from urllib.request import Request, urlopen
import webbrowser

from orbits import (
    DEFAULT_SETTINGS, EARLY_ALERT_MINUTES, TLE_URL, OrbitCalculator, already_alerted, alert_due, alert_time,
    iso, parse_tles, resolve_name, utc_now, validate_settings, settings_for_startup,
)
from hardware import HardwareController, available_ports, select_target
from profiles import doppler, DEFAULT_PROFILES
from windows_audio import maximize_windows_volume

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("satellite_alert")
PAUSE_SETTINGS = {"automatic_pause_enabled", "automatic_pause_start", "automatic_pause_hours"}
HARDWARE_SETTINGS = {"satellite_profiles", "rotator_enabled", "rotator_port", "rotator_baud", "rotator_max_azimuth",
                     "radio_enabled", "radio_port", "radio_baud", "radio_address", "radio_step_hz", "tracking_minimum_mode"}


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def read_json(path: Path, default):
    if not path.exists():
        return deepcopy(default)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def download_elements() -> str:
    request = Request(TLE_URL, headers={"User-Agent": "NextSatellite/1.0 (personal satellite pass monitor)"})
    with urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
        body = response.read(1_000_001)
    if len(body) > 1_000_000:
        raise ValueError("The orbital elements download is unexpectedly large.")
    return body.decode("utf-8-sig")


class SoundPlayer:
    """Windows audio works even when the browser tab is in the background."""
    def __init__(self):
        self.windows = sys.platform == "win32"
        self.status = "Windows WAV audio · use Test alert to check" if self.windows else "Enable browser sound in the dashboard"
        self.busy = threading.Lock()

    def play(self, maximize_volume=False):
        if not self.windows or not self.busy.acquire(blocking=False):
            return
        volume_status = ''
        if maximize_volume:
            try:
                maximize_windows_volume()
                volume_status = ' · Windows volume 100%, unmuted'
            except Exception as error:
                volume_status = f' · Could not set Windows volume: {error}'
                LOG.exception('Unable to set Windows playback volume; playing at current volume')

        def run():
            try:
                import winsound
                winsound.PlaySound(str(ROOT / "static" / "alert-chime.wav"), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                self.status = "Windows WAV playback completed" + volume_status
            except Exception as error:
                self.status = f"Windows sound failed: {error}. Enable browser sound."
                LOG.exception("Unable to play Windows sound")
            finally:
                self.busy.release()
        threading.Thread(target=run, name="alarm-sound", daemon=True).start()


class Monitor:
    def __init__(self, root: Path = ROOT, clock=utc_now, fetcher=download_elements, local_timezone=None):
        self.root, self.clock, self.fetcher = root, clock, fetcher
        self.local_timezone = local_timezone  # None uses the computer's local time zone.
        self.data = root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        saved_settings = read_json(root / "settings.json", {})
        self.settings = settings_for_startup(saved_settings, available_ports())
        if not (root / "settings.json").exists() or any(self.settings[key] != saved_settings.get(key)
                for key in ("radio_port", "radio_baud", "rotator_port", "rotator_baud")):
            atomic_json(root / "settings.json", {**saved_settings, **self.settings})
        self.calculator = OrbitCalculator()
        self.sound = SoundPlayer()
        self.hardware = HardwareController()
        self.tracking_target = None
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.manual_refresh = False
        self.generation = 0
        self.records = {}
        self.models = []
        self.passes = []
        self.satellite_status = []
        self.forecast_at = None
        self.fetched_at = None
        self.retry_at = None
        self.fetch_error = None
        self.forecast_error = None
        self.updating = False
        self.warnings = []
        self.last_tick = None
        self.last_sound_at = 0.0
        self.sound_serial = 0
        self.threads = []
        self.paused = False
        self.manual_paused = False
        self.resume_override_window = None
        try:
            control = read_json(self.data / "pause.json", {})
            manual, override = control.get("manual_paused", False), control.get("resume_override_window")
            if not isinstance(manual, bool) or (override is not None and (not isinstance(override, str) or datetime.strptime(override, "%Y-%m-%d").date().isoformat() != override)):
                raise ValueError("Invalid saved pause control.")
            self.manual_paused, self.resume_override_window = manual, override
        except (ValueError, TypeError, AttributeError, OSError):
            LOG.exception("Pause control could not be read")
            self.warnings.append("Saved pause control could not be read; the daily schedule applies.")
        self._update_pause(self.clock())
        self.alerts = []
        self.ledger = []
        try:
            saved = read_json(self.data / "alerts.json", {"ledger": [], "alerts": []})
            ledger, alerts = saved["ledger"], saved["alerts"]
            if not isinstance(ledger, list) or not isinstance(alerts, list):
                raise ValueError("Invalid alert history lists.")
            for entry in ledger:
                if not isinstance(entry.get("norad"), int) or datetime.fromisoformat(entry["horizon_rise"]).tzinfo is None:
                    raise ValueError("Invalid pass history.")
            for entry in alerts:
                for key in ("id", "name", "message", "test", "acknowledged", "created_at", "expires_at"):
                    if key not in entry:
                        raise ValueError("Invalid alert history.")
                if any(datetime.fromisoformat(entry[key]).tzinfo is None for key in ("created_at", "expires_at")):
                    raise ValueError("Invalid alert timestamp.")
            self.ledger, self.alerts = ledger, alerts[-100:]
        except (ValueError, KeyError, TypeError, OSError):
            LOG.exception("Alert history could not be read")
            self.warnings.append("Alert history could not be read; a pass may alert again.")
        try:
            cache = read_json(self.data / "elements.json", None)
            if cache:
                self.records, messages = parse_tles(cache["text"])
                self.fetched_at = datetime.fromisoformat(cache["fetched_at"])
                if self.fetched_at.tzinfo is None:
                    raise ValueError("Cache timestamp must include its time zone.")
                self.warnings.extend(messages)
        except (ValueError, KeyError, TypeError, OSError):
            self.records, self.fetched_at = {}, None
            self.fetch_error = "The saved orbital data could not be read; downloading again."
            LOG.exception("Invalid cached elements")

    def start(self):
        # Check once per program start. A provider can return unchanged old
        # epochs; keep normal refresh/retry intervals instead of looping downloads.
        with self.lock:
            self.manual_refresh = self.manual_refresh or self._startup_refresh_required()
        self.hardware.start()
        for name, function in (("orbital-data", self._maintenance), ("pass-monitor", self._monitor)):
            thread = threading.Thread(target=function, name=name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _startup_refresh_required(self):
        now = self.clock()
        if not self.records or self.fetched_at is None or now - self.fetched_at >= timedelta(days=1):
            return True
        for name in self.settings["satellites"]:
            record = resolve_name(name, self.records)
            if record is None:
                return True
            try:
                epoch = self.calculator.satellite(record).epoch.utc_datetime()
                if now - epoch > timedelta(days=1):
                    return True
            except Exception:
                LOG.exception("Unable to check cached element age for %s", name)
                return True
        return False

    def close(self):
        self.stop.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=2)
        self.hardware.close()

    def next_refresh(self):
        due = self.fetched_at + timedelta(hours=self.settings["refresh_hours"]) if self.fetched_at else self.clock()
        return self.retry_at or due

    def refresh(self):
        with self.lock:
            self.manual_refresh = True
        self.wake.set()

    def save_settings(self, values):
        with self.lock:
            combined = {**self.settings, **values}
            if "latitude" in values or "longitude" in values:
                combined["location_note"] = ""
            settings = validate_settings(combined)
            recalculate = any(settings[key] != self.settings[key] for key in settings if key not in PAUSE_SETTINGS | HARDWARE_SETTINGS | {"earth_overlay", "windows_maximum_volume"})
            hardware_changed = any(settings[key] != self.settings[key] for key in HARDWARE_SETTINGS | {"latitude", "longitude", "observer_height_m", "minimum_elevation", "satellites"})
            pause_changed = any(settings[key] != self.settings[key] for key in PAUSE_SETTINGS)
            if hardware_changed:
                self.hardware.set_enabled(False, settings)
            atomic_json(self.root / "settings.json", settings)
            self.settings = settings
            if pause_changed:
                self.resume_override_window = None
                self._persist_pause()
            self._update_pause(self.clock())
            if recalculate:
                self.generation += 1
                # Stop using old-location or old-threshold windows immediately.
                self.models, self.passes, self.satellite_status = [], [], []
                self.forecast_at = None
                for alert in self.alerts:
                    alert["acknowledged"] = True
                self._persist_alerts()
            self._update_tracking()
        if recalculate:
            self.wake.set()

    def _persist_alerts(self):
        cutoff = self.clock() - timedelta(days=3)
        self.ledger = [entry for entry in self.ledger if datetime.fromisoformat(entry["horizon_rise"]) > cutoff]
        self.alerts = self.alerts[-100:]
        atomic_json(self.data / "alerts.json", {"ledger": self.ledger, "alerts": self.alerts})

    def _update_tracking(self):
        for row in self.satellite_status:
            if row.get("status") == "tracking" and "range_rate_km_s" in row:
                row["doppler"] = doppler(self.settings["satellite_profiles"][row["name"]], row["range_rate_km_s"])
        self.tracking_target = select_target(self.satellite_status, self.settings)
        self.hardware.offer(self.tracking_target, self.settings)

    def toggle_tracking(self, enabled):
        with self.lock:
            self._update_tracking()
            self.hardware.set_enabled(enabled, self.settings)

    def toggle_radio_test(self, enabled):
        with self.lock:
            self.hardware.set_radio_test(enabled, self.settings)

    def acknowledge(self, alert_id: str | None = None):
        with self.lock:
            for alert in self.alerts:
                if alert_id is None or alert["id"] == alert_id:
                    alert["acknowledged"] = True
            self._persist_alerts()

    def toggle_pause(self, paused: bool):
        if not isinstance(paused, bool):
            raise ValueError("Pause must be true or false.")
        with self.lock:
            self.manual_paused = paused
            self.resume_override_window = self._pause_window(self.clock()) if not paused else None
            self._persist_pause()
            self._update_pause(self.clock())

    def _pause_window(self, now):
        """Identify today's quiet period using local wall time, including DST.

        A period crossing midnight belongs to the date on which it started.
        Returning the start date lets Resume override only that daily period.
        """
        if not self.settings["automatic_pause_enabled"]:
            return None
        local = now.astimezone(self.local_timezone)
        hour, minute = map(int, self.settings["automatic_pause_start"].split(":"))
        start = hour * 3600 + minute * 60
        current = local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1_000_000
        if (current - start) % 86400 >= self.settings["automatic_pause_hours"] * 3600:
            return None
        date = local.date() - timedelta(days=int(current < start))
        return date.isoformat()

    def _update_pause(self, now):
        window = self._pause_window(now)
        if self.resume_override_window != window:
            self.resume_override_window = None
        self.automatic_pause_active = window is not None
        self.automatic_pause_overridden = window is not None and self.resume_override_window == window
        self.paused = self.manual_paused or (self.automatic_pause_active and not self.automatic_pause_overridden)
        self.pause_reason = "manual" if self.manual_paused else "automatic" if self.paused else None

    def _persist_pause(self):
        atomic_json(self.data / "pause.json", dict(manual_paused=self.manual_paused, resume_override_window=self.resume_override_window))

    def _refresh_once(self):
        with self.lock:
            self.updating = True
        try:
            text = self.fetcher()
            records, warnings = parse_tles(text)
            with self.lock:
                if not any(resolve_name(name, records) for name in self.settings["satellites"]):
                    raise ValueError("The download contains none of your selected satellites; keeping the previous data.")
                downloaded = self.clock()
                atomic_json(self.data / "elements.json", {"source": TLE_URL, "fetched_at": iso(downloaded), "text": text})
                self.records, self.fetched_at, self.warnings = records, downloaded, warnings
                self.retry_at, self.fetch_error, self.forecast_at = None, None, None
                self.generation += 1
            LOG.info("Downloaded %s valid satellites from AMSAT", len(records))
            for warning in warnings:
                LOG.warning(warning)
        except Exception as error:
            LOG.exception("Element refresh failed")
            with self.lock:
                self.fetch_error = f"Download failed: {error}. Retrying in 5 minutes; retained the last good data."
                self.retry_at = self.clock() + timedelta(minutes=5)
        finally:
            with self.lock:
                self.updating = False

    def _rebuild(self):
        with self.lock:
            settings, records, generation = dict(self.settings), dict(self.records), self.generation
        now = self.clock()
        observer = self.calculator.observer(settings)
        models, windows, status = [], [], []
        seen = set()
        for name in settings["satellites"]:
            record = resolve_name(name, records)
            row = dict(name=name, feed_name=record.name if record else None, norad=record.norad if record else None,
                       status="missing", detail="Not found in the AMSAT feed. Check this name in Settings.")
            if record:
                try:
                    if record.norad in seen:
                        row.update(status="duplicate", detail="Already monitored under another selected name.")
                    else:
                        seen.add(record.norad)
                        satellite = self.calculator.satellite(record)
                        age = (now - satellite.epoch.utc_datetime()).total_seconds() / 86400
                        row.update(epoch=iso(satellite.epoch.utc_datetime()), tle_age_days=age)
                        if abs(age) > settings["maximum_tle_age_days"]:
                            row.update(status="stale", detail="Orbital elements too old for alerts; waiting for fresh data.")
                        else:
                            predicted = self.calculator.passes(name, satellite, observer, settings, now)
                            windows.extend(predicted)
                            models.append((name, satellite, observer))
                            row.update(status="tracking", detail="Ready", next_pass=predicted[0].to_json(settings["alert_mode"]) if predicted else None)
                except Exception as error:
                    LOG.exception("Prediction failed for %s", name)
                    row.update(status="error", detail=f"Prediction failed: {error}")
            status.append(row)
        windows.sort(key=lambda pass_: alert_time(pass_, settings["alert_mode"]))
        with self.lock:
            if self.generation == generation:
                self.models, self.passes, self.satellite_status = models, windows, status
                self.forecast_at, self.forecast_error = now, None

    def _maintenance(self):
        while not self.stop.is_set():
            try:
                with self.lock:
                    now = self.clock()
                    download = self.manual_refresh or now >= self.next_refresh()
                    self.manual_refresh = False
                if download:
                    self._refresh_once()
                with self.lock:
                    rebuild = bool(self.records) and (self.forecast_at is None or abs((now - self.forecast_at).total_seconds()) > 1800)
                if rebuild:
                    self._rebuild()
            except Exception as error:
                LOG.exception("Forecast maintenance failed")
                with self.lock:
                    self.forecast_error = f"Forecast failed: {error}. Retrying shortly."
            self.wake.wait(2)
            self.wake.clear()

    def tick(self):
        with self.lock:
            now = self.clock()
            self.last_tick = now
            self._update_pause(now)
            rows = {row["name"]: row for row in self.satellite_status}
            ready = set()
            for name, satellite, observer in self.models:
                row = rows[name]
                age = (now - satellite.epoch.utc_datetime()).total_seconds() / 86400
                row["tle_age_days"] = age
                if abs(age) > self.settings["maximum_tle_age_days"]:
                    row.update(status="stale", detail="Orbital data too old; alerts suppressed.")
                    continue
                try:
                    row.update(self.calculator.position(satellite, observer, now, ground=True))
                    row.update(status="tracking", detail="Ready")
                    ready.add(name)
                except Exception as error:
                    row.update(status="error", detail=str(error))
            new_alerts = []
            self._update_tracking()
            if not self.paused:
                for pass_ in self.passes:
                    if pass_.name not in ready or not alert_due(pass_, self.settings["alert_mode"], now) or already_alerted(pass_, self.ledger):
                        continue
                    elevation = rows[pass_.name].get("elevation", -90)
                    # Guard an outdated forecast after a manoeuvre/new download.
                    if self.settings["alert_mode"] == "threshold" and elevation < self.settings["minimum_elevation"]:
                        continue
                    if self.settings["alert_mode"] == "horizon" and elevation < 0:
                        continue
                    alert = self._make_alert(pass_.name, False, now, pass_.threshold_set, elevation, pass_.peak_elevation, pass_.horizon_rise)
                    self.alerts.append(alert)
                    self.ledger.append(dict(norad=pass_.norad, horizon_rise=iso(pass_.horizon_rise)))
                    new_alerts.append(alert)
                if new_alerts:
                    self._persist_alerts()
            active = [alert for alert in self.alerts if not alert["acknowledged"] and datetime.fromisoformat(alert["expires_at"]) > now]
            if not self.paused and active and self.settings["sound_enabled"]:
                if new_alerts or time.monotonic() - self.last_sound_at >= self.settings["repeat_sound_seconds"]:
                    self._ring()
            popup = bool(new_alerts and self.settings["windows_popup"] and sys.platform == "win32")
        if popup:
            self._popup(new_alerts)

    def _make_alert(self, name, test, now, end, elevation, peak, horizon_rise=None):
        if test:
            message = "TEST ALERT — this is a demonstration, not a real pass."
        elif self.settings["alert_mode"] in EARLY_ALERT_MINUTES:
            rise_time = horizon_rise.strftime("%H:%M:%S UTC")
            message = f"{name}: qualifying pass, horizon rise at {rise_time}; predicted peak {peak:.1f}°."
        elif self.settings["alert_mode"] == "horizon":
            message = f"{name} is above the horizon on a qualifying pass."
        else:
            message = f"{name} is at or above {self.settings['minimum_elevation']}°."
        return dict(id=secrets.token_hex(8), name=name, test=test, created_at=iso(now), expires_at=iso(end),
                    elevation=round(elevation, 1), peak_elevation=round(peak, 1), acknowledged=False,
                    alert_mode=self.settings["alert_mode"], horizon_rise=iso(horizon_rise), message=message)

    def _ring(self):
        self.last_sound_at = time.monotonic()
        self.sound.play(maximize_volume=self.settings["windows_maximum_volume"])
        self.sound_serial += 1

    def test_alert(self):
        with self.lock:
            now = self.clock()
            alert = self._make_alert("TEST", True, now, now + timedelta(seconds=60), 35, 65)
            self.alerts.append(alert)
            self._persist_alerts()
            if self.settings["sound_enabled"]:
                self._ring()
            return alert

    def _popup(self, alerts):
        def show():
            try:
                import ctypes
                message = "\n\n".join(alert["message"] + f"\nPredicted peak: {alert['peak_elevation']}°" for alert in alerts)
                message += "\n\nAcknowledge this alert in Satellite Alert using Acknowledge & silence."
                ctypes.windll.user32.MessageBoxW(None, message, "Satellite pass alert", 0x40 | 0x10000 | 0x40000)
            except Exception:
                LOG.exception("Windows popup failed; dashboard alert remains visible")
        threading.Thread(target=show, name="pass-popup", daemon=True).start()

    def _monitor(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                LOG.exception("Monitor tick failed")
            self.stop.wait(2)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            self._update_pause(now)
            windows = [pass_.to_json(self.settings["alert_mode"]) for pass_ in self.passes if pass_.threshold_set > now]
            pending = [pass_.to_json(self.settings["alert_mode"]) for pass_ in self.passes
                       if pass_.threshold_set > now and not already_alerted(pass_, self.ledger)]
            pending.sort(key=lambda pass_: pass_["alert_at"])
            rows = deepcopy(self.satellite_status)
            for row in rows:
                row["next_pass"] = next((pass_ for pass_ in pending if pass_["name"] == row["name"]), None)
            rows.sort(key=lambda row: (row["next_pass"] is None, row["next_pass"]["alert_at"] if row["next_pass"] else ""))
            alerts = deepcopy(self.alerts)
            for alert in alerts:
                alert["active"] = not alert["acknowledged"] and datetime.fromisoformat(alert["expires_at"]) > now
            return dict(now=iso(now), settings=dict(self.settings), satellites=rows, passes=windows,
                        tracking_target=deepcopy(self.tracking_target), hardware=self.hardware.snapshot(),
                        alerts=alerts[::-1], fetched_at=iso(self.fetched_at), next_refresh_at=iso(self.next_refresh()),
                        forecast_at=iso(self.forecast_at), last_tick=iso(self.last_tick),
                        updating=self.updating, fetch_error=self.fetch_error, forecast_error=self.forecast_error,
                        warnings=list(self.warnings), paused=self.paused, sound_serial=self.sound_serial,
                        pause_reason=self.pause_reason, automatic_pause_active=self.automatic_pause_active,
                        automatic_pause_overridden=self.automatic_pause_overridden,
                        native_sound=self.sound.windows, sound_status=self.sound.status, source=TLE_URL)

    def filtered_elements(self):
        with self.lock:
            records = [resolve_name(name, self.records) for name in self.settings["satellites"]]
            unique = {record.norad: record for record in records if record}
            return "".join(f"{record.name}\n{record.line1}\n{record.line2}\n" for record in unique.values())


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, monitor):
        self.monitor = monitor
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, message, *args):
        LOG.debug(message, *args)

    def _send(self, status, content, content_type="application/json; charset=utf-8"):
        body = json.dumps(content, allow_nan=False).encode() if isinstance(content, (dict, list)) else content
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _local_request(self):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        valid_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        return host in valid_hosts and (origin is None or origin in {f"http://{name}" for name in valid_hosts})

    def do_GET(self):
        if not self._local_request():
            return self._send(403, {"error": "Use the local dashboard address."})
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            snapshot = self.server.monitor.snapshot()
            snapshot["token"] = self.server.token
            return self._send(200, snapshot)
        if path == "/api/elements":
            return self._send(200, self.server.monitor.filtered_elements(), "text/plain; charset=utf-8")
        if path == "/api/ports":
            return self._send(200, {"ports": available_ports(), "default_profiles": DEFAULT_PROFILES})
        if path == "/api/health":
            return self._send(200, {"ok": True, "app": "NextSatellite", "last_tick": iso(self.server.monitor.last_tick)})
        files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css"),
                 "/earth-overlay.png": ("earth-overlay.png", "image/png"),
                 "/d3-array.min.js": ("d3-array.min.js", "text/javascript"), "/d3-geo.min.js": ("d3-geo.min.js", "text/javascript"),
                 "/world-land.json": ("world-land.json", "application/json"),
                 "/alert-chime.wav": ("alert-chime.wav", "audio/wav")}
        if path in files:
            name, content_type = files[path]
            if content_type.startswith("text/"):
                content_type += "; charset=utf-8"
            return self._send(200, (self.server.monitor.root / "static" / name).read_bytes(), content_type)
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        return self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self._local_request() or self.headers.get("X-Satellite-Token") != self.server.token:
            return self._send(403, {"error": "Reload the local dashboard before saving."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 16384 or self.headers.get_content_type() != "application/json":
                raise ValueError("Send a small JSON request.")
            data = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Send a JSON object.")
            monitor = self.server.monitor
            if self.path == "/api/settings":
                monitor.save_settings(data)
            elif self.path == "/api/refresh":
                monitor.refresh()
            elif self.path == "/api/acknowledge":
                monitor.acknowledge(data.get("id"))
            elif self.path == "/api/pause":
                monitor.toggle_pause(data.get("paused"))
            elif self.path == "/api/tracking":
                monitor.toggle_tracking(data.get("enabled"))
            elif self.path == "/api/radio-test":
                monitor.toggle_radio_test(data.get("enabled"))
            elif self.path == "/api/test-alert":
                monitor.test_alert()
            elif self.path == "/api/stop":
                self._send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                return self._send(404, {"error": "Not found"})
            self._send(200, {"ok": True})
        except (ValueError, TypeError, OSError) as error:
            self._send(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 to 65535.")
    (ROOT / "data").mkdir(exist_ok=True)
    LOG.setLevel(logging.INFO)
    handler = RotatingFileHandler(ROOT / "data" / "satellite_alert.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    try:
        monitor = Monitor()
        server = LocalServer(("127.0.0.1", args.port), monitor)
    except (ValueError, OSError) as error:
        print(f"Could not start Satellite Alert: {error}")
        print("Check settings.json, or try a different --port if this port is already in use.")
        return 1
    monitor.start()
    address = f"http://127.0.0.1:{args.port}/"
    print(f"Satellite Alert is running at {address}", flush=True)
    print("Keep this process running for continuous alerts. Ctrl+C or Stop app in the dashboard exits.", flush=True)
    LOG.info("Started dashboard at %s", address)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        monitor.close()
        LOG.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
