import json
import sys
from datetime import date
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import apartment_hunt as ah  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CFG = ah.load_config(ROOT / "config.toml")


def listing(**kw):
    base = dict(id="x", url="http://x", title="1BR apartment", price=1600, location="")
    base.update(kw)
    return ah.Listing(**base)


class ParseTests(unittest.TestCase):
    def test_search_results(self):
        results = ah.parse_search_results((FIXTURES / "search.html").read_text())
        self.assertEqual(len(results), 3)
        first = results[0]
        self.assertEqual(first.id, "cl-7811111111")
        self.assertEqual(first.price, 1650)
        self.assertEqual(first.location, "Logan Circle")
        self.assertIn("Sun-filled", first.title)

    def test_detail_page(self):
        l = listing()
        ah.enrich_from_detail(l, (FIXTURES / "detail.html").read_text())
        self.assertAlmostEqual(l.lat, 38.9101)
        self.assertAlmostEqual(l.lon, -77.0318)
        self.assertIn("floor-to-ceiling", l.description)
        self.assertIn("w/d in unit", l.description)
        self.assertNotIn("QR Code", l.description)

    def test_search_url(self):
        url = ah.craigslist_search_url(CFG)
        self.assertTrue(url.startswith("https://washingtondc.craigslist.org/search/doc/apa?"))
        self.assertIn("max_price=1800", url)  # 1700 + 100 stretch
        self.assertIn("min_bedrooms=1", url)


class EvaluateTests(unittest.TestCase):
    def test_full_pipeline_ranks_good_listing(self):
        results = ah.parse_search_results((FIXTURES / "search.html").read_text())
        ah.enrich_from_detail(results[0], (FIXTURES / "detail.html").read_text())
        for l in results:
            ah.evaluate(l, CFG)
        good, basement, anacostia = results
        self.assertIsNone(good.rejected)
        self.assertEqual(good.neighborhood, "Logan Circle")
        self.assertGreaterEqual(good.score, 90)
        self.assertEqual(good.nearest_metro, "U St")
        self.assertIn("in_unit_laundry", good.amenities)
        self.assertIn("basement", basement.rejected)
        self.assertIn("excluded", anacostia.rejected)

    def test_basement_euphemisms(self):
        for phrase in ["garden level 1BR", "Terrace-level apartment", "lower level unit", "below grade"]:
            l = ah.evaluate(listing(title=phrase), CFG)
            self.assertIsNotNone(l.rejected, phrase)

    def test_over_budget_and_scam_price(self):
        self.assertIn("over budget", ah.evaluate(listing(price=1850), CFG).rejected)
        stretched = ah.evaluate(listing(price=1750), CFG)
        self.assertIsNone(stretched.rejected)
        self.assertTrue(any("stretch" in f for f in stretched.flags))
        cheap = ah.evaluate(listing(price=700, location="Dupont"), CFG)
        self.assertTrue(any("scam" in f for f in cheap.flags))

    def test_scam_language(self):
        l = ah.evaluate(listing(description="I am out of the country for work, I will mail you the keys"), CFG)
        self.assertIn("scam", l.rejected)

    def test_coordinates_beat_claimed_neighborhood(self):
        # Claims Capitol Hill, but the pin is in Congress Heights.
        l = ah.evaluate(listing(location="Capitol Hill", lat=38.8445, lon=-76.9985), CFG)
        self.assertEqual(l.neighborhood, "Congress Heights")
        self.assertIsNotNone(l.rejected)

    def test_tier_override(self):
        cfg = {**CFG, "location": {**CFG["location"], "tier_overrides": {"Georgetown": "excluded"}}}
        self.assertIsNotNone(ah.evaluate(listing(location="Georgetown"), cfg).rejected)

    def test_studio_and_shared_rejected(self):
        self.assertIn("studio", ah.evaluate(listing(title="Bright studio in Shaw"), CFG).rejected)
        self.assertIn("shared", ah.evaluate(listing(title="Room for rent in Petworth"), CFG).rejected)

    def test_unknown_neighborhood_flagged(self):
        l = ah.evaluate(listing(location="Washington"), CFG)
        self.assertIsNone(l.rejected)
        self.assertEqual(l.tier, "unknown")


