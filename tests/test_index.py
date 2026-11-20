"""指数评估：同款优先、换代接续、共同支持范围、刊期冻结。"""

import unittest

from support import season


class DirectAndFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_same_sku_trade_beats_bridge_when_available(self):
        level = self.analysis.series("IDX-L", "2026-W46", "2026-11-18")
        self.assertEqual(level.kind, "direct")
        self.assertAlmostEqual(level.level.low, 606 / 590)
        self.assertEqual(level.level.low, level.level.high)

    def test_offered_quote_is_used_only_when_no_trade_exists(self):
        level = self.analysis.series("IDX-S", "2026-W41", "2026-11-26")
        self.assertEqual(level.current.status, "offered")
        self.assertEqual(level.current.observation_id, "P15")
        # 无成交的报价仍可作同款结果，但不产生销量权重。
        self.assertEqual(level.current.sales, 0)

    def test_not_listed_and_withdrawn_are_not_zero_change(self):
        level = self.analysis.series("IDX-S", "2026-W42", "2026-11-26")
        self.assertIsNone(level.level)
        self.assertEqual(level.kind, "no_evidence")
        self.assertIn("没有", level.reason)

    def test_substitute_is_not_a_replacement(self):
        level = self.analysis.series("IDX-N", "2026-W42", "2026-10-20")
        # 此时只有 OLD1 存在，OLD1→ST1 是同档替代，不构成接续。
        self.assertEqual(level.kind, "no_evidence")
        self.assertIn("同档替代", level.reason)


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_new_installed_price_is_reduced_by_fee_before_bridging(self):
        level = self.analysis.series("IDX-N", "2026-W42", "2026-11-18")
        self.assertEqual(level.kind, "quality_adjusted")
        # 525 含 90 安装费，硬件 435；旧款 410 加 8..15 质量档。
        self.assertAlmostEqual(level.level.low, 435 / 425)
        self.assertAlmostEqual(level.level.high, 435 / 418)

    def test_bridge_requires_overlap_or_signature(self):
        level = self.analysis.series("IDX-S", "2026-W43", "2026-11-26")
        self.assertEqual(level.kind, "quality_adjusted")
        generation = level.generations[0]
        self.assertTrue(generation.cross_gap)
        self.assertEqual(generation.link_id, "L-S1")

    def test_crossing_two_generations_adds_both_quality_ranges(self):
        level = self.analysis.series("IDX-N", "2026-W46", "2026-11-18")
        self.assertEqual([gen.claim_id for gen in level.generations], ["Q-A", "Q-B"])
        self.assertAlmostEqual(level.quality.low, 8 + 13)
        self.assertAlmostEqual(level.quality.high, 15 + 21)

    def test_direct_claim_and_generation_chain_agree_on_common_range(self):
        """OLD1→NEW2 直接档与 OLD1→NEW1→NEW2 逐代档必须取共同支持范围。"""
        level = self.analysis.series("IDX-N", "2026-W46", "2026-11-28")
        self.assertIn("OLD1→NEW1→NEW2", level.paths)
        self.assertIn("OLD1→NEW2", level.paths)
        # 直接档 22..28 与逐代档 21..36 的共同支持范围。
        self.assertAlmostEqual(level.quality.low, 22)
        self.assertAlmostEqual(level.quality.high, 28)

    def test_cross_gap_without_both_signatures_is_rejected(self):
        level = self.analysis.series("IDX-N", "2026-W46", "2026-11-27")
        # L-N3 在 11-28 才确认，之前只能走两代重叠路径。
        self.assertNotIn("OLD1→NEW2", level.paths)

    def test_quality_correction_applies_after_receipt_only(self):
        before = self.analysis.series("IDX-N", "2026-W46", "2026-11-27")
        self.assertEqual(before.quality.low, 21)
        self.assertEqual(before.kind, "quality_adjusted")
        # 12-03 收到订正后，直接档与逐代档不再相容，按规则不得挑一条补覆盖。
        after = self.analysis.series("IDX-N", "2026-W46", "2026-12-05")
        self.assertEqual(after.kind, "no_evidence")
        self.assertTrue(after.disputed)
        self.assertIn("不挑选能补足覆盖", after.reason)


class PublicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = season()

    def test_published_edition_keeps_its_original_fee_basis(self):
        original = self.analysis.edition("ED-O")
        reissue = self.analysis.edition("ED-O2")
        self.assertEqual(original.current.fee_source, "statement:F5")
        self.assertEqual(reissue.current.fee_source, "statement:F6")
        self.assertNotEqual(original.level, reissue.level)

    def test_reissue_lists_every_affected_month(self):
        plan = self.analysis.reissue_plan("2026-12-05")
        months = {(row["series"], row["month"]) for row in plan["pending_reissue"]}
        self.assertIn(("IDX-N", "2026-10"), months)
        self.assertIn(("IDX-N", "2026-11"), months)
        self.assertIn("同一套可比依据", plan["note"])

    def test_follow_reissue_requires_full_pair_from_one_batch(self):
        basis = self.analysis.reference_basis("K1", "2026-11-20")
        self.assertEqual(basis.status, "ok")
        self.assertEqual(basis.batch, "ED-0")
        self.assertEqual(
            (basis.base.edition_id, basis.current.edition_id), ("ED-O", "ED-N")
        )

    def test_keep_original_contract_uses_original_editions(self):
        basis = self.analysis.reference_basis("K2", "2026-12-05")
        self.assertEqual(basis.policy, "keep_original")
        self.assertEqual(
            (basis.base.edition_id, basis.current.edition_id), ("ED-O", "ED-N")
        )

    def test_no_mixed_batch_pair_is_signed(self):
        # 12-01 只重发了 10 月，11 月仍是旧批次；不能拼成一对。
        basis = self.analysis.reference_basis("K1", "2026-12-01T23:59")
        self.assertEqual(basis.status, "ok")
        self.assertNotEqual(
            (basis.base.edition_id, basis.current.edition_id), ("ED-O2", "ED-N")
        )


if __name__ == "__main__":
    unittest.main()
