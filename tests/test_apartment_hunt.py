import sys
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
        self.assertIn("max_price=1700", url)
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
        self.assertIn("over budget", ah.evaluate(listing(price=1800), CFG).rejected)
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


class CsvImportTests(unittest.TestCase):
    def test_import(self):
        path = FIXTURES / "manual.csv"
        rows = ah.load_csv(str(path))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].price, 1695)
        self.assertEqual(rows[0].source, "manual")


if __name__ == "__main__":
    unittest.main()
