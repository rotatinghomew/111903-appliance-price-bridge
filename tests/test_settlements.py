"""结算台账：订正、付款窗口、争议、回执去重。"""

import unittest

from support import season


class NoticeStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_bank_batch_is_frozen_and_not_rewritten(self):
        states = self.analysis.receipts()
        self.assertEqual(states["BANK-2"]["receipts"], ["BR-2"])
        self.assertEqual(states["BANK-2"]["duplicate_deliveries"], 1)

    def test_released_positive_difference_is_payable(self):
        window = self.analysis.window("B2")
        payable = {row["notice"] for row in window["payable"]}
        self.assertIn("S4", payable)
        deferred = {row["notice"] for row in window["deferred"]}
        self.assertEqual(deferred, {"S1", "S2", "S3"})

    def test_sent_batch_is_never_reissued_in_a_later_window(self):
        window = self.analysis.window("B5")
        self.assertEqual(window["payable_total"], 760)
        self.assertEqual({row["notice"] for row in window["deferred"]}, {"S1", "S2", "S3"})

    def test_dispute_only_holds_unsent_amount(self):
        window = self.analysis.window("B2")
        self.assertNotIn("S3", {row["notice"] for row in window["payable"]})

    def test_difference_totals_are_per_contract_and_period(self):
        result = self.analysis.difference("K1", "2026-W42")
        self.assertAlmostEqual(result["signed_total"], 760)
        self.assertAlmostEqual(result["sent_total"], 0)

    def test_k2_difference_does_not_absorb_k1_notices(self):
        result = self.analysis.difference("K2", "2026-W42")
        self.assertAlmostEqual(result["signed_total"], 1440)


class NoticeTraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_merchant_can_trace_a_notice_to_its_week_and_fees(self):
        trace = self.analysis.notice("S1")
        self.assertEqual(trace["period"], "2026-W42")
        weeks = {row["week"] for row in trace["observations"]}
        self.assertIn("2026-W40", weeks)
        self.assertEqual({row["id"] for row in trace["fee_statements"]}, {"F1", "F2"})
        self.assertEqual(trace["agreement"]["rate"], 0.05)

    def test_amendment_does_not_rewrite_original_notice_sources(self):
        original = self.analysis.inputs.settlements["S1"]
        self.assertIn("F2", original["sources"])
        self.assertNotIn("F6", original["sources"])

    def test_worklist_separates_procurement_from_finance(self):
        worklist = self.analysis.worklist()
        self.assertTrue(all("action" in row for row in worklist["finance"]))


if __name__ == "__main__":
    unittest.main()
