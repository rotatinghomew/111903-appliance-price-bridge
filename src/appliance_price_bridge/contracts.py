"""合同调价复核：固定篮子、覆盖、门槛、方向与可结金额。

判断顺序是硬性的：

1. **覆盖**：按原合同篮子权重判断。缺价地区的权重不能转给有货地区。
2. **缺口处理**：跨过覆盖线也不表示缺价部分可按零涨跌算。篮子项有合同约定的
   涨跌上下界时按它进入总范围；没有约定就不能确认全篮子方向。
3. **方向**：只有整个范围落在同一方向，才继续判断金额。
4. **门槛**：整个范围都超过门槛才构成调价；不是只结算超过门槛的部分。范围跨
   在门槛上时，不能确认是否应调价。
5. **金额**：范围是点时可直接计价；否则要有商户与采购共同签认、对应当前依据、
   且落在支持范围内的结算比例。不得挑一个对己方有利的值。

“证据不足”“未达门槛”“方向已定待确认金额”是三个分开的状态，由采购分别处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .evidence import fee_value
from .index_eval import (
    NO_EVIDENCE,
    Level,
    evaluate_item,
    shared_evidence_groups,
)
from .inputs import Inputs
from .ranges import Range

COVERAGE_INSUFFICIENT = "coverage_insufficient"
DIRECTION_UNDETERMINED = "direction_undetermined"
NO_ADJUSTMENT = "no_adjustment"
THRESHOLD_NOT_ESTABLISHED = "threshold_not_established"
AWAITING_AMOUNT = "awaiting_amount"
AMOUNT_CONFIRMED = "amount_confirmed"

STATUS_LABELS = {
    COVERAGE_INSUFFICIENT: "覆盖不足",
    DIRECTION_UNDETERMINED: "方向未定",
    NO_ADJUSTMENT: "未达门槛，不构成调价",
    THRESHOLD_NOT_ESTABLISHED: "门槛未确认",
    AWAITING_AMOUNT: "方向已定，待确认金额",
    AMOUNT_CONFIRMED: "金额已确定",
}

THRESHOLD_LABELS = {
    "below": "整个范围未超过门槛",
    "exceeds": "整个范围超过门槛",
    "straddles": "范围跨在门槛上",
    "unknown": "范围无法确定",
}


@dataclass
class ItemResult:
    sku: str
    region: str
    weight: float
    level: Level
    change: Range | None
    contract_bound: Range | None = None
    reason: str = ""

    @property
    def comparable(self) -> bool:
        return self.change is not None

    def describe(self) -> dict[str, Any]:
        row = {
            "sku": self.sku,
            "region": self.region,
            "weight": self.weight,
            "comparability": self.level.kind,
            "comparability_label": self.level.describe()["comparability_label"],
        }
        if self.change is not None:
            row["change_percent"] = self.change.as_percent()
            row["change"] = [round(self.change.low, 6), round(self.change.high, 6)]
        if self.level.reason:
            row["reason"] = self.level.reason
        if self.contract_bound is not None:
            row["contract_uncovered_range"] = [
                round(self.contract_bound.low, 6),
                round(self.contract_bound.high, 6),
            ]
        if self.level.base:
            row["base"] = self.level.base.note()
        if self.level.current:
            row["current"] = self.level.current.note()
        if self.level.generations:
            row["generations"] = self.level.describe().get("generations")
        return row


@dataclass
class BasketResult:
    contract_id: str
    period: str
    service: str
    principal: float
    coverage_min: float
    threshold: float
    status: str
    items: list[ItemResult]
    covered_weight: float = 0.0
    covered_change: Range | None = None
    total_change: Range | None = None
    threshold_state: str = "unknown"
    amount: float | None = None
    rate: float | None = None
    rate_agreement: str | None = None
    notes: list[str] = field(default_factory=list)
    used_fees: dict[str, float] = field(default_factory=dict)

    @property
    def coverage_met(self) -> bool:
        return self.covered_weight >= self.coverage_min - 1e-12

    @property
    def direction(self) -> str:
        return self.total_change.direction if self.total_change else "unknown"

    def describe(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "contract": self.contract_id,
            "period": self.period,
            "service": self.service,
            "coverage": round(self.covered_weight, 6),
            "coverage_min": self.coverage_min,
            "coverage_met": self.coverage_met,
            "status": self.status,
            "status_label": STATUS_LABELS[self.status],
            "threshold": self.threshold,
            "threshold_state": self.threshold_state,
            "threshold_label": THRESHOLD_LABELS[self.threshold_state],
            "principal": self.principal,
            "items": [item.describe() for item in self.items],
        }
        if self.covered_change is not None:
            row["covered_change_percent"] = self.covered_change.as_percent()
        if self.total_change is not None:
            row["total_change_percent"] = self.total_change.as_percent()
        if self.amount is not None:
            row["amount"] = round(self.amount, 2)
        if self.rate is not None:
            row["rate"] = self.rate
            row["rate_agreement"] = self.rate_agreement
        if self.notes:
            row["notes"] = list(self.notes)
        if self.used_fees:
            row["fee_values"] = dict(sorted(self.used_fees.items()))
        return row


def _item_bounds(item: dict[str, Any]) -> Range | None:
    raw = item.get("uncovered_change_range")
    if raw is None:
        return None
    low, high = raw
    return Range(float(low), float(high))


def evaluate_contract(
    inputs: Inputs,
    contract_id: str,
    *,
    period: str | None = None,
    as_of: date | None = None,
) -> BasketResult:
    """按合同指定的期间、地区和服务口径核算一次调价。"""
    result = _evaluate_contract(inputs, contract_id, period=period, as_of=as_of)
    _collect_fee_check(result)
    return result


def _evaluate_contract(
    inputs: Inputs,
    contract_id: str,
    *,
    period: str | None = None,
    as_of: date | None = None,
) -> BasketResult:
    contract = inputs.contracts.get(contract_id)
    if contract is None:
        raise KeyError(f"未知合同 {contract_id}")
    on = as_of or date.max
    period = period or contract.get("current_week") or contract.get("period")
    service = contract["service"]
    base_week = contract.get("base_week")
    if base_week is None:
        raise ValueError(f"合同 {contract_id} 没有基准周")
    if period is None:
        raise ValueError(f"合同 {contract_id} 没有结算期间")

    items: list[ItemResult] = []
    levels: dict[str, Level] = {}
    for entry in contract["basket"]:
        key = f"{entry['sku']}@{entry['region']}"
        level = evaluate_item(
            inputs,
            sku=entry["sku"],
            region=entry["region"],
            service=service,
            base_week=base_week,
            week=period,
            as_of=on.isoformat(),
            label=f"{contract_id}:{key}",
        )
        levels[key] = level
        change = level.change
        bounds = _item_bounds(entry)
        items.append(
            ItemResult(
                sku=entry["sku"],
                region=entry["region"],
                weight=float(entry["weight"]),
                level=level,
                change=change,
                contract_bound=bounds,
                reason=level.reason,
            )
        )

    # 多个旧款接同一新品时，新品不能分别充当几份独立证据。保留一条（按权重
    # 最大的位置），其余项标为“同一批新机证据已被占用”，不得用于补齐覆盖。
    shared = shared_evidence_groups(levels)
    shared_groups: dict[str, list[str]] = {}
    for key, observation_id in shared.items():
        shared_groups.setdefault(observation_id, []).append(key)
    for observation_id, keys in shared_groups.items():
        keeper = max(
            (item for item in items if f"{item.sku}@{item.region}" in keys),
            key=lambda item: item.weight,
        )
        for item in items:
            key = f"{item.sku}@{item.region}"
            if key not in keys or item is keeper:
                continue
            item.change = None
            item.level.kind = NO_EVIDENCE
            item.reason = (
                f"{observation_id} 这同一批新机成交已被 {keeper.sku}@{keeper.region} "
                "用作证据；不能为了补齐覆盖再算一份独立证据"
            )
            item.level.reason = item.reason

    result = BasketResult(
        contract_id=contract_id,
        period=period,
        service=service,
        principal=float(contract["principal"]),
        coverage_min=float(contract["coverage_min"]),
        threshold=float(contract["threshold"]),
        status=DIRECTION_UNDETERMINED,
        items=items,
    )
    result.covered_weight = sum(item.weight for item in items if item.comparable)
    result.covered_change = _weighted([item for item in items if item.comparable])

    # 未覆盖部分：有合同约定上下界就进入总范围，否则方向不能确认。
    uncovered = [item for item in items if not item.comparable]
    unresolved = [item for item in uncovered if item.contract_bound is None]
    total = result.covered_change or Range(0.0, 0.0)
    if result.covered_weight + 1e-12 < result.coverage_min:
        result.status = COVERAGE_INSUFFICIENT
        result.notes.append(
            f"可比权重 {result.covered_weight:.2%} 低于合同覆盖线 {result.coverage_min:.2%}；"
            "缺价权重不得转给有货地区"
        )
    if unresolved:
        result.notes.append(
            "缺价部分没有合同约定的涨跌上下界："
            + "、".join(f"{item.sku}@{item.region}" for item in unresolved)
            + "；不能按零涨跌补齐"
        )
        if result.covered_weight + 1e-12 >= result.coverage_min:
            result.status = DIRECTION_UNDETERMINED
    elif uncovered:
        result.notes.append(
            "缺价部分按合同约定区间进入总范围："
            + "、".join(f"{item.sku}@{item.region}" for item in uncovered)
        )
        for item in uncovered:
            assert item.contract_bound is not None
            total = total + item.contract_bound * item.weight
    # 总范围仍按完整篮子记录，即使覆盖不足；覆盖不足本身已单独标出。
    result.total_change = total

    if result.status == COVERAGE_INSUFFICIENT:
        return result

    total = result.total_change

    if total.direction == "undetermined":
        result.status = DIRECTION_UNDETERMINED
        return result

    threshold = result.threshold
    if total.within(threshold):
        result.threshold_state = "below"
        result.status = NO_ADJUSTMENT
    elif total.beyond(threshold):
        result.threshold_state = "exceeds"
        result.status = _amount_status(inputs, contract, period, total, on, result)
    else:
        result.threshold_state = "straddles"
        result.status = THRESHOLD_NOT_ESTABLISHED
        result.notes.append(
            f"变化范围 {total} 跨在门槛 {threshold:.2%} 上，不能确认是否构成调价"
        )
    return result


def _weighted(items: list[ItemResult]) -> Range | None:
    total = Range(0.0, 0.0)
    for item in items:
        assert item.change is not None
        total = total + item.change * item.weight
    return total


def _amount_status(
    inputs: Inputs,
    contract: dict[str, Any],
    period: str,
    change: Range,
    on: date,
    result: BasketResult,
) -> str:
    """方向已定后判断可结金额：点值可直接算，否则必须有有效结算比例。"""
    agreements = [
        row
        for row in inputs.rate_agreements.values()
        if row["contract"] == contract["id"]
        and row["period"] == period
        and row.get("signed_on", "9999-12-31") <= on.isoformat()
        and row.get("buyer")
        and row.get("merchant")
    ]
    usable: list[dict[str, Any]] = []
    rejected: list[str] = []
    for agreement in agreements:
        if not _agreement_fees_in_force(inputs, agreement, on):
            rejected.append(f"{agreement['id']}（费用依据已失效）")
            continue
        if not change.contains(float(agreement["rate"])):
            rejected.append(f"{agreement['id']}（比例 {agreement['rate']:.4%} 超出支持范围）")
            continue
        usable.append(agreement)
    if not usable:
        result.status = AWAITING_AMOUNT
        result.notes.append(
            "没有商户与采购共同签认、对应当前费用依据且落在支持范围内的结算比例"
            + (f"；不可用：{'、'.join(rejected)}" if rejected else "")
        )
        return result.status
    rates = sorted({float(row["rate"]) for row in usable})
    if len(rates) > 1:
        result.status = AWAITING_AMOUNT
        result.notes.append(
            "存在多个不同的有效结算比例 "
            + "、".join(f"{value:.4%}" for value in rates)
            + "；不能挑选对己方有利的一个"
        )
        return result.status
    rate = rates[0]
    result.rate = rate
    result.rate_agreement = usable[-1]["id"]
    result.amount = round(result.principal * rate, 2)
    result.status = AMOUNT_CONFIRMED
    return result.status


def _agreement_fees_in_force(inputs: Inputs, agreement: dict[str, Any], on: date) -> bool:
    for statement_id in agreement.get("fee_sources", []):
        if fee_value(inputs, statement_id, on) is None:
            return False
    return True


def _collect_fee_check(result: BasketResult) -> None:
    """同一费用凭据在多个篮子项出现时，必须取同一情形下的同一取值。"""
    seen: dict[str, float] = {}
    for item in result.items:
        for quote in (item.level.base, item.level.current):
            if quote is None or ":" not in quote.fee_source:
                continue
            statement_id = quote.fee_source.split(":", 1)[1]
            if statement_id in seen and abs(seen[statement_id] - quote.fee) > 1e-9:
                raise AssertionError(
                    f"费用凭据 {statement_id} 在同一篮子中取到不一致的取值"
                )
            seen[statement_id] = quote.fee
    result.used_fees = seen
