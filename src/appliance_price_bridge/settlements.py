"""结算与付款：已签通知、订正复核、付款窗口、争议与累计差额。

边界：

* 历史通知及其依据保持可查；订正不回头改写已签通知本身，只改变“还应补多少”。
* 当前复核按同合同、同调整期累计已签金额，只签发尚未通知的差额。
* 已送银行的清单不改写；未送银行的通知才可归到当前窗口。已送银行后新增的差额
  进入后续开放窗口，并保留原调整期，不当作后续期间的新调价。
* 商户对已签正差额的争议只暂停该通知尚未送银行的款项；不撤销通知，也不重开
  银行批次。争议未解除时，不能用一笔反向新通知让同合同同期间未送银行的净额被
  提前付出或收回。
* 重复订正、重复回执或重新生成清单，不得重复签发、送款或跨期抵扣。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .inputs import Inputs

OPEN = "open"
SENT = "sent"
AWAITING_RESOLUTION = "awaiting_fee_resolution"
AWAITING_AMOUNT = "awaiting_amount"
AMOUNT_CONFIRMED = "amount_confirmed"
IN_DISPUTE = "in_dispute"
RELEASED = "released"

STATUS_LABELS = {
    OPEN: "未进付款窗口",
    SENT: "已送银行",
    AWAITING_RESOLUTION: "等待费用争议",
    AWAITING_AMOUNT: "等待金额签认",
    AMOUNT_CONFIRMED: "差额已确定",
    IN_DISPUTE: "争议中，暂停付款",
    RELEASED: "争议已解除",
}


def fee_chain(inputs: Inputs, statement_id: str) -> list[str]:
    """一份凭据及其全部下游订正（不含旁支以外的兄弟）。"""
    out: list[str] = []
    pending = [statement_id]
    while pending:
        current = pending.pop()
        for row in inputs.fee_statements.values():
            if row.get("replaces") == current and row["id"] not in out:
                out.append(row["id"])
                pending.append(row["id"])
    return sorted(out, key=lambda ident: inputs.fee_statements[ident]["received_on"])


def settlement_fee_status(inputs: Inputs, notice: dict[str, Any]) -> dict[str, Any]:
    """通知引用的费用凭据是否已被订正，以及订正是否已共同裁定。

    订正只管它明确替代的那一份旧凭据。判断“当时是否悬而未决”用订正收到当日
    的状态：两个同源版本都还在等裁定时，受影响合同上的通知进入等待费用争议。
    """
    statements = [source for source in notice.get("sources", []) if source in inputs.fee_statements]
    if not statements:
        return {"superseded": False, "awaiting_resolution": False, "versions": []}
    superseded: list[str] = []
    versions: list[str] = []
    for statement_id in statements:
        downstream = fee_chain(inputs, statement_id)
        if downstream:
            superseded.append(statement_id)
            versions.extend(downstream)
    awaiting = False
    if versions:
        latest = max(
            (inputs.fee_statements[ident]["received_on"], ident) for ident in versions
        )[1]
        on = date.fromisoformat(inputs.fee_statements[latest]["received_on"])
        for statement_id in statements:
            if len(inputs.fee_versions_in_force(statement_id, on)) != 1:
                awaiting = True
    return {
        "superseded": bool(superseded),
        "awaiting_resolution": awaiting,
        "versions": sorted(set(versions)),
        "superseded_sources": sorted(set(superseded)),
    }


def notice_states(inputs: Inputs) -> dict[str, dict[str, Any]]:
    """每份通知的当前状态、所属批次与争议。"""
    sent: dict[str, str] = {}
    for batch in inputs.bank_batches.values():
        for notice_id in batch.get("notices", []):
            sent.setdefault(notice_id, batch["id"])
    disputes: dict[str, dict[str, Any]] = {}
    for row in inputs.notice_disputes:
        disputes[row["notice"]] = row
    out: dict[str, dict[str, Any]] = {}
    for notice_id, notice in inputs.settlements.items():
        sent_batch = sent.get(notice_id)
        dispute = disputes.get(notice_id)
        fee_status = settlement_fee_status(inputs, notice)
        if sent_batch is not None:
            # 已送银行的清单固定：之后出现的费用争议或回执都不改写它。
            status = SENT
        elif dispute and not dispute.get("release_signed_on"):
            status = IN_DISPUTE
        elif fee_status["awaiting_resolution"]:
            status = AWAITING_RESOLUTION
        elif dispute is not None:
            status = RELEASED
        else:
            status = OPEN
        out[notice_id] = {
            "notice": notice,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "batch": sent_batch,
            "dispute": dispute,
            "fee": fee_status,
        }
    return out


@dataclass
class DifferenceResult:
    contract: str
    period: str
    signed_total: float
    sent_total: float
    unsent_total: float
    disputed_unsent: float
    signed_net_unsent: float
    new_amount: float | None
    status: str
    recomputed_notice: dict[str, Any] | None = None
    notices: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        row = {
            "contract": self.contract,
            "period": self.period,
            "signed_total": round(self.signed_total, 2),
            "sent_total": round(self.sent_total, 2),
            "unsent_total": round(self.unsent_total, 2),
            "disputed_unsent": round(self.disputed_unsent, 2),
            "signed_net_unsent": round(self.signed_net_unsent, 2),
            "new_amount": None if self.new_amount is None else round(self.new_amount, 2),
            "status": self.status,
            "status_label": STATUS_LABELS[self.status],
            "notices": self.notices,
            "notes": list(self.notes),
        }
        if self.recomputed_notice is not None:
            row["recomputed_notice"] = self.recomputed_notice
        return row


def difference_for(
    inputs: Inputs,
    contract_id: str,
    period: str,
    *,
    recomputed_amount: float | None = None,
    as_of: str | None = None,
) -> DifferenceResult:
    """同合同、同调整期的累计差额复核。

    ``recomputed_amount`` 是订正后应签发的累计金额（含此前已签部分）。给出时，
    只把尚未通知的差额作为可签金额；已送银行的无论如何不再改动，未送银行但已
    通知的也不重复签发。未给出时只汇报台账，不推断一个“应发”数字。
    """
    states = notice_states(inputs)
    rows = [
        state
        for state in states.values()
        if state["notice"]["contract"] == contract_id and state["notice"]["period"] == period
    ]
    signed_total = sum(float(state["notice"]["amount"]) for state in rows)
    sent_total = sum(float(state["notice"]["amount"]) for state in rows if state["status"] == SENT)
    unsent = [state for state in rows if state["status"] != SENT]
    unsent_total = sum(float(state["notice"]["amount"]) for state in unsent)
    disputed_unsent = sum(
        float(state["notice"]["amount"])
        for state in unsent
        if state["status"] in (IN_DISPUTE, AWAITING_RESOLUTION, AWAITING_AMOUNT)
    )
    notes: list[str] = []
    awaiting_fee = any(state["fee"]["awaiting_resolution"] for state in rows)
    if any(state["status"] == AWAITING_RESOLUTION for state in rows) or awaiting_fee:
        notes.append(
            "费用版本尚未共同裁定：受影响合同的相关通知暂停，"
            "不得以反向新通知提前结清未送银行净额"
        )
    if any(state["status"] == SENT for state in rows):
        notes.append("已送银行的通知所属清单固定，本期任何更正都不改写；新增差额进入后续开放窗口")
    if any(state["status"] in (IN_DISPUTE, AWAITING_AMOUNT) for state in unsent):
        notes.append("尚有未送银行的争议或待签认通知；不得用一笔反向通知提前付出或收回净额")

    status = AMOUNT_CONFIRMED
    if awaiting_fee or any(state["status"] == AWAITING_RESOLUTION for state in rows):
        status = AWAITING_RESOLUTION
    elif any(state["status"] in (AWAITING_AMOUNT, IN_DISPUTE) for state in unsent):
        status = AWAITING_AMOUNT

    result = DifferenceResult(
        contract=contract_id,
        period=period,
        signed_total=signed_total,
        sent_total=sent_total,
        unsent_total=unsent_total,
        disputed_unsent=disputed_unsent,
        signed_net_unsent=sum(
            float(state["notice"]["amount"])
            for state in rows
            if state["status"] == OPEN
        ),
        new_amount=None,
        status=status,
        notes=notes,
        notices=[
            {
                "notice": state["notice"]["id"],
                "issue_date": state["notice"]["issue_date"],
                "amount": state["notice"]["amount"],
                "kind": state["notice"]["kind"],
                "status": state["status"],
                "status_label": state["status_label"],
                "batch": state["batch"],
                "sources": state["notice"]["sources"],
            }
            for state in sorted(
                rows, key=lambda row: (row["notice"]["issue_date"], row["notice"]["id"])
            )
        ],
    )
    if recomputed_amount is not None:
        result.new_amount = round(float(recomputed_amount) - signed_total, 2)
        result.recomputed_notice = {
            "recomputed_total": round(float(recomputed_amount), 2),
            "already_signed": round(signed_total, 2),
            "basis": "订正只替代所指凭据；累计已签金额包括已送银行与未送银行的通知",
        }
        if result.new_amount == 0:
            result.notes.append("订正后的累计金额与此前已签一致，无需再签通知")
    return result


@dataclass
class WindowResult:
    window: str
    from_date: str
    through: str
    payable: list[dict[str, Any]]
    held: list[dict[str, Any]]
    deferred: list[dict[str, Any]]

    def describe(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "from": self.from_date,
            "through": self.through,
            "payable": self.payable,
            "held": self.held,
            "deferred": self.deferred,
            "payable_total": round(sum(row["amount"] for row in self.payable), 2),
        }


def payment_window(inputs: Inputs, window_id: str) -> WindowResult:
    """一个开放窗口能付哪些通知。

    已送银行的通知不在本窗口重付；争议中的通知暂停；其余未送银行的按金额入窗。
    绝不跨期间抵扣另一调整期的差额。
    """
    window = inputs.payment_windows.get(window_id)
    if window is None:
        raise KeyError(f"未知付款窗口 {window_id}")
    states = notice_states(inputs)
    payable: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for state in states.values():
        notice = state["notice"]
        row = {
            "notice": notice["id"],
            "contract": notice["contract"],
            "period": notice["period"],
            "amount": notice["amount"],
            "kind": notice["kind"],
            "status": state["status"],
        }
        if state["status"] == SENT:
            deferred.append({**row, "reason": f"已在批次 {state['batch']} 送出，清单固定"})
        elif state["status"] == AWAITING_RESOLUTION:
            held.append({**row, "reason": "等待费用版本共同裁定"})
        elif state["status"] in (IN_DISPUTE, AWAITING_AMOUNT):
            held.append({**row, "reason": "争议未解除或金额未签认"})
        else:
            payable.append(row)
    return WindowResult(
        window=window_id,
        from_date=window["from"],
        through=window["through"],
        payable=payable,
        held=held,
        deferred=deferred,
    )


def receipt_summary(inputs: Inputs) -> dict[str, dict[str, Any]]:
    """按批次汇总银行回执；重复送达只算一次。"""
    out: dict[str, dict[str, Any]] = {}
    for receipt in inputs.bank_receipts:
        batch_id = receipt["batch"]
        entry = out.setdefault(
            batch_id,
            {
                "batch": batch_id,
                "receipts": set(),
                "paid_on": receipt["paid_on"],
                "received_dates": set(),
                "duplicate_deliveries": 0,
            },
        )
        entry["receipts"].add(receipt["receipt"])
        entry["received_dates"].add(receipt["received_on"])
        entry["duplicate_deliveries"] = max(
            entry["duplicate_deliveries"],
            len(entry["received_dates"]) - 1,
        )
    for entry in out.values():
        entry["receipts"] = sorted(entry["receipts"])
        entry["received_dates"] = sorted(entry["received_dates"])
    return out


def notice_trace(inputs: Inputs, notice_id: str) -> dict[str, Any]:
    """商户从一笔通知追溯到当周价格、服务依据与所属调整期。"""
    notice = inputs.settlements.get(notice_id)
    if notice is None:
        raise KeyError(f"未知通知 {notice_id}")
    statements = {
        source: inputs.fee_statements[source]
        for source in notice.get("sources", [])
        if source in inputs.fee_statements
    }
    observations = {
        source: inputs.observations[source]
        for source in notice.get("sources", [])
        if source in inputs.observations
    }
    agreement = inputs.rate_agreements.get(notice.get("agreement", ""))
    state = notice_states(inputs)[notice_id]
    return {
        "notice": notice_id,
        "contract": notice["contract"],
        "period": notice["period"],
        "kind": notice["kind"],
        "amount": notice["amount"],
        "issue_date": notice["issue_date"],
        "agreement": None
        if agreement is None
        else {
            "id": agreement["id"],
            "rate": agreement["rate"],
            "signed_on": agreement["signed_on"],
            "fee_sources": agreement["fee_sources"],
        },
        "observations": [
            {
                "id": ident,
                "sku": row["sku"],
                "region": row["region"],
                "week": row["week"],
                "status": row["status"],
                "price": row["price"],
                "installation_fee": row.get("installation_fee"),
            }
            for ident, row in sorted(observations.items())
        ],
        "fee_statements": [
            {
                "id": ident,
                "fee": row["fee"],
                "received_on": row["received_on"],
                "replaces": row.get("replaces"),
            }
            for ident, row in sorted(statements.items())
        ],
        "status": state["status"],
        "status_label": state["status_label"],
        "batch": state["batch"],
        "fee": state["fee"],
    }


def worklist(inputs: Inputs) -> dict[str, list[dict[str, Any]]]:
    """采购、财务各自要处理的队列。"""
    states = notice_states(inputs)
    buy: list[dict[str, Any]] = []
    finance: list[dict[str, Any]] = []
    for notice_id, state in states.items():
        notice = state["notice"]
        row = {
            "notice": notice_id,
            "contract": notice["contract"],
            "period": notice["period"],
            "amount": notice["amount"],
            "status": state["status"],
            "status_label": state["status_label"],
        }
        if state["status"] == AWAITING_RESOLUTION:
            buy.append({**row, "action": "先完成费用版本共同裁定"})
        elif state["status"] in (IN_DISPUTE, AWAITING_AMOUNT):
            buy.append({**row, "action": "确认结算金额或解除争议"})
        elif state["status"] != SENT:
            finance.append({**row, "action": "纳入开放付款窗口"})
        else:
            finance.append({**row, "action": f"已在批次 {state['batch']} 送出"})
    return {"procurement": buy, "finance": finance}


def direction_note(index_change: "Range | None", contract_change: "Range | None") -> str:
    """解释同一轮换代为何推高成交均价却不一定触发某份合同调价。"""
    parts: list[str] = []
    if index_change is not None:
        parts.append(f"指数（固定篮子、同款优先并逐代接质量档）变化 {index_change}")
    if contract_change is not None:
        parts.append(f"该合同篮子可比部分变化 {contract_change}")
    parts.append("两者口径不同：指数用固定篮子与质量调整，合同篮子按自己的权重、覆盖线与门槛判断")
    return "；".join(parts)
