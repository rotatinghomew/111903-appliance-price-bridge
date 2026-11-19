"""检查合成资料可读。"""
import json
from pathlib import Path
import unittest

class FixtureTest(unittest.TestCase):
    def test_index_publications_link_in_order_to_regional_quotes_and_contracts(self):
        data = Path(__file__).parents[1] / "data"
        season = json.loads((data / "overlap-season.json").read_text(encoding="utf-8"))
        chain = json.loads((data / "publication_chain.json").read_text(encoding="utf-8"))
        observations = {row["id"]: row for row in season["observations"] + chain["observations"]}
        fees = {row["id"]: row for row in season["fee_statements"] + chain["fee_statements"]}
        series = {row["series"]: row for row in chain["series"]}
        claims = {row["id"]: row for row in chain["quality_claims"]}
        links = {row["id"]: row for row in chain["confirmed_links"]}
        editions = {row["id"]: row for row in chain["editions"]}
        for link in links.values():
            market = series[link["series"]]["region"]
            old = observations[link["old_observation"]]
            new = observations[link["new_observation"]]
            claim = claims[link["claim"]]
            self.assertEqual((old["region"], new["region"]), (market, market))
            self.assertEqual((old["sku"], new["sku"]), (claim["old"], claim["new"]))
            self.assertIn(market, claim["regions"])
            if old["week"] != new["week"]:
                self.assertTrue(link["cross_gap"] and link["buyer"] and link["merchant"])
        for edition in editions.values():
            for fee_id in edition["charges"]:
                self.assertIn(fees[fee_id]["observation"], edition["observations"])
                self.assertLessEqual(fees[fee_id]["received_on"], edition["published_on"])
            for link_id in edition["links"]:
                self.assertEqual(links[link_id]["series"], edition["series"])
                self.assertLessEqual(links[link_id]["confirmed_on"], edition["published_on"])
            if "replaces" in edition:
                old = editions[edition["replaces"]]
                self.assertEqual((old["series"], old["month"]), (edition["series"], edition["month"]))
                self.assertLess(old["published_on"], edition["published_on"])
        for reference in chain["contract_references"]:
            self.assertIn(reference["contract"], {row["id"] for row in season["contracts"]})
            base, current = (editions[reference[field]] for field in ("base_edition", "current_edition"))
            self.assertEqual(base["series"], current["series"])
            self.assertEqual(current["week"], reference["period"])

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
