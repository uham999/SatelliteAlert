"""Look up editable radio profiles in AMSAT's live FM and linear tables."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from html.parser import HTMLParser
import re
import ssl
import threading
import time
from urllib.request import Request, urlopen

import certifi

from profiles import DEFAULT_PROFILES, FM_SOURCE, LINEAR_SOURCE, profile


class _TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).replace("\xa0", " ").strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row, self.cell = None, None


def _key(name):
    # Exact aliases only: AO-9 must never match AO-91.
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _centre(text):
    frequencies = [float(value) * (1000 if unit.upper() == "GHZ" else 1)
                   for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(MHz|GHz)\b", text, re.I)]
    if not 1 <= len(frequencies) <= 2 or any(not 1 <= value <= 1400 for value in frequencies):
        return None
    return round(sum(frequencies) / len(frequencies), 6)


def parse_profiles(html, source):
    parser = _TableRows()
    parser.feed(html)
    result = []
    for row in parser.rows:
        if source == FM_SOURCE and len(row) == 4:
            label, tx, rx, comment = row
            downlink, uplink = _centre(rx), _centre(tx)
            rx_mode = tx_mode = "FM"
            extra = "FM repeater. " + " ".join(tx.splitlines()[1:])
        elif source == LINEAR_SOURCE and len(row) == 3:
            label, frequencies, comment = row
            directions = {}
            for direction, mode, values in re.findall(r"(Uplink|Downlink)\s+(USB|LSB|FM|CW-R|CW)\s*:\s*([^\n]+)", frequencies, re.I):
                directions[direction.lower()] = (mode.upper(), _centre(values))
            if set(directions) != {"uplink", "downlink"}:
                continue
            rx_mode, downlink = directions["downlink"]
            tx_mode, uplink = directions["uplink"]
            extra = "Linear transponder passband centre; edit both frequencies to choose your channel."
        else:
            continue
        if downlink is None or uplink is None:
            continue
        title = label.splitlines()[0].strip()
        canonical = title.split("(")[0].strip()
        aliases = [canonical]
        aliases.append(re.sub(r"\s+Mode\s+[A-Z]$", "", canonical, flags=re.I))
        for alternate in re.findall(r"\(([^)]+)\)", label):
            aliases.extend(part.strip() for part in alternate.split("/"))
        notes = (extra + " " + " ".join(comment.split()))[:600].strip()
        value = profile(downlink, uplink, rx_mode, tx_mode, notes, source)
        # AMSAT publishes a shared FM pair for the TEVEL2 constellation.
        group = re.fullmatch(r"(.+-)(\d+)\s+thru\s+\1(\d+)", canonical, re.I)
        if group and 0 <= int(group[3]) - int(group[2]) < 30:
            for number in range(int(group[2]), int(group[3]) + 1):
                name = group[1] + str(number)
                result.append(dict(label=name, aliases=[name], profile=deepcopy(value)))
        else:
            result.append(dict(label=title, aliases=aliases, profile=value))
    return result


def download_table(url):
    request = Request(url, headers={"User-Agent": "SatelliteAlert/1.0 frequency lookup"})
    with urlopen(request, timeout=8, context=ssl.create_default_context(cafile=certifi.where())) as response:
        content = response.read(1_000_001)
        if len(content) > 1_000_000:
            raise ValueError("Frequency table exceeds the download limit.")
        return content.decode("utf-8")


class FrequencyLookup:
    def __init__(self, fetcher=None, clock=time.monotonic):
        self.fetcher, self.clock = fetcher or download_table, clock
        self.lock = threading.Lock()
        self.expires = 0
        self.entries, self.failed_sources = [], []

    def lookup(self, name, aliases=()):
        keys = {_key(value) for value in (name, *aliases)}
        with self.lock:
            if self.clock() >= self.expires:
                entries, failed = [], []
                with ThreadPoolExecutor(max_workers=2) as pool:
                    downloads = {source: pool.submit(self.fetcher, source) for source in (FM_SOURCE, LINEAR_SOURCE)}
                    for source, pending in downloads.items():
                        try:
                            parsed = parse_profiles(pending.result(), source)
                            if not parsed:
                                raise ValueError("No supported profiles in the frequency table.")
                            entries.extend(parsed)
                        except (OSError, ValueError, UnicodeError):
                            failed.append(source)
                self.entries, self.failed_sources = entries, failed
                self.expires = self.clock() + (60 if failed else 86400)
            matches = [dict(label=item["label"], profile=deepcopy(item["profile"])) for item in self.entries
                       if keys.intersection(_key(alias) for alias in item["aliases"])]
            bundled = False
            if not matches:
                matches = [dict(label=label, profile=deepcopy(value)) for label, value in DEFAULT_PROFILES.items()
                           if value["source"] in self.failed_sources and _key(label) in keys]
                bundled = bool(matches)
            if matches:
                message = ("Using the bundled reference from 8 October 2026 because the live table is unavailable. " if bundled else "Found in AMSAT's frequency tables. ")
                message += "Choose an operating profile below." if len(matches) > 1 else "Review the frequencies and modes before saving."
                return dict(status="found", matches=matches, message=message, bundled=bundled)
            if self.failed_sources:
                return dict(status="unavailable", matches=[], message="No match was found in the available tables; part or all of the automatic lookup is unavailable. Enter the downlink, uplink, receive mode and transmit mode manually.")
            return dict(status="not_found", matches=[], message="No matching frequency profile was found in AMSAT's FM or linear tables. Enter the downlink, uplink, receive mode and transmit mode manually.")
