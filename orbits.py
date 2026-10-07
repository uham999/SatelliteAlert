"""TLE validation, satellite identity, and geometric pass calculations."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import math
import re

import numpy as np
from skyfield.api import EarthSatellite, load, wgs84

UTC = timezone.utc
TLE_URL = "https://www.amsat.org/tle/current/nasabare.txt"
EARLY_ALERT_MINUTES = {"before_horizon_5": 5, "before_horizon": 10, "before_horizon_20": 20}
# NORAD catalogue numbers keep identity stable if the feed renames a satellite.
# AO-97 deliberately has no guessed replacement.
CATALOGUE = {
    "AO-07": 7530, "AO-7": 7530, "AO-91": 43017,
    "UO-11": 14781, "RS-44": 44909, "AO-73": 39444,
    "SO-50": 27607, "ISS": 25544, "FO-29": 24278,
    "JO-97": 43803, "AO-123": 61781,
}
DEFAULT_SETTINGS = {
    "latitude": 51.825,
    "longitude": -0.375,
    "observer_height_m": 0.0,
    "minimum_elevation": 35.0,
    "satellites": ["RS-44", "AO-73", "SO-50", "ISS", "FO-29", "JO-97", "AO-123"],
    "refresh_hours": 24.0,
    "forecast_hours": 48,
    "maximum_tle_age_days": 7.0,
    "alert_mode": "before_horizon_5",
    "earth_overlay": True,
    "sound_enabled": True,
    "repeat_sound_seconds": 30,
    "windows_popup": True,
    "automatic_pause_enabled": True,
    "automatic_pause_start": "00:00",
    "automatic_pause_hours": 7.0,
    "location_note": "",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def validate_settings(values: dict) -> dict:
    """Validate before saving anything; reject NaN, infinity and invalid names."""
    if not isinstance(values, dict):
        raise ValueError("Settings must be a JSON object.")
    result = dict(DEFAULT_SETTINGS)
    result.update({k: v for k, v in values.items() if k in result})
    bounds = {
        "latitude": (-90, 90), "longitude": (-180, 180),
        "observer_height_m": (-500, 10000), "minimum_elevation": (0, 89.9),
        "refresh_hours": (1, 168), "forecast_hours": (6, 168),
        "maximum_tle_age_days": (1, 14), "repeat_sound_seconds": (10, 600),
        "automatic_pause_hours": (0.25, 24),
    }
    for key, (lower, upper) in bounds.items():
        try:
            value = float(result[key])
        except (ValueError, TypeError):
            raise ValueError(f"{key.replace('_', ' ')} must be a number.") from None
        if isinstance(result[key], bool) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f"{key.replace('_', ' ')} must be between {lower} and {upper}.")
        result[key] = value
    for key in ("sound_enabled", "windows_popup", "earth_overlay", "automatic_pause_enabled"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be true or false.")
    if not isinstance(result["automatic_pause_start"], str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", result["automatic_pause_start"]):
        raise ValueError("Automatic pause start must be a time in HH:MM format, from 00:00 to 23:59.")
    if result["alert_mode"] not in ("threshold", "horizon", *EARLY_ALERT_MINUTES):
        raise ValueError("Choose threshold, horizon, or 5, 10 or 20 minutes before horizon rise.")
    names = result["satellites"]
    if not isinstance(names, list) or not 1 <= len(names) <= 30:
        raise ValueError("Choose between 1 and 30 satellites.")
    normalised = []
    for name in names:
        if not isinstance(name, str) or not name.strip() or not re.fullmatch(r"[A-Za-z0-9 ()_+./-]{1,60}", name):
            raise ValueError("Use satellite names from the feed, or a NORAD number.")
        name = name.strip().upper()
        if name not in normalised:
            normalised.append(name)
    result["satellites"] = normalised
    if not isinstance(result["location_note"], str) or len(result["location_note"]) > 500:
        raise ValueError("Location note is invalid.")
    return result


@dataclass(frozen=True)
class TLE:
    name: str
    norad: int
    line1: str
    line2: str


def _checksum(line: str) -> bool:
    return len(line) == 69 and line[68].isdigit() and (
        sum(int(c) if c.isdigit() else 1 if c == "-" else 0 for c in line[:68]) % 10
        == int(line[68])
    )


def parse_tles(text: str) -> tuple[dict[int, TLE], list[str]]:
    """Read three-line sets, refusing corrupt/truncated records and HTML errors."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    records: dict[int, TLE] = {}
    warnings = []
    index = 0
    while index < len(lines):
        name = lines[index].removeprefix("0 ")
        if index + 2 >= len(lines) or not lines[index + 1].startswith("1 ") or not lines[index + 2].startswith("2 "):
            warnings.append(f"Skipped malformed record near {name[:60]}.")
            index += 1
            continue
        line1, line2 = lines[index + 1:index + 3]
        index += 3
        try:
            norad = int(line1[2:7])
            if int(line2[2:7]) != norad or not all(_checksum(line) for line in (line1, line2)):
                raise ValueError("bad checksum or identity")
            year, day = int(line1[18:20]), float(line1[20:32])
            inclination, eccentricity = float(line2[8:16]), float("0." + line2[26:33])
            mean_motion = float(line2[52:63])
            if not (1 <= day < 367 and 0 <= inclination <= 180 and 0 <= eccentricity < 1 and 0 < mean_motion < 20):
                raise ValueError("invalid orbital field")
            if norad in records:
                raise ValueError("duplicate catalogue number")
            records[norad] = TLE(name, norad, line1, line2)
        except (ValueError, IndexError):
            warnings.append(f"Rejected invalid TLE for {name[:60]}.")
    if not records:
        raise ValueError("The download contains no valid TLE orbital elements.")
    return records, warnings


