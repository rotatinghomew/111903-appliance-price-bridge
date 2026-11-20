"""合同篮子：覆盖、缺口、门槛、方向、金额。"""

import unittest

from support import season


class CoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_missing_region_weight_is_not_moved_to_stocked_region(self):
        result = self.analysis.contract("K3", period="2026-W42", as_of="2026-12-05")
        self.assertEqual(result.status, "coverage_insufficient")
        self.assertAlmostEqual(result.covered_weight, 0.7)
        self.assertLess(result.covered_weight, result.coverage_min)

    def test_not_listed_is_not_zero_change(self):
        result = self.analysis.contract("K2", period="2026-W42", as_of="2026-12-05")
        self.assertEqual(result.status, "coverage_insufficient")
        self.assertEqual(result.covered_weight, 0)
        self.assertIsNone(result.items[0].change)

    def test_no_evidence_item_requires_contract_bound_to_enter_total(self):
        result = self.analysis.contract("K1", period="2026-W42", as_of="2026-10-20")
        self.assertEqual(result.status, "coverage_insufficient")
        self.assertTrue(any("不能按零涨跌" in note for note in result.notes))


class ThresholdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_whole_range_must_exceed_threshold(self):
        result = self.analysis.contract("K1", period="2026-W42", as_of="2026-11-04")
        self.assertEqual(result.status, "threshold_not_established")
        self.assertEqual(result.threshold_state, "straddles")

    def test_below_threshold_is_no_adjustment_not_a_partial_amount(self):
        result = self.analysis.contract("K1", period="2026-W42", as_of="2026-12-05")
        self.assertEqual(result.status, "no_adjustment")
        self.assertIsNone(result.amount)

    def test_undetermined_fee_version_stops_comparability(self):
        result = self.analysis.contract("K1", period="2026-W42", as_of="2026-11-05")
        self.assertEqual(result.status, "coverage_insufficient")
        self.assertIn("未裁定版本", result.items[0].reason)


class AmountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_amount_never_exceeds_contract_rate_agreement(self):
        result = self.analysis.contract("K1", period="2026-W42", as_of="2026-11-04")
        self.assertIsNone(result.amount)

    def test_same_fee_source_takes_one_value_per_basket(self):
        result = self.analysis.contract("K1", period="2026-W42", as_of="2026-12-05")
        self.assertEqual(result.used_fees.get("F6"), 98)


if __name__ == "__main__":
    unittest.main()
