"""规则边界：合同约定缺口区间、共享新品证据、混合批次。"""

import unittest

from support import Scenario


class ContractBoundTest(unittest.TestCase):
    def test_contract_bound_enters_total_when_item_comparable(self):
        scenario = Scenario()
        # K3 的 S 区缺价，但合同给了该位置的涨跌上下界。
        entry = scenario.contract("K3")["basket"][1]
        entry["uncovered_change_range"] = [0.0, 0.06]
        analysis = scenario.build()
        result = analysis.contract("K3", period="2026-W42", as_of="2026-12-05")
        self.assertEqual(result.status, "coverage_insufficient")
        # 覆盖仍不足，但缺口不再影响方向判断；总范围应把 S 区按 0..6% 计入。
        self.assertIsNotNone(result.total_change)
        self.assertAlmostEqual(result.total_change.high, 0.3 * 0.06 + 0.7 * 0.021531, places=5)

    def test_contract_bound_cannot_be_zero_by_default(self):
        analysis = Scenario().build()
        result = analysis.contract("K3", period="2026-W42", as_of="2026-12-05")
        # 缺口没有约定区间时，不能把 S 区按 0 计入；总范围只覆盖可比部分，
        # 并且明确记录缺口。
        self.assertTrue(any("不能按零涨跌" in note for note in result.notes))
        self.assertIsNone(result.items[1].contract_bound)
        self.assertIsNone(result.items[1].change)
        # 只有可比部分进入记录的总范围，缺口没有被当成零。
        self.assertAlmostEqual(result.total_change.high, 0.7 * 0.021531, places=5)
        self.assertEqual(result.status, "coverage_insufficient")


class SharedEvidenceTest(unittest.TestCase):
    def test_two_old_skus_cannot_both_count_same_new_sku(self):
        scenario = Scenario()
        scenario.contract("K3")["basket"] = [
            {"sku": "OLD1", "region": "N", "weight": 0.5},
            {"sku": "OLD2", "region": "N", "weight": 0.5},
        ]
        # 让 OLD2→NEW1 也成为一项正式换代质量档，并给一条已确认接续。
        scenario.chain["quality_claims"].append(
            {"id": "Q-C", "old": "OLD2", "new": "NEW1", "range": [0, 12], "regions": ["N"]}
        )
        scenario.chain["confirmed_links"].append(
            {
                "id": "L-N4",
                "series": "IDX-N",
                "old_observation": "P2",
                "new_observation": "P5",
                "claim": "Q-C",
                "confirmed_on": "2026-10-18",
                "cross_gap": True,
                "buyer": "BU-N",
                "merchant": "ME-N",
            }
        )
        analysis = scenario.build()
        result = analysis.contract("K3", period="2026-W42", as_of="2026-12-05")
        # OLD1 与 OLD2 都接 NEW1；同一批 NEW1 成交只能算一份证据。
        self.assertAlmostEqual(result.covered_weight, 0.5)
        self.assertEqual(result.status, "coverage_insufficient")
        reused = [item for item in result.items if item.change is None]
        self.assertTrue(reused)
        self.assertIn("同一批新机", reused[0].reason)


class MixedBatchTest(unittest.TestCase):
    def test_follow_reissue_uses_last_complete_pair(self):
        scenario = Scenario()
        # 只让 10 月进入新批次 ED-1，11 月仍是旧批次 ED-0。
        scenario.chain["editions"] = [
            row for row in scenario.chain["editions"] if row["id"] != "ED-N2"
        ]
        analysis = scenario.build()
        basis = analysis.reference_basis("K1", "2026-12-02")
        self.assertEqual(basis.status, "ok")
        # 不能把 ED-O2 与 ED-N 拼成一对。
        self.assertEqual(
            (basis.base.edition_id, basis.current.edition_id), ("ED-O", "ED-N")
        )

    def test_follow_reissue_waits_when_no_complete_pair_exists(self):
        scenario = Scenario()
        # 删掉 11 月全部版本：只剩 10 月的重发，凑不出一对。
        scenario.chain["editions"] = [
            row
            for row in scenario.chain["editions"]
            if row["month"] != "2026-11" or row["series"] != "IDX-N"
        ]
        analysis = scenario.build()
        basis = analysis.reference_basis("K1", "2026-12-05")
        self.assertEqual(basis.status, "awaiting_edition")
        self.assertIn("不存在", basis.reason)


class FeeDisputeOnContractTest(unittest.TestCase):
    def test_unresolved_fee_versions_surface_on_affected_contract(self):
        scenario = Scenario()
        # 去掉共同裁定，让 F4/F5 两个版本都悬着。
        scenario.season["fee_resolutions"] = []
        analysis = scenario.build()
        result = analysis.contract("K1", period="2026-W42", as_of="2026-11-05")
        self.assertEqual(result.status, "coverage_insufficient")
        self.assertIn("未裁定版本", result.items[0].reason)
        # 原版通知仍可查，不被抹掉。
        self.assertIn("S1", analysis.inputs.settlements)
        # 引用受影响凭据的已签通知（含已送银行的）显示为等待费用争议。
        states = analysis.settlement_states()
        self.assertEqual(states["S1"]["status"], "sent")  # 已送银行不回头改写
        self.assertTrue(states["S1"]["fee"]["awaiting_resolution"])
        # 争议期间不能把未送银行的净额提前结清。
        difference = analysis.difference("K1", "2026-W42")
        self.assertIn("费用版本尚未共同裁定", " ".join(difference["notes"]))
        self.assertEqual(difference["status"], "awaiting_fee_resolution")


if __name__ == "__main__":
    unittest.main()
