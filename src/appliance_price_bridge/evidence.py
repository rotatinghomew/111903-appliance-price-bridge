"""价格观察与安装费的可用性判定。

四种状态严格区分：

* ``traded``    —— 有成交，可按销量加权；
* ``offered``   —— 没有成交但还能报价，只能作为可核验报价，不产生销量权重；
* ``withdrawn`` —— 已退出，不能进入当期；
* ``not_listed``—— 当地还没铺货，是缺价，不是零涨跌。

税费、赠品和服务口径无法对齐的观察不进入可比部分。新款售价若含安装服务，必须
先按该观察的安装费说明书（没有说明书时按报价自带的拆分）还原为硬件可比价，否
则换代接续会把服务费误当成涨价。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .inputs import Inputs, InputError
from .ranges import Range

TRADED = "traded"
OFFERED = "offered"
WITHDRAWN = "withdrawn"
NOT_LISTED = "not_listed"
STATUSES = (TRADED, OFFERED, WITHDRAWN, NOT_LISTED)

STATUS_LABELS = {
    TRADED: "有成交",
    OFFERED: "无成交可报价",
    WITHDRAWN: "已退出",
    NOT_LISTED: "当地未铺货",
}


@dataclass(frozen=True)
class Quote:
    """一个通过可比性检查的观察。``hardware_price`` 已扣除安装服务。"""

    observation_id: str
    sku: str
    region: str
    week: str
    status: str
    price: float
    sales: int
    fee: float
    fee_source: str  # "statement:F5" / "quote" / "none"
    fee_disputed: bool
    service: str

    @property
    def traded(self) -> bool:
        return self.status == TRADED and self.sales > 0

    @property
    def hardware_price(self) -> float:
        return self.price - self.fee

    @property
    def total_price(self) -> float:
        return self.price

    def note(self) -> str:
        label = STATUS_LABELS.get(self.status, self.status)
        fee = "无安装费" if self.fee == 0 else f"安装费 {self.fee:g}（{self.fee_source}）"
        return f"{self.observation_id} {self.sku} {self.region} {self.week} {label} 价 {self.price:g}，{fee}"


class NotComparable(ValueError):
    """观察不能进入可比部分。"""


def _effective_fee(
    inputs: Inputs,
    observation_id: str,
    inline: float | None,
    on: date,
    pins: dict[str, str] | None,
) -> tuple[float, str, bool]:
    """返回（安装费、依据、是否处于版本争议）。

    ``pins`` 是发行批次或合同口径钉死的费用版本；有钉死值时不再按当日有效版本
    推断，这样重发批次和保留原版的合同各用各的口径。
    """
    if pins is not None and observation_id in pins:
        statement_id = pins[observation_id]
        statement = inputs.fee_statements.get(statement_id)
        if statement is None or statement.get("observation") != observation_id:
            raise InputError(f"钉死的费用凭据 {statement_id} 与观察 {observation_id} 不符")
        return float(statement["fee"]), f"statement:{statement_id}", False

    candidates = [
        row
        for row in inputs.fee_statements.values()
        if row.get("observation") == observation_id and row["received_on"] <= on.isoformat()
    ]
    if candidates:
        roots = [row for row in candidates if not row.get("replaces")]
        start = roots[0]["id"] if roots else candidates[0]["id"]
        versions = inputs.fee_versions_in_force(start, on)
        if len(versions) != 1:
            return (inline if inline is not None else 0.0), f"争议版本:{','.join(versions)}", True
        chosen = inputs.fee_statements[versions[0]]
        if chosen.get("observation") == observation_id:
            return float(chosen["fee"]), f"statement:{versions[0]}", False
    if inline is not None:
        return float(inline), "quote", False
    return 0.0, "none", False


def quote_for(
    inputs: Inputs,
    observation_id: str,
    *,
    service: str,
    as_of: date | None = None,
    pins: dict[str, str] | None = None,
    allowed: set[str] | None = None,
) -> Quote:
    """把观察整理成可比较报价；不可比时抛出 :class:`NotComparable`。

    ``allowed`` 限定该口径可用的观察编号（如某期刊次所列的原始依据）。
    """
    row = inputs.observations.get(observation_id)
    if row is None:
        raise NotComparable(f"未知观察 {observation_id}")
    if allowed is not None and observation_id not in allowed:
        raise NotComparable(f"观察 {observation_id} 不在该期次的原始依据内")
    status = row.get("status")
    if status not in STATUSES:
        raise NotComparable(f"{observation_id} 的状态 {status!r} 未定义")
    if status in (WITHDRAWN, NOT_LISTED):
        raise NotComparable(f"{observation_id} {STATUS_LABELS[status]}，不构成价格证据")
    if row.get("price") is None:
        raise NotComparable(f"{observation_id} 没有价格")
    if status == TRADED and not row.get("sales"):
        raise NotComparable(f"{observation_id} 标为成交但没有销量")
    for key, label in (
        ("tax_included", "税费"),
        ("gift_neutral", "赠品"),
        ("comparable", "可比性"),
    ):
        if row.get(key) is False:
            raise NotComparable(f"{observation_id} 的{label}口径无法对齐")

    product = inputs.products[row["sku"]]
    declared_service = product.get("service")
    fee, source, disputed = _effective_fee(
        inputs, observation_id, row.get("installation_fee"), as_of or date.max, pins
    )
    if declared_service and declared_service != service:
        # 服务口径不同：只有能把安装服务分出来，才还原成硬件可比价。
        if source == "none":
            raise NotComparable(
                f"{observation_id} 的服务口径为 {declared_service}，但没有安装费凭据可还原"
            )
    if disputed:
        raise NotComparable(
            f"{observation_id} 的安装费存在未裁定版本（{source.split(':', 1)[1]}），口径未定"
        )
    return Quote(
        observation_id=observation_id,
        sku=row["sku"],
        region=row["region"],
        week=row["week"],
        status=status,
        price=float(row["price"]),
        sales=int(row.get("sales") or 0),
        fee=fee,
        fee_source=source,
        fee_disputed=False,
        service=declared_service or service,
    )


def fee_value(inputs: Inputs, statement_id: str, on: date) -> Range | None:
    """一份说明书在 ``on`` 当日代表的费用；版本有争议时返回 ``None``。"""
    if statement_id not in inputs.fee_statements:
        return None
    versions = inputs.fee_versions_in_force(statement_id, on)
    if len(versions) != 1:
        return None
    return Range.point(float(inputs.fee_statements[versions[0]]["fee"]))


def fee_candidates(inputs: Inputs, statement_id: str, on: date) -> tuple[float, ...]:
    """一份说明书在争议中可能成立的全部费用版本。"""
    if statement_id not in inputs.fee_statements:
        return ()
    versions = inputs.fee_versions_in_force(statement_id, on)
    return tuple(float(inputs.fee_statements[version]["fee"]) for version in versions)


def version_pin(inputs: Inputs, observation_id: str, on: date) -> dict[str, str]:
    """把当日有效版本钉成一次性 ``pins`` 形参。"""
    candidates = [
        row
        for row in inputs.fee_statements.values()
        if row.get("observation") == observation_id and row["received_on"] <= on.isoformat()
    ]
    if not candidates:
        return {}
    roots = [row for row in candidates if not row.get("replaces")]
    start = roots[0]["id"] if roots else candidates[0]["id"]
    versions = inputs.fee_versions_in_force(start, on)
    if len(versions) != 1:
        return {}
    return {observation_id: versions[0]}


def fee_disputes(inputs: Inputs, on: date) -> list[dict[str, Any]]:
    """当日仍未裁定的费用版本分叉。"""
    out: list[dict[str, Any]] = []
    for statement in inputs.fee_statements.values():
        versions = inputs.fee_versions_in_force(statement["id"], on)
        if len(versions) > 1:
            out.append(
                {
                    "observation": statement.get("observation"),
                    "versions": list(versions),
                    "values": [inputs.fee_statements[v]["fee"] for v in versions],
                    "status": "awaiting_fee_resolution",
                }
            )
    # 同一分叉会被多条链各报一次，按观察去重。
    unique: dict[str | None, dict[str, Any]] = {}
    for row in out:
        unique.setdefault(row["observation"], row)
    return list(unique.values())
