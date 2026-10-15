"""Offline lookup parsing, identity, caching and unavailable-source behavior."""
import unittest

from frequency_lookup import FrequencyLookup, parse_profiles
from profiles import FM_SOURCE, LINEAR_SOURCE

FM_HTML = """<table><tr><th>Satellite</th><th>Uplink</th><th>Downlink</th><th>Comment</th></tr>
<tr><td><a>AO-91<br>(RadFxSat / Fox-1B)</a></td><td>435.250 MHz</td><td>145.960 MHz</td><td>Example FM profile.</td></tr>
<tr><td>SO-50<br>(SaudiSat-1C)</td><td>145.850 MHz<br>(67 Hz CTCSS)</td><td>436.795 MHz</td><td>Example tone note.</td></tr>
<tr><td>TEVEL2-1 thru TEVEL2-9</td><td>145.970 MHz</td><td>436.400 MHz</td><td>Shared pair.</td></tr></table>"""
LINEAR_HTML = """<table><tr><th>Satellite</th><th>Frequencies</th><th>Comment</th></tr>
<tr><td><a>AO-73 (FUNcube-1)</a><br>U/v Inverting Analog</td><td>Uplink LSB: 435.130 MHz &#8211; 435.150 MHz<br>Downlink USB: 145.950 MHz – 145.970 MHz</td><td>Example schedule.</td></tr>
<tr><td>AO-7 Mode A<br>V/a Non-Inverting Analog</td><td>Uplink USB: 145.850 MHz – 145.950 MHz<br>Downlink USB: 29.400 MHz – 29.500 MHz</td><td>Example A.</td></tr>
<tr><td>AO-7 Mode B<br>U/v Inverting Analog</td><td>Uplink LSB: 432.125 MHz – 432.175 MHz<br>Downlink USB: 145.925 MHz – 145.975 MHz</td><td>Example B.</td></tr>
<tr><td>QO-100</td><td>Uplink USB: 2400.000 MHz – 2400.500 MHz<br>Downlink USB: 10489.500 MHz – 10490.000 MHz</td><td>Outside supported settings range.</td></tr></table>"""


class FrequencyLookupTests(unittest.TestCase):
    def make_lookup(self):
        self.calls = []
        self.now = 100.0

        def fetch(url):
            self.calls.append(url)
            return {FM_SOURCE: FM_HTML, LINEAR_SOURCE: LINEAR_HTML}[url]

        return FrequencyLookup(fetcher=fetch, clock=lambda: self.now)

    def test_fm_alias_and_ctcss_are_not_mistaken_for_a_frequency(self):
        lookup = self.make_lookup()
        result = lookup.lookup("SaudiSat-1C")
        self.assertEqual(result["status"], "found")
        value = result["matches"][0]["profile"]
        self.assertEqual((value["downlink_mhz"], value["uplink_mhz"]), (436.795, 145.850))
        self.assertEqual((value["downlink_mode"], value["uplink_mode"]), ("FM", "FM"))
        self.assertIn("67 Hz", value["notes"])
        self.assertEqual(value["source"], FM_SOURCE)

    def test_linear_centres_and_explicit_modes_and_norad_alias(self):
        result = self.make_lookup().lookup("39444", ["AO-73"])
        value = result["matches"][0]["profile"]
        self.assertEqual((value["downlink_mhz"], value["uplink_mhz"]), (145.960, 435.140))
        self.assertEqual((value["downlink_mode"], value["uplink_mode"]), ("USB", "LSB"))
        self.assertEqual(value["source"], LINEAR_SOURCE)

    def test_exact_alias_matching_never_guesses_similar_names(self):
        lookup = self.make_lookup()
        self.assertEqual(lookup.lookup("fox-1b")["matches"][0]["label"], "AO-91")
        for name in ("AO-9", "AO-97", "UNKNOWN", "QO-100"):
            with self.subTest(name=name):
                result = lookup.lookup(name)
                self.assertEqual(result["status"], "not_found")
                self.assertEqual(result["matches"], [])
                self.assertIn("manually", result["message"])

    def test_multiple_operating_profiles_are_returned_for_user_choice(self):
        matches = self.make_lookup().lookup("AO-7")["matches"]
        self.assertEqual([row["label"] for row in matches], ["AO-7 Mode A", "AO-7 Mode B"])
        self.assertEqual(matches[0]["profile"]["uplink_mode"], "USB")
        self.assertEqual(matches[1]["profile"]["uplink_mode"], "LSB")

    def test_shared_constellation_only_matches_in_range_satellites(self):
        lookup = self.make_lookup()
        for number in (1, 5, 9):
            self.assertEqual(lookup.lookup(f"TEVEL2-{number}")["matches"][0]["profile"]["uplink_mhz"], 145.970)
        self.assertEqual(lookup.lookup("TEVEL2-10")["status"], "not_found")

    def test_cache_expires_and_returned_profiles_are_independent(self):
        lookup = self.make_lookup()
        result = lookup.lookup("AO-91")
        result["matches"][0]["profile"]["downlink_mhz"] = 1
        self.assertEqual(lookup.lookup("AO-91")["matches"][0]["profile"]["downlink_mhz"], 145.960)
        self.assertEqual(len(self.calls), 2)
        self.now += 86401
        lookup.lookup("AO-91")
        self.assertEqual(len(self.calls), 4)

    def test_offline_and_changed_page_report_manual_entry_and_explicit_bundled_fallback(self):
        for fetcher in (lambda url: "<html>Maintenance</html>", self.unavailable):
            with self.subTest(fetcher=fetcher):
                lookup = FrequencyLookup(fetcher=fetcher)
                unknown = lookup.lookup("UNKNOWN")
                self.assertEqual(unknown["status"], "unavailable")
                self.assertIn("manually", unknown["message"])
                saved = lookup.lookup("ISS")
                self.assertTrue(saved["bundled"])
                self.assertIn("8 October 2026", saved["message"])
                self.assertEqual(saved["matches"][0]["profile"]["downlink_mhz"], 437.8)

    @staticmethod
    def unavailable(url):
        raise TimeoutError("Offline test")

    def test_incomplete_or_out_of_range_rows_are_not_autofilled(self):
        bad = FM_HTML.replace("436.795 MHz", "unknown").replace("145.960 MHz", "9000 MHz")
        self.assertFalse(any(row["label"] in ("SO-50", "AO-91") for row in parse_profiles(bad, FM_SOURCE)))


if __name__ == "__main__":
    unittest.main()