def resolve_name(name: str, records: dict[int, TLE]) -> TLE | None:
    normalised = name.strip().upper()
    if normalised in CATALOGUE:
        return records.get(CATALOGUE[normalised])
    if normalised.isdigit():
        return records.get(int(normalised))
    matches = [record for record in records.values() if record.name.upper() == normalised]
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class Pass:
    name: str
    norad: int
    horizon_rise: datetime
    threshold_rise: datetime
    peak_time: datetime
    peak_elevation: float
    threshold_set: datetime
    horizon_set: datetime
    rise_azimuth: float
    set_azimuth: float
    path: list[dict]

    def to_json(self, mode: str) -> dict:
        value = asdict(self)
        for key in ("horizon_rise", "threshold_rise", "peak_time", "threshold_set", "horizon_set"):
            value[key] = iso(value[key])
        value["alert_at"] = iso(alert_time(self, mode))
        return value


class OrbitCalculator:
    def __init__(self):
        # Built-in time tables: no ephemeris download is needed for this geometry.
        self.ts = load.timescale(builtin=True)

    def satellite(self, record: TLE) -> EarthSatellite:
        return EarthSatellite(record.line1, record.line2, record.name, self.ts)

    @staticmethod
    def observer(settings: dict):
        return wgs84.latlon(settings["latitude"], settings["longitude"], settings["observer_height_m"])

    def position(self, satellite, observer, now: datetime) -> dict:
        position = (satellite - observer).at(self.ts.from_datetime(now))
        if position.message:
            raise ValueError(str(position.message))
        altitude, azimuth, distance = position.altaz()
        values = [float(altitude.degrees), float(azimuth.degrees), float(distance.km)]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Orbit propagation produced an invalid position.")
        return dict(elevation=values[0], azimuth=values[1], range_km=values[2])

    def _segments(self, satellite, observer, start, end, altitude):
        times, codes = satellite.find_events(observer, self.ts.from_datetime(start), self.ts.from_datetime(end), altitude_degrees=altitude)
        # A window can begin during a pass; several culminations can occur.
        rise = start if self.position(satellite, observer, start)["elevation"] >= altitude else None
        peaks = []
        result = []
        for moment, code in zip(times, codes):
            moment_dt = moment.utc_datetime()
            if code == 0:
                rise, peaks = moment_dt, []
            elif code == 1 and rise is not None:
                peaks.append(moment_dt)
            elif code == 2 and rise is not None:
                result.append((rise, moment_dt, peaks))
                rise, peaks = None, []
        if rise is not None:
            result.append((rise, end, peaks))
        return result

    def passes(self, name: str, satellite, observer, settings: dict, now: datetime) -> list[Pass]:
        start = now - timedelta(hours=4)
        cutoff = now + timedelta(hours=settings["forecast_hours"])
        end = cutoff + timedelta(hours=4)
        threshold = settings["minimum_elevation"]
        windows = self._segments(satellite, observer, start, end, threshold)
        horizons = self._segments(satellite, observer, start, end, 0) if threshold > 0 else windows
        result = []
        for rise, setting, peaks in windows:
            if setting <= now:
                continue
            peak_candidates = peaks or [rise + (setting - rise) / 2]
            peak_time = max(peak_candidates, key=lambda moment: self.position(satellite, observer, moment)["elevation"])
            peak = self.position(satellite, observer, peak_time)["elevation"]
            if peak + 1e-6 < threshold:
                continue
            horizon = next((p for p in horizons if p[0] <= peak_time <= p[1]), (rise, setting, []))
            hrise, hset = horizon[:2]
            sample_dates = [hrise + (hset - hrise) * fraction for fraction in np.linspace(0, 1, 65)]
            sample_times = self.ts.from_datetimes(sample_dates)
            alt, az, _ = (satellite - observer).at(sample_times).altaz()
            if not (np.all(np.isfinite(alt.degrees)) and np.all(np.isfinite(az.degrees))):
                raise ValueError("Invalid pass path.")
            path = [dict(time=iso(d), elevation=float(a), azimuth=float(z)) for d, a, z in zip(sample_dates, alt.degrees, az.degrees)]
            pass_ = Pass(name, satellite.model.satnum, hrise, rise, peak_time, peak, setting, hset, float(az.degrees[0]), float(az.degrees[-1]), path)
            if alert_time(pass_, settings["alert_mode"]) <= cutoff:
                result.append(pass_)
        return result


def alert_time(pass_: Pass, mode: str) -> datetime:
    if mode in EARLY_ALERT_MINUTES:
        return pass_.horizon_rise - timedelta(minutes=EARLY_ALERT_MINUTES[mode])
    return pass_.horizon_rise if mode == "horizon" else pass_.threshold_rise


def alert_due(pass_: Pass, mode: str, now: datetime) -> bool:
    """Catch an ongoing pass on startup/resume; never alert after its useful window."""
    return alert_time(pass_, mode) <= now < pass_.threshold_set


def already_alerted(pass_: Pass, ledger: list[dict]) -> bool:
    # New element sets can shift a crossing by seconds: deduplicate by the pass,
    # not by an exact timestamp. Selected LEO passes are much farther apart.
    for item in ledger:
        if item.get("norad") == pass_.norad:
            try:
                if abs((datetime.fromisoformat(item["horizon_rise"]) - pass_.horizon_rise).total_seconds()) < 1200:
                    return True
            except (KeyError, ValueError, TypeError):
                continue
    return False
