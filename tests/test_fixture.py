"""检查合成资料可读。"""
import json
from pathlib import Path
import unittest

class FixtureTest(unittest.TestCase):
    def test_sample_is_json_object(self):
        path = Path(__file__).parents[1] / "data" / "sample.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        self.assertTrue(value)

    def test_season_revisions_and_issued_notices_refer_to_known_inputs(self):
        path = Path(__file__).parents[1] / "data" / "overlap-season.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        products = {row["sku"] for row in value["products"]}
        observations = {row["id"]: row for row in value["observations"]}
        contracts = {row["id"]: row for row in value["contracts"]}
        statements = {row["id"]: row for row in value["fee_statements"]}
        for row in observations.values():
            self.assertIn(row["sku"], products)
        for row in contracts.values():
            self.assertAlmostEqual(sum(item["weight"] for item in row["basket"]), 1)
            self.assertTrue(all(item["sku"] in products for item in row["basket"]))
        for row in statements.values():
            self.assertIn(row["observation"], observations)
            if "replaces" in row:
                previous = statements[row["replaces"]]
                self.assertEqual(row["observation"], previous["observation"])
                self.assertGreater(row["received_on"], previous["received_on"])
        for row in value["settlements"]:
            self.assertIn(row["contract"], contracts)
            self.assertTrue(set(row["sources"]) <= set(observations) | set(statements))

if __name__ == "__main__":
    unittest.main()