class NewCriteriaTests(unittest.TestCase):
    TODAY = date(2026, 12, 1)

    def test_must_haves(self):
        shared = ah.evaluate(listing(description="Shared laundry in the building. Central air."), CFG, self.TODAY)
        self.assertIn("in unit laundry", shared.rejected)
        window = ah.evaluate(listing(description="W/D in unit, window units provided"), CFG, self.TODAY)
        self.assertIn("central air", window.rejected)
        silent = ah.evaluate(listing(description="Nice place. " * 30), CFG, self.TODAY)
        self.assertIsNone(silent.rejected)
        self.assertEqual(sum("confirm" in f for f in silent.flags), 2)
        sparse = ah.evaluate(listing(description="Nice place"), CFG, self.TODAY)
        self.assertTrue(any("open the listing" in f for f in sparse.flags))
        self.assertGreater(sparse.score, silent.score)

    def test_move_in_window(self):
        ok = ah.evaluate(listing(description="Available Jan 25"), CFG, self.TODAY)
        self.assertEqual(ok.available, date(2027, 1, 25))
        self.assertTrue(any("matches your move" in r for r in ok.reasons))
        early = ah.evaluate(listing(description="available now!"), CFG, self.TODAY)
        self.assertTrue(any("days early" in f for f in early.flags))
        late = ah.evaluate(listing(description="move-in 3/15"), CFG, self.TODAY)
        self.assertTrue(any("not available until" in f for f in late.flags))

    def test_floor_privacy(self):
        self.assertEqual(ah.unit_floor("Apt 504"), 5)
        self.assertEqual(ah.unit_floor("Unit 1204"), 12)
        self.assertEqual(ah.unit_floor("#3B"), 3)
        self.assertEqual(ah.unit_floor("Unit LL"), "basement")
        self.assertEqual(ah.unit_floor("Apt 002"), "basement")
        self.assertEqual(ah.unit_floor("PH2"), "top")
        self.assertEqual(ah.unit_floor("Unit G"), "maybe_lower")
        ground = ah.evaluate(listing(description="ground floor unit"), CFG, self.TODAY)
        self.assertTrue(any("see in" in f for f in ground.flags))
        self.assertIsNotNone(ah.evaluate(listing(unit="LL"), CFG, self.TODAY).rejected)

    def test_size(self):
        big = ah.evaluate(listing(description="780 sq ft with a den"), CFG, self.TODAY)
        self.assertEqual(big.sqft, 780)
        self.assertIn("workspace", big.amenities)
        tiny = ah.evaluate(listing(description="cozy 420 sqft"), CFG, self.TODAY)
        self.assertTrue(any("tight" in f for f in tiny.flags))

    def test_dc_only(self):
        self.assertTrue(ah.in_dc(38.9215, -77.0422))   # Adams Morgan
        self.assertTrue(ah.in_dc(38.9076, -77.0654))   # Georgetown
        self.assertTrue(ah.in_dc(38.8765, -77.0035))   # Navy Yard
        self.assertFalse(ah.in_dc(38.8960, -77.0710))  # Rosslyn
        self.assertFalse(ah.in_dc(38.8870, -77.0950))  # Clarendon
        self.assertFalse(ah.in_dc(38.9840, -77.0940))  # Bethesda
        pinned = ah.evaluate(listing(location="Georgetown", lat=38.8960, lon=-77.0710), CFG, self.TODAY)
        self.assertIn("outside DC", pinned.rejected)
        self.assertIn("outside DC", ah.evaluate(listing(location="Arlington, VA"), CFG, self.TODAY).rejected)

    def test_favorite_bonus(self):
        a = ah.evaluate(listing(location="Adams Morgan"), CFG, self.TODAY)
        b = ah.evaluate(listing(location="Navy Yard"), CFG, self.TODAY)
        self.assertEqual(a.score - b.score, 5)


class SourceTests(unittest.TestCase):
    def test_alert_email(self):
        rows = ah.parse_alert_email((FIXTURES / "zillow_alert.html").read_text(), "zillow")
        by_addr = {r.address: r for r in rows}
        self.assertEqual(set(by_addr), {"1850 Columbia Rd NW", "1101 3rd St SW", "4000 Wisconsin Ave NW"})
        cr = by_addr["1850 Columbia Rd NW"]
        self.assertEqual((cr.price, cr.unit, cr.sqft, cr.bedrooms), (1725, "402", 640, 1.0))
        self.assertIn("zillow.com/homedetails/1850", cr.url)
        self.assertEqual(by_addr["1101 3rd St SW"].price, 1650)

    def test_rentcast(self):
        rows = ah.parse_rentcast(json.loads((FIXTURES / "rentcast.json").read_text()))
        self.assertEqual(len(rows), 2)
        r = rows[0]
        self.assertEqual((r.price, r.unit, r.sqft), (1695, "Apt 503", 610))
        l = ah.evaluate(r, CFG, date(2026, 12, 1))
        self.assertEqual(l.neighborhood, "Adams Morgan")
        self.assertTrue(any("floor 5" in x for x in l.reasons))

    def test_merge_duplicates(self):
        a = listing(id="a", url="http://zillow", address="1850 Columbia Rd NW", unit="402", source="zillow")
        b = listing(id="b", url="http://cl", address="1850 Columbia Road N.W.", unit="Apt 402",
                    source="craigslist", description="long description with details", lat=38.92, lon=-77.04)
        merged = ah.merge_duplicates([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].url, "http://cl")
        self.assertEqual(merged[0].also_on, ["zillow: http://zillow"])


class CsvImportTests(unittest.TestCase):
    def test_import(self):
        path = FIXTURES / "manual.csv"
        rows = ah.load_csv(str(path))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].price, 1695)
        self.assertEqual(rows[0].source, "manual")


if __name__ == "__main__":
    unittest.main()
