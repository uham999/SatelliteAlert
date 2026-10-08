"""Editable radio reference frequencies; defaults verified against AMSAT 2026-10-08."""
from copy import deepcopy
import math

MODES = {"LSB": 0x00, "USB": 0x01, "CW": 0x03, "FM": 0x05, "CW-R": 0x07}
LINEAR_SOURCE = "https://www.amsat.org/live-linear-satellites/"
FM_SOURCE = "https://www.amsat.org/live-fm-satellites/"


def profile(rx=None, tx=None, rx_mode="USB", tx_mode="LSB", notes="", source=""):
    return dict(downlink_mhz=rx, uplink_mhz=tx, downlink_mode=rx_mode,
                uplink_mode=tx_mode, notes=notes, source=source)


DEFAULT_PROFILES = {
    "RS-44": profile(435.640, 145.965, notes="Inverting linear transponder centre. Edit both frequencies to choose your channel.", source=LINEAR_SOURCE),
    "AO-73": profile(145.960, 435.140, notes="Inverting linear transponder centre; telemetry is 145.935 MHz. Operation depends on satellite schedule.", source=LINEAR_SOURCE),
    "SO-50": profile(436.795, 145.850, "FM", "FM", "Set 67 Hz uplink CTCSS on the radio. Satellite activation uses a 74.4 Hz tone; this app does not transmit.", FM_SOURCE),
    "ISS": profile(437.800, 145.990, "FM", "FM", "FM repeater pair, 67 Hz uplink CTCSS on the radio. Other ISS services use other frequencies.", FM_SOURCE),
    "FO-29": profile(435.850, 145.950, notes="Inverting linear transponder centre. Availability depends on spacecraft power and schedule.", source=LINEAR_SOURCE),
    "JO-97": profile(145.865, 435.110, notes="Inverting linear transponder centre; edit both frequencies for your channel.", source=LINEAR_SOURCE),
    "AO-123": profile(435.400, 145.850, "FM", "FM", "FM repeater pair from AMSAT's live FM table. Set 67 Hz uplink CTCSS on the radio; other payloads use other frequencies.", FM_SOURCE),
}


def default_profile(name):
    return deepcopy(DEFAULT_PROFILES.get(name, profile()))


def validate_profiles(values, names):
    if not isinstance(values, dict) or len(values) > 60:
        raise ValueError("Satellite frequencies must be a small object keyed by satellite name.")
    result = {}
    for name in names:
        value = values.get(name, default_profile(name))
        if not isinstance(value, dict):
            raise ValueError(f"{name}: frequency profile must be an object.")
        item = {**default_profile(name), **{key: v for key, v in value.items() if key in default_profile(name)}}
        for key in ("downlink_mhz", "uplink_mhz"):
            frequency = item[key]
            if frequency is not None:
                if isinstance(frequency, bool):
                    raise ValueError(f"{name}: {key} must be a frequency in MHz or empty.")
                try:
                    frequency = float(frequency)
                except (ValueError, TypeError):
                    raise ValueError(f"{name}: {key} must be a frequency in MHz or empty.") from None
                if not math.isfinite(frequency) or not 1 <= frequency <= 1400:
                    raise ValueError(f"{name}: frequencies must be between 1 and 1400 MHz.")
                item[key] = frequency
        for key in ("downlink_mode", "uplink_mode"):
            if not isinstance(item[key], str) or item[key] not in MODES:
                raise ValueError(f"{name}: choose FM, USB, LSB, CW or CW-R.")
        for key in ("notes", "source"):
            if not isinstance(item[key], str) or len(item[key]) > 600:
                raise ValueError(f"{name}: {key} must be short text.")
        result[name] = item
    return result


def doppler(profile_, range_rate_km_s):
    """First-order one-way Doppler; positive range rate means receding.

    RX follows the shifted downlink. TX applies the opposite correction so the
    satellite receives the desired uplink frequency. Observer rotation is already
    included in Skyfield's topocentric range rate.
    """
    if not math.isfinite(range_rate_km_s) or abs(range_rate_km_s) > 30:
        raise ValueError("Invalid satellite range rate.")
    fraction = range_rate_km_s / 299792.458
    result = dict(range_rate_km_s=range_rate_km_s)
    for prefix, key, factor in (("rx", "downlink_mhz", 1 - fraction), ("tx", "uplink_mhz", 1 + fraction)):
        base = profile_.get(key)
        hz = round(base * 1_000_000) if base is not None else None
        result[prefix + "_base_hz"] = hz
        result[prefix + "_hz"] = round(hz * factor) if hz is not None else None
        result[prefix + "_shift_hz"] = result[prefix + "_hz"] - hz if hz is not None else None
    return result
