"""换代接续：同款优先，跨代时逐代接上硬件与服务口径。

规则：

1. 同地区、同服务口径下，当期有同款成交就用同款成交；没有成交才用可核验的同
   款报价。
2. 两者都没有才走合同约定的正式换代。换代须在同地区有可比较的重叠周，或采购
   与商户都签字同意跨过断档。
3. 同档替代（``substitute``）不是换代，不自动提供接续。
4. 多个旧款接同一新品时，新品只提供一份证据；不能为了补齐覆盖把同一批新机销
   量分别计入几份独立证据。
5. 跨两代时每一代都接上硬件与服务口径，价格范围取共同支持的交集；同一硬件改
   动在各地区、各期使用同一份质量判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .inputs import Inputs
from .ranges import Range

# 一代的价位怎么取：
#   traded  —— 同款当期成交价
#   offered —— 同款当期可核验报价
#   bridge  —— 合同约定的正式换代
KIND_TRADED = "traded"
KIND_OFFERED = "offered"
KIND_BRIDGE = "bridge"

KIND_LABELS = {
    KIND_TRADED: "同款成交",
    KIND_OFFERED: "同款报价",
    KIND_BRIDGE: "约定换代",
}


@dataclass(frozen=True)
class QualityClaim:
    """一次硬件改动的质量价差判断。"""

    claim_id: str
    old: str
    new: str
    price_range: Range
    regions: frozenset[str]
    known_on: str
    base_id: str

    def applies_to(self, region: str) -> bool:
        return region in self.regions


def quality_claim(inputs: Inputs, old: str, new: str, region: str, as_of: str) -> QualityClaim | None:
    """同一硬件改动的质量档案，与期次、地区报价无关。

    订正只替代原判断并按其知悉日生效，不另立一项价差；同一硬件改动在各地区、
    各期都读这一份判断。
    """
    bases = [
        row
        for row in inputs.quality_claims.values()
        if (row["old"], row["new"]) == (old, new) and region in row.get("regions", [])
    ]
    if not bases:
        return None
    base = min(bases, key=lambda row: row["id"])
    low, high = (float(value) for value in base["range"])
    claim_id = base["id"]
    known_on = "0000-01-01"
    correction = next(
        (row for row in inputs.claim_corrections if row.get("claim") == base["id"]), None
    )
    if correction and correction["received_on"] <= as_of:
        low, high = (float(value) for value in correction["range"])
        claim_id = correction.get("id", base["id"])
        known_on = correction["received_on"]
    return QualityClaim(
        claim_id=claim_id,
        old=old,
        new=new,
        price_range=Range(low, high),
        regions=frozenset(base.get("regions", [])),
        known_on=known_on,
        base_id=base["id"],
    )


@dataclass(frozen=True)
class Step:
    """一代接续。``weight`` 是新一代相对旧一代的硬件可比价倍数区间。"""

    old_observation: str
    new_observation: str
    old_sku: str
    new_sku: str
    kind: str
    weight: Range
    claim_id: str | None = None
    claim_range: Range | None = None
    link_id: str | None = None
    cross_gap: bool = False

    @property
    def label(self) -> str:
        if self.kind == KIND_BRIDGE:
            return f"{self.old_sku}→{self.new_sku} 按质量档 {self.claim_id} 换代"
        return f"{self.old_sku} 当期同款{KIND_LABELS[self.kind][2:]}"


@dataclass
class Path:
    """从基准型号走到当期型号的路径。"""

    steps: list[Step]
    gap: str | None = None
    alternatives: list[str] | None = None

    @property
    def ok(self) -> bool:
        return self.gap is None

    def weight(self) -> Range:
        out = Range(1.0, 1.0)
        for step in self.steps:
            out = Range(out.low * step.weight.low, out.high * step.weight.high)
        return out

    def labels(self) -> list[str]:
        return [step.label for step in self.steps]

    def evidence(self) -> list[str]:
        out: list[str] = []
        for step in self.steps:
            out.append(step.old_observation)
            out.append(step.new_observation)
            if step.link_id:
                out.append(step.link_id)
        return out


def shift_week(text: str, count: int) -> str | None:
    """把 ``2026-W40`` 平移若干周；解析不了返回 ``None``。"""
    head, _, tail = text.partition("-W")
    if not tail.isdigit():
        return None
    try:
        year, week = int(head), int(tail) + count
    except ValueError:
        return None
    if week < 1 or week > 53:
        return None
    return f"{year}-W{week:02d}"


def adjust_to_week(quote_week: str, target_week: str) -> int | None:
    """同款跨周的周差；只用于判断能否用邻近周的同款结果。"""
    if quote_week == target_week:
        return 0
    head, _, tail = quote_week.partition("-W")
    head2, _, tail2 = target_week.partition("-W")
    if not (head.isdigit() and tail.isdigit() and head2.isdigit() and tail2.isdigit()):
        return None
    return (int(head2) - int(head)) * 52 + (int(tail2) - int(tail))


def confirmed_link(
    inputs: Inputs,
    *,
    old_observation: str,
    new_observation: str,
    claim: QualityClaim,
    as_of: str,
    scope: Iterable[str] | None,
) -> dict[str, Any] | None:
    """找唯一一条与这项硬件改动对得上的已确认接续。"""
    allowed = set(scope) if scope is not None else None
    candidates = [
        row
        for row in inputs.confirmed_links.values()
        if row["old_observation"] == old_observation
        and row["new_observation"] == new_observation
        and row.get("claim") in ({claim.base_id} | {claim.claim_id})
        and (allowed is None or row["id"] in allowed)
    ]
    if len(candidates) != 1:
        return None
    link = candidates[0]
    if link["confirmed_on"] > as_of:
        return None
    return link


def gap_permitted(inputs: Inputs, link: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> bool:
    if old["week"] == new["week"]:
        return True
    return bool(link.get("cross_gap") and link.get("buyer") and link.get("merchant"))


def bridge_step(
    inputs: Inputs,
    *,
    old_observation: str,
    new_observation: str,
    claim: QualityClaim,
    as_of: str,
    scope: Iterable[str] | None,
) -> Step | None:
    """用已确认接续与质量档案接一代；接不上返回 ``None``。"""
    link = confirmed_link(
        inputs,
        old_observation=old_observation,
        new_observation=new_observation,
        claim=claim,
        as_of=as_of,
        scope=scope,
    )
    if link is None:
        return None
    old_row = inputs.observations[old_observation]
    new_row = inputs.observations[new_observation]
    if not gap_permitted(inputs, link, old_row, new_row):
        return None
    old_price = float(old_row["price"])
    new_price = float(new_row["price"])
    hardware = claim.price_range
    total_old = Range(old_price + hardware.low, old_price + hardware.high)
    return Step(
        old_observation=old_observation,
        new_observation=new_observation,
        old_sku=old_row["sku"],
        new_sku=new_row["sku"],
        kind=KIND_BRIDGE,
        weight=Range(new_price / total_old.high, new_price / total_old.low),
        claim_id=claim.claim_id,
        claim_range=hardware,
        link_id=link["id"],
        cross_gap=bool(link.get("cross_gap")),
    )


def replacement_targets(inputs: Inputs, sku: str, region: str) -> set[str]:
    """同地区已知能接上的换代型号（只来自 replacement / 质量档案）。"""
    out: set[str] = set()
    for bridge in inputs.bridges:
        if bridge.get("old") == sku and bridge.get("relationship") == "replacement":
            if bridge.get("region") in (None, region):
                out.add(bridge["new"])
    for claim in inputs.quality_claims.values():
        if claim["old"] == sku and region in claim.get("regions", []):
            out.add(claim["new"])
    return out


def substitute_target(inputs: Inputs, sku: str, region: str) -> set[str]:
    """同档替代：不是换代，只用于说明“有替代但不自动接续”。"""
    out: set[str] = set()
    for bridge in inputs.bridges:
        if bridge.get("old") == sku and bridge.get("relationship") == "substitute":
            if bridge.get("region") in (None, region):
                out.add(bridge["new"])
    return out
