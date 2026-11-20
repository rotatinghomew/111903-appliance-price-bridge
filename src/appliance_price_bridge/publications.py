"""指数刊期台账：已发布冻结、修订影响范围、重发批次与合同基准。

刊期规则：

* 已发布的原版始终可查。后来的费用订正只用于重发版，不改写原版。
* 一处修正会影响哪些后续期次，要看这些期次是否沿用受影响的接续；重发范围列出
  来，不能只重发被直接点到的那一个月。
* 即使某个后续月没有新的价格订正，只要它的上月接续依据变了，也必须与上月一起
  用同一套可比依据重发，不能用新上月和旧本月混算。
* 同一批次里基础月与原版、当月与上月的口径必须一致；接不上的月份要明确止于哪
  一月。
* 合同自己约定保留原版（``keep_original``）还是跟随修订（``follow_reissues``）。
  跟随修订也必须拿到同一批次的一整对可比指数，不能跨批次、跨口径拼出一个方向。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .index_eval import Level, edition_chain, edition_pins, evaluate_edition
from .inputs import Inputs
from .ranges import Range

PUBLISHED = "published"
SUPERSEDED = "superseded"
REISSUE_DUE = "reissue_due"
BLOCKED = "blocked"


@dataclass
class EditionState:
    edition_id: str
    series: str
    month: str
    week: str
    batch: str
    published_on: str
    replaces: str | None
    superseded_by: str | None
    level: Level
    flagged_reason: str | None = None

    @property
    def is_latest(self) -> bool:
        return self.superseded_by is None

    def describe(self) -> dict[str, Any]:
        row = self.level.describe()
        row.update(
            {
                "edition": self.edition_id,
                "series": self.series,
                "month": self.month,
                "week": self.week,
                "batch": self.batch,
                "published_on": self.published_on,
                "replaces": self.replaces,
                "superseded_by": self.superseded_by,
                "state": "superseded" if self.superseded_by else "current_version",
            }
        )
        if self.flagged_reason:
            row["flagged_reason"] = self.flagged_reason
        return row


def edition_states(inputs: Inputs) -> dict[str, EditionState]:
    """每个版本的冻结评估与替代关系。"""
    superseded_by: dict[str, str] = {}
    for edition in inputs.editions.values():
        if edition.get("replaces"):
            superseded_by[edition["replaces"]] = edition["id"]
    out: dict[str, EditionState] = {}
    for edition_id, edition in inputs.editions.items():
        out[edition_id] = EditionState(
            edition_id=edition_id,
            series=edition["series"],
            month=edition["month"],
            week=edition["week"],
            batch=edition["batch"],
            published_on=edition["published_on"],
            replaces=edition.get("replaces"),
            superseded_by=superseded_by.get(edition_id),
            level=evaluate_edition(inputs, edition_id),
        )
    return out


def _statement_is_current(inputs: Inputs, statement_id: str, as_of: str) -> bool:
    versions = inputs.fee_versions_in_force(statement_id, date.fromisoformat(as_of))
    return versions == (statement_id,)


def _claim_is_current(inputs: Inputs, claim_id: str, as_of: str) -> bool:
    for correction in inputs.claim_corrections:
        if correction.get("claim") == claim_id and correction["received_on"] <= as_of:
            return False
    return True


def _link_is_current(inputs: Inputs, link_id: str, as_of: str) -> bool:
    link = inputs.confirmed_links.get(link_id)
    if link is None:
        return False
    return link["confirmed_on"] <= as_of


def edition_dependencies(inputs: Inputs, edition_id: str) -> dict[str, Any]:
    """一个版本实际用到的费用、质量和接续凭据。"""
    level = evaluate_edition(inputs, edition_id)
    return {
        "fees": sorted(level.used_fees),
        "claims": sorted(level.used_claims),
        "links": sorted(level.used_links),
        "comparable": level.comparable,
    }


def direct_impact(inputs: Inputs, as_of: str | None = None) -> dict[str, list[str]]:
    """订正之后，哪些版本直接依赖了已被替代或已失效的凭据。"""
    on = as_of or _latest_date(inputs)
    reasons: dict[str, list[str]] = {}
    for edition_id in inputs.editions:
        edition = inputs.editions[edition_id]
        if edition["published_on"] > on:
            continue
        deps = edition_dependencies(inputs, edition_id)
        why: list[str] = []
        for statement_id in deps["fees"]:
            if not _statement_is_current(inputs, statement_id, on):
                why.append(f"费用凭据 {statement_id} 已被订正")
        for claim_id in deps["claims"]:
            if not _claim_is_current(inputs, claim_id, on):
                why.append(f"质量档 {claim_id} 已有订正")
        for link_id in deps["links"]:
            if not _link_is_current(inputs, link_id, on):
                why.append(f"接续 {link_id} 尚未确认")
        # 新确认的接续若与本版所用的接法互不相容，也要复核。
        for link in inputs.confirmed_links.values():
            if (
                link["series"] == edition["series"]
                and edition["published_on"] < link["confirmed_on"] <= on
                and link["id"] not in deps["links"]
                and _link_conflicts(inputs, edition_id, link)
            ):
                why.append(f"新增接续 {link['id']} 与本版接法无法同时成立")
        if why:
            reasons[edition_id] = why
    return reasons


def _link_conflicts(inputs: Inputs, edition_id: str, link: dict[str, Any]) -> bool:
    """新增接续是否给本期带来了另一条互不相容的路径。"""
    edition = inputs.editions[edition_id]
    scope = set(edition["links"]) | {link["id"]}
    from .index_eval import evaluate_series

    trial = evaluate_series(
        inputs,
        series_id=edition["series"],
        week=edition["week"],
        as_of=link["confirmed_on"],
        pins=edition_pins(inputs, edition),
        allowed=set(edition["observations"]),
        link_scope=scope,
    )
    return trial.kind == "no_evidence" or bool(trial.disputed)


def _latest_date(inputs: Inputs) -> str:
    """资料中名义上最晚的日期；周次按其起始日参与比较。

    这里只用来给“截至现在”一个默认值，具体复核仍按调用方传入的 ``as_of``。
    """
    dates: list[str] = []
    for row in inputs.observations.values():
        start = _week_start(row["week"])
        if start:
            dates.append(start)
    for row in inputs.editions.values():
        dates.append(row["published_on"])
    for row in inputs.fee_statements.values():
        dates.append(row["received_on"])
    for row in inputs.confirmed_links.values():
        dates.append(row["confirmed_on"])
    for row in inputs.claim_corrections:
        dates.append(row["received_on"])
    for row in inputs.fee_resolutions:
        dates.append(row["signed_on"])
    for row in inputs.settlements.values():
        dates.append(row["issue_date"])
    for row in inputs.bank_batches.values():
        dates.append(row["sent_on"])
    for row in inputs.payment_windows.values():
        dates.append(row["through"])
    for row in inputs.notice_disputes:
        dates.append(row["release_signed_on"])
    return max(dates)


def _week_start(week: str) -> str | None:
    """ISO 周次对应的周一日期（样例资料以 2026 年为基准）。"""
    head, _, tail = week.partition("-W")
    if not (head.isdigit() and tail.isdigit()):
        return None
    try:
        return date.fromisocalendar(int(head), int(tail), 1).isoformat()
    except ValueError:
        return None


def reissue_plan(inputs: Inputs, as_of: str | None = None) -> dict[str, Any]:
    """列出待重发范围，并把接不上的月份明确止住。"""
    on = as_of or _latest_date(inputs)
    states = edition_states(inputs)
    impact = direct_impact(inputs, on)
    series_order: dict[str, list[str]] = {}
    for edition in inputs.editions.values():
        series_order.setdefault(edition["series"], []).append(edition["id"])
    for series_id, ids in series_order.items():
        ids.sort(key=lambda ident: (inputs.editions[ident]["month"], inputs.editions[ident]["published_on"]))

    planned: dict[str, dict[str, Any]] = {}
    terminated: list[dict[str, Any]] = []
    stops = {
        series_id: _series_stop(inputs, series_id, on) for series_id in series_order
    }
    for edition_id, why in impact.items():
        edition = inputs.editions[edition_id]
        chain = edition_chain(inputs, edition_id)
        latest = inputs.editions[chain[-1]]
        if latest["published_on"] > on:
            continue
        stop = stops.get(edition["series"])
        # 直接受影响的月份，以及同一系列其后各月：后续月的上月基准变了。
        months = sorted(
            {
                inputs.editions[other]["month"]
                for other in series_order[edition["series"]]
                if inputs.editions[other]["published_on"] <= on
                and inputs.editions[other]["month"] >= edition["month"]
                and (stop is None or inputs.editions[other]["month"] <= stop["ends_at"])
            }
        )
        entry = planned.setdefault(
            edition["series"], {"series": edition["series"], "months": set(), "reasons": []}
        )
        entry["months"].update(months)
        entry["reasons"].extend(why)
    for stop in stops.values():
        if stop is not None:
            terminated.append(stop)

    pending: list[dict[str, Any]] = []
    for series_id, entry in planned.items():
        for month in sorted(entry["months"]):
            versions = [
                ident
                for ident in series_order[series_id]
                if inputs.editions[ident]["month"] == month
                and inputs.editions[ident]["published_on"] <= on
            ]
            if not versions:
                continue
            latest_version = max(versions, key=lambda ident: inputs.editions[ident]["published_on"])
            latest = inputs.editions[latest_version]
            child = [
                row["id"]
                for row in inputs.editions.values()
                if row.get("replaces") == latest_version and row["published_on"] <= on
            ]
            if child:
                continue
            stop = stops.get(series_id)
            row = {
                "series": series_id,
                "month": month,
                "based_on": latest_version,
                "week": latest["week"],
                "reasons": sorted(set(entry["reasons"])),
            }
            if stop is not None and month == stop["ends_at"]:
                # 重发范围到此为止；该月没有可比依据，不能签发。
                row.update(
                    {
                        "status": BLOCKED,
                        "blocked_reason": stop["reason"],
                    }
                )
            else:
                row["status"] = REISSUE_DUE
            pending.append(row)
    return {
        "as_of": on,
        "pending_reissue": pending,
        "terminated": terminated,
        "note": "重发必须整批使用同一套可比依据；不能用新上月与旧本月混算",
    }


def _series_stop(
    inputs: Inputs,
    series_id: str,
    as_of: str,
) -> dict[str, Any] | None:
    """系列从哪个版本起已无法按同一依据接续。

    重发只能覆盖到最后一个仍可比的版本；其后月份必须明确止住，不能凭旧依据
    继续外推。
    """
    versions = sorted(
        (
            (inputs.editions[ident]["month"], inputs.editions[ident]["published_on"], ident)
            for ident, state in edition_states(inputs).items()
            if inputs.editions[ident]["series"] == series_id
            and inputs.editions[ident]["published_on"] <= as_of
        ),
    )
    if not versions:
        return None
    seen: dict[str, str] = {}
    for month, _, ident in versions:
        seen[month] = ident
    states = edition_states(inputs)
    for month in sorted(seen):
        level = states[seen[month]].level
        if not level.comparable:
            return {
                "series": series_id,
                "ends_at": month,
                "reason": f"{seen[month]} 无可比结果，系列接续止于此月：{level.reason}",
            }
    return None


@dataclass
class ReferenceBasis:
    reference: dict[str, Any]
    policy: str
    base: EditionState | None
    current: EditionState | None
    batch: str | None
    status: str
    reason: str = ""
    change: Range | None = None
    stages: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        row = {
            "contract": self.reference["contract"],
            "period": self.reference["period"],
            "policy": self.policy,
            "status": self.status,
            "base_edition": self.base.edition_id if self.base else None,
            "current_edition": self.current.edition_id if self.current else None,
            "batch": self.batch,
        }
        if self.reason:
            row["reason"] = self.reason
        if self.change is not None:
            row["change_percent"] = self.change.as_percent()
            row["direction"] = self.change.direction
        if self.stages:
            row["stages"] = list(self.stages)
        return row


def reference_basis(inputs: Inputs, reference: dict[str, Any], as_of: str) -> ReferenceBasis:
    """按合同自己的约定选基准：保留原版，或跟随同批次重发。"""
    policy = reference["policy"]
    states = edition_states(inputs)
    original_base = states.get(reference["base_edition"])
    original_current = states.get(reference["current_edition"])
    if original_base is None or original_current is None:
        missing = [
            ident
            for ident in (reference["base_edition"], reference["current_edition"])
            if ident not in states
        ]
        return ReferenceBasis(
            reference=reference,
            policy=policy,
            base=original_base,
            current=original_current,
            batch=None,
            status="awaiting_edition",
            reason=f"约定的版本 {'、'.join(missing)} 在资料中不存在；不能改用其他月份顶替",
        )
    if policy == "keep_original":
        basis = ReferenceBasis(
            reference=reference,
            policy=policy,
            base=original_base,
            current=original_current,
            batch=None,
            status="ok",
            reason="合同约定保留已公布原版，原版始终可查",
        )
        _apply_change(basis)
        return basis
    if policy != "follow_reissues":
        raise ValueError(f"未知的基准修订约定 {policy!r}")

    base_lineage = edition_chain(inputs, reference["base_edition"])
    current_lineage = edition_chain(inputs, reference["current_edition"])
    by_batch: dict[str, dict[str, EditionState]] = {}
    for edition_id in base_lineage:
        state = states[edition_id]
        if state.published_on <= as_of:
            by_batch.setdefault(state.batch, {})["base"] = state
    for edition_id in current_lineage:
        state = states[edition_id]
        if state.published_on <= as_of:
            by_batch.setdefault(state.batch, {})["current"] = state
    complete = {
        batch: pair for batch, pair in by_batch.items() if pair.get("base") and pair.get("current")
    }
    if not complete:
        available = {
            batch: {
                "base": pair.get("base").edition_id if pair.get("base") else None,
                "current": pair.get("current").edition_id if pair.get("current") else None,
            }
            for batch, pair in by_batch.items()
        }
        basis = ReferenceBasis(
            reference=reference,
            policy=policy,
            base=original_base,
            current=original_current,
            batch=None,
            status="awaiting_full_pair",
            reason="尚未拿到同一批次的一整对可比指数；已签通知可查阅，但不能据此签出混合口径的差额",
            stages=[f"{batch}: {pair}" for batch, pair in sorted(available.items())],
        )
        return basis
    # 同一批次内两个版本都已发布，取最新批次。
    batch = max(
        complete,
        key=lambda name: max(
            complete[name]["base"].published_on, complete[name]["current"].published_on
        ),
    )
    pair = complete[batch]
    if pair["base"].batch != pair["current"].batch:  # pragma: no cover - 由上方结构保证
        raise AssertionError("基础与当期必须来自同一批次")
    basis = ReferenceBasis(
        reference=reference,
        policy=policy,
        base=pair["base"],
        current=pair["current"],
        batch=batch,
        status="ok",
        reason=f"使用同一批次 {batch} 的一整对可比指数",
    )
    _apply_change(basis)
    return basis


def _apply_change(basis: ReferenceBasis) -> None:
    if basis.base is None or basis.current is None:
        return
    base_level = basis.base.level.level
    current_level = basis.current.level.level
    if base_level is None or current_level is None:
        basis.status = "no_comparable"
        basis.reason = "基础版或当期版没有可比指数"
        return
    basis.change = Range(
        current_level.low / base_level.high - 1,
        current_level.high / base_level.low - 1,
    )


def contract_reference_evaluation(
    inputs: Inputs,
    reference: dict[str, Any],
    as_of: str,
) -> dict[str, Any]:
    """合同下一结算期的基准复核：方向、门槛，以及是否有可签金额的依据。"""
    contract = inputs.contracts[reference["contract"]]
    basis = reference_basis(inputs, reference, as_of)
    row = basis.describe()
    row["threshold"] = contract["threshold"]
    row["principal"] = contract["principal"]
    if basis.change is None:
        row["decision"] = "awaiting_basis"
        return row
    change = basis.change
    if change.within(contract["threshold"]):
        row["decision"] = "no_adjustment"
    elif change.beyond(contract["threshold"]):
        row["decision"] = "threshold_exceeded"
        agreements = [
            agreement
            for agreement in inputs.rate_agreements.values()
            if agreement["contract"] == contract["id"]
            and agreement["period"] == reference["period"]
            and agreement["signed_on"] <= as_of
        ]
        usable = [
            agreement
            for agreement in agreements
            if change.contains(float(agreement["rate"]))
            and all(
                _statement_is_current(inputs, statement_id, as_of)
                for statement_id in agreement.get("fee_sources", [])
            )
        ]
        if len({float(agreement["rate"]) for agreement in usable}) == 1:
            rate = float(usable[0]["rate"])
            row["decision"] = "amount_confirmed"
            row["rate"] = rate
            row["amount"] = round(float(contract["principal"]) * rate, 2)
        else:
            row["decision"] = "awaiting_amount"
            row["reason"] = (
                "方向已定，但没有商户与采购共同签认、对应当前依据且落在支持范围内的结算比例"
            )
    else:
        row["decision"] = "threshold_not_established"
        row["reason"] = "变化范围跨在门槛上，不能确认是否构成调价"
    return row
