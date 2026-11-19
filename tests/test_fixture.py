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

    def test_payment_and_fee_ledgers_refer_to_same_settlement_period(self):
        path = Path(__file__).parents[1] / "data" / "overlap-season.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        statements = {row["id"]: row for row in value["fee_statements"]}
        agreements = {row["id"]: row for row in value["rate_agreements"]}
        notices = {row["id"]: row for row in value["settlements"]}
        batches = {row["id"]: row for row in value["bank_batches"]}
        windows = {row["window"]: row for row in value["payment_windows"]}
        for resolution in value["fee_resolutions"]:
            self.assertIn(resolution["selected"], resolution["considered"])
            for reference in resolution["considered"]:
                self.assertEqual(statements[reference]["observation"], resolution["observation"])
                self.assertLessEqual(statements[reference]["received_on"], resolution["signed_on"])
        for notice in notices.values():
            if "agreement" in notice:
                agreement = agreements[notice["agreement"]]
                self.assertEqual((notice["contract"], notice["period"]), (agreement["contract"], agreement["period"]))
                self.assertLessEqual(agreement["signed_on"], notice["issue_date"])
                self.assertTrue(set(agreement["fee_sources"]) <= set(notice["sources"]))
        for batch in batches.values():
            window = windows[batch["window"]]
            self.assertLessEqual(window["from"], batch["sent_on"])
            self.assertLessEqual(batch["sent_on"], window["through"])
            for notice_id in batch["notices"]:
                self.assertLessEqual(notices[notice_id]["issue_date"], batch["sent_on"])
        for receipt in value["bank_receipts"]:
            self.assertLessEqual(batches[receipt["batch"]]["sent_on"], receipt["paid_on"])
            self.assertLessEqual(receipt["paid_on"], receipt["received_on"])
        for dispute in value["notice_disputes"]:
            self.assertLessEqual(notices[dispute["notice"]]["issue_date"], dispute["received_on"])
            self.assertLessEqual(dispute["received_on"], dispute["release_signed_on"])

if __name__ == "__main__":
    unittest.main()
