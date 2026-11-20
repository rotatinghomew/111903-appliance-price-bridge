"""固定篮子指数：同款优先、逐代接续、共同支持范围。

一个位置在某一期只有三种结局：

* **直接可比**：同地区、同服务口径下当期有同款成交；没有成交才用可核验的同款
  报价。基准期与当期是同一货号。
* **依赖质量调整**：同款结果不存在，走合同约定的正式换代。每一代都用同一份硬
  件价差档案接上；质量档是范围，结论也是范围。
* **无证据接上**：换代缺重叠周又无双方签字、只有同档替代、当地未铺货、服务口
  径无法还原、费用版本未裁定、多个经确认接法互不相容等。

“无证据”不是零涨跌。销量加权只用于销售视角，不进入固定篮子。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from .bridges import QualityClaim, quality_claim, replacement_targets, substitute_target
from .evidence import NotComparable, Quote, quote_for
from .inputs import Inputs
from .ranges import Range

DIRECT = "direct"
QUALITY = "quality_adjusted"
NO_EVIDENCE = "no_evidence"

KIND_LABELS = {
    DIRECT: "直接可比",
    QUALITY: "依赖质量调整",
    NO_EVIDENCE: "无证据接上",
}


@dataclass
class Generation:
    old_sku: str
    new_sku: str
    claim_id: str
    quality: Range
    link_id: str | None
    cross_gap: bool


@dataclass
class Level:
    """一个货号或系列从基准周到评估周的可比水平（基准 = 1）。"""

    label: str
    week: str
    level: Range | None
    kind: str
    reason: str = ""
    base: Quote | None = None
    current: Quote | None = None
    generations: list[Generation] = field(default_factory=list)
    quality: Range | None = None
    paths: list[str] = field(default_factory=list)
    disputed: list[str] = field(default_factory=list)
    used_fees: set[str] = field(default_factory=set)
    used_claims: set[str] = field(default_factory=set)
    used_links: set[str] = field(default_factory=set)
    alternatives: list[str] = field(default_factory=list)

    @property
    def comparable(self) -> bool:
        return self.level is not None

    @property
    def change(self) -> Range | None:
        if self.level is None:
            return None
        return Range(self.level.low - 1, self.level.high - 1)

    def describe(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "label": self.label,
            "week": self.week,
            "comparability": self.kind,
            "comparability_label": KIND_LABELS[self.kind],
        }
        if self.reason:
            row["reason"] = self.reason
        if self.level is not None:
            assert self.change is not None
            row["level"] = [round(self.level.low, 6), round(self.level.high, 6)]
            row["change_percent"] = self.change.as_percent()
            row["direction"] = self.change.direction
        if self.base:
            row["base"] = self.base.note()
        if self.current:
            row["current"] = self.current.note()
        if self.quality is not None:
            row["quality_range"] = [self.quality.low, self.quality.high]
        if self.generations:
            row["generations"] = [
                {
                    "old": gen.old_sku,
                    "new": gen.new_sku,
                    "claim": gen.claim_id,
                    "quality_range": [gen.quality.low, gen.quality.high],
                    "link": gen.link_id,
                    "cross_gap": gen.cross_gap,
                }
                for gen in self.generations
            ]
        if self.paths:
            row["paths"] = list(self.paths)
        if self.disputed:
            row["disputed"] = list(self.disputed)
        if self.alternatives:
            row["alternatives"] = list(self.alternatives)
        return row


__all__ = [
    "DIRECT",
    "QUALITY",
    "NO_EVIDENCE",
    "KIND_LABELS",
    "Generation",
    "Level",
    "evaluate_item",
    "evaluate_series",
    "evaluate_edition",
    "edition_pins",
    "edition_chain",
    "latest_edition",
]


def _quote(
    inputs: Inputs,
    observation_id: str,
    *,
    service: str,
    as_of: str,
    pins: dict[str, str] | None,
    allowed: set[str] | None,
) -> Quote | None:
    try:
        return quote_for(
            inputs,
            observation_id,
            service=service,
            as_of=date.fromisoformat(as_of),
            pins=pins,
            allowed=allowed,
        )
    except NotComparable:
        return None


def _pick(quotes: Iterable[Quote]) -> Quote | None:
    """当期同款成交优先；没有成交才用可核验报价。"""
    quotes = list(quotes)
    traded = [quote for quote in quotes if quote.traded]
    if traded:
        return max(traded, key=lambda quote: (quote.sales, quote.observation_id))
    offered = [quote for quote in quotes if quote.status == "offered"]
    if offered:
        return max(offered, key=lambda quote: quote.observation_id)
    return None


def _week_quotes(
    inputs: Inputs,
    *,
    sku: str,
    region: str,
    week: str,
    service: str,
    as_of: str,
    pins: dict[str, str] | None,
    allowed: set[str] | None,
) -> list[Quote]:
    return _week_walk(
        inputs,
        sku=sku,
        region=region,
        week=week,
        service=service,
        as_of=as_of,
        pins=pins,
        allowed=allowed,
    )[0]


def _week_walk(
    inputs: Inputs,
    *,
    sku: str,
    region: str,
    week: str,
    service: str,
    as_of: str,
    pins: dict[str, str] | None,
    allowed: set[str] | None,
) -> tuple[list[Quote], list[str]]:
    """同周同款的可用报价，以及不可用的具体原因（含费用版本争议）。"""
    quotes: list[Quote] = []
    reasons: list[str] = []
    rows = [
        row
        for row in inputs.observations.values()
        if (row["sku"], row["region"], row["week"]) == (sku, region, week)
    ]
    for row in rows:
        try:
            quote = quote_for(
                inputs,
                row["id"],
                service=service,
                as_of=date.fromisoformat(as_of),
                pins=pins,
                allowed=allowed,
            )
        except NotComparable as exc:
            reasons.append(str(exc))
        else:
            quotes.append(quote)
    return quotes, reasons


def _base_quote(
    inputs: Inputs,
    *,
    sku: str,
    region: str,
    week: str,
    service: str,
    as_of: str,
    pins: dict[str, str] | None,
    allowed: set[str] | None,
) -> Quote | None:
    """基准报价。基准周是历史起点，即使本期次未重列也仍可用作分母。"""
    for scope in (allowed, None):
        quote = _pick(
            _week_quotes(
                inputs,
                sku=sku,
                region=region,
                week=week,
                service=service,
                as_of=as_of,
                pins=pins,
                allowed=scope,
            )
        )
        if quote is not None:
            return quote
        if allowed is None:
            break
    return None


def _current_quote(
    inputs: Inputs,
    *,
    sku: str,
    region: str,
    week: str,
    service: str,
    as_of: str,
    pins: dict[str, str] | None,
    allowed: set[str] | None,
) -> Quote | None:
    return _pick(
        _week_quotes(
            inputs,
            sku=sku,
            region=region,
            week=week,
            service=service,
            as_of=as_of,
            pins=pins,
            allowed=allowed,
        )
    )


def _known_edges(inputs: Inputs, region: str, as_of: str) -> list[QualityClaim]:
    """当期可用的质量档案边。同一硬件改动只取一份判断。"""
    edges: list[QualityClaim] = []
    seen: set[tuple[str, str]] = set()
    for base in inputs.quality_claims.values():
        key = (base["old"], base["new"])
        if key in seen or region not in base.get("regions", []):
            continue
        claim = quality_claim(inputs, base["old"], base["new"], region, as_of)
        if claim is not None:
            edges.append(claim)
            seen.add(key)
    return edges


def _simple_paths(
    edges: list[QualityClaim], start: str, target: str, max_length: int = 2
) -> list[list[QualityClaim]]:
    out: list[list[QualityClaim]] = []

    def walk(sku: str, used: list[QualityClaim], seen: set[str]) -> None:
        if sku == target and used:
            out.append(list(used))
            return
        if len(used) >= max_length:
            return
        for edge in edges:
            if edge.old != sku or edge.new in seen:
                continue
            walk(edge.new, used + [edge], seen | {edge.new})

    walk(start, [], {start})
    return out


def _generation_link(
    inputs: Inputs,
    claim: QualityClaim,
    *,
    scope: set[str] | None,
    as_of: str,
    region: str,
) -> dict[str, Any] | None:
    ids = {claim.base_id, claim.claim_id}
    candidates = [
        row
        for row in inputs.confirmed_links.values()
        if row.get("claim") in ids
        and (scope is None or row["id"] in scope)
        and inputs.observations[row["old_observation"]]["region"] == region
        and inputs.observations[row["new_observation"]]["region"] == region
    ]
    if len(candidates) != 1:
        return None
    link = candidates[0]
    if link["confirmed_on"] > as_of:
        return None
    old = inputs.observations[link["old_observation"]]
    new = inputs.observations[link["new_observation"]]
    if (old["sku"], new["sku"]) != (claim.old, claim.new):
        return None
    if old["week"] != new["week"] and not (
        link.get("cross_gap") and link.get("buyer") and link.get("merchant")
    ):
        return None
    return link


def _generations_for(
    inputs: Inputs,
    path: list[QualityClaim],
    *,
    scope: set[str] | None,
    as_of: str,
    region: str,
) -> list[Generation] | None:
    out: list[Generation] = []
    for claim in path:
        link = _generation_link(inputs, claim, scope=scope, as_of=as_of, region=region)
        if link is None:
            return None
        out.append(
            Generation(
                old_sku=claim.old,
                new_sku=claim.new,
                claim_id=claim.claim_id,
                quality=claim.price_range,
                link_id=link["id"],
                cross_gap=bool(link.get("cross_gap")),
            )
        )
    return out


def _generation_key(generations: list[Generation]) -> tuple[tuple[str, str, str], ...]:
    """路径去重键：同一条硬件链只算一次，不因数法不同重复计入。"""
    return tuple((gen.old_sku, gen.new_sku, gen.claim_id) for gen in generations)


def evaluate_item(
    inputs: Inputs,
    *,
    sku: str,
    region: str,
    service: str,
    base_week: str,
    week: str,
    as_of: str,
    pins: dict[str, str] | None = None,
    allowed: set[str] | None = None,
    link_scope: set[str] | None = None,
    label: str | None = None,
) -> Level:
    """计算一个货号/地区位置在 ``week`` 的可比水平。"""
    result = Level(label=label or f"{sku}@{region}@{week}", week=week, level=None, kind=NO_EVIDENCE)
    base = _base_quote(
        inputs,
        sku=sku,
        region=region,
        week=base_week,
        service=service,
        as_of=as_of,
        pins=pins,
        allowed=allowed,
    )
    if base is None:
        result.reason = f"基准周 {base_week} 的 {sku} 在 {region} 没有可比较价格"
        return result
    result.base = base

    # 1) 当期同款成交，其次可核验同款报价。
    week_quotes, same_reasons = _week_walk(
        inputs,
        sku=sku,
        region=region,
        week=week,
        service=service,
        as_of=as_of,
        pins=pins,
        allowed=allowed,
    )
    same = _pick(week_quotes)
    if same is not None:
        result.current = same
        result.kind = DIRECT
        result.level = Range.point(same.hardware_price / base.hardware_price)
        _collect_fees(result, [base, same])
        return result
    disputed_fee = [text for text in same_reasons if "未裁定版本" in text]
    if disputed_fee:
        result.reason = "；".join(disputed_fee)
        return result

    # 2) 没有同款结果，才走约定换代。同档替代不自动接续。
    edges = _known_edges(inputs, region, as_of)
    reachable: dict[str, list[list[QualityClaim]]] = {}
    for candidate_sku in sorted(replacement_targets(inputs, sku, region)):
        if candidate_sku == sku:
            continue
        paths = _simple_paths(edges, sku, candidate_sku)
        if paths:
            reachable[candidate_sku] = paths
    candidates: list[Quote] = []
    target_fee_disputed = ""
    for candidate_sku in reachable:
        quote = _current_quote(
            inputs,
            sku=candidate_sku,
            region=region,
            week=week,
            service=service,
            as_of=as_of,
            pins=pins,
            allowed=allowed,
        )
        if quote is None:
            _, why = _week_walk(
                inputs,
                sku=candidate_sku,
                region=region,
                week=week,
                service=service,
                as_of=as_of,
                pins=pins,
                allowed=allowed,
            )
            disputed = [text for text in why if "未裁定版本" in text]
            if disputed and not target_fee_disputed:
                target_fee_disputed = "；".join(disputed)
            continue
        candidates.append(quote)
    current = _pick(candidates)
    substitutes = substitute_target(inputs, sku, region)
    if current is None:
        if target_fee_disputed:
            result.reason = target_fee_disputed
        elif substitutes:
            result.reason = (
                f"当期没有 {sku} 的同款结果；{'/'.join(sorted(substitutes))} "
                "只是同档替代，不作为换代接续"
            )
        else:
            result.reason = f"当期 {week} 没有 {sku} 的同款结果，也没有已接上的换代型号"
        return result
    result.current = current

    # 同一组硬件改动可能有直接档与逐代档两种经确认的写法，必须说同一笔差价。
    assessments: dict[tuple[tuple[str, str, str], ...], tuple[str, Range, list[Generation]]] = {}
    for path in reachable.get(current.sku, []):
        generations = _generations_for(
            inputs, path, scope=link_scope, as_of=as_of, region=region
        )
        if generations is None:
            continue
        quality = Range(0.0, 0.0)
        for generation in generations:
            quality = quality + generation.quality
        label_text = "→".join([generations[0].old_sku] + [gen.new_sku for gen in generations])
        assessments[_generation_key(generations)] = (label_text, quality, generations)
    direct = quality_claim(inputs, sku, current.sku, region, as_of)
    if direct is not None:
        direct_generations = _generations_for(
            inputs, [direct], scope=link_scope, as_of=as_of, region=region
        )
        if direct_generations is not None:
            assessments[_generation_key(direct_generations)] = (
                f"{sku}→{current.sku}",
                direct.price_range,
                direct_generations,
            )

    if not assessments:
        if target_fee_disputed:
            result.reason = target_fee_disputed
        elif substitutes:
            result.reason = (
                f"当期只有 {current.sku} 成交；{'/'.join(sorted(substitutes))} "
                "只是同档替代，不作为换代接续，且没有其他已确认的换代接续"
            )
        else:
            result.reason = (
                f"当期只有 {current.sku} 成交，但 {sku}→{current.sku} 没有唯一、已确认、"
                "且同周重叠或双方签字跨断档的接续"
            )
        return result

    ordered = list(assessments.values())
    common = ordered[0][1]
    clash: list[str] = []
    for _, quality, _ in ordered[1:]:
        overlap = common.intersect(quality)
        if overlap is None:
            clash = [item[0] for item in ordered]
            break
        common = overlap
    if clash:
        result.disputed = clash
        result.reason = (
            "同一组硬件改动的多个经确认接法无法同时成立（"
            + "、".join(clash)
            + "）；相关接续有争议，不挑选能补足覆盖的接法"
        )
        return result

    result.paths = [item[0] for item in ordered]
    result.generations = ordered[0][2]
    result.quality = common
    result.used_claims.update(gen.claim_id for _, _, gens in ordered for gen in gens)
    result.used_links.update(gen.link_id for gen in result.generations if gen.link_id)
    _collect_fees(result, [base, current])
    result.kind = QUALITY
    base_hw = base.hardware_price
    result.level = Range(
        current.hardware_price / (base_hw + common.high),
        current.hardware_price / (base_hw + common.low),
    )
    return result


def _collect_fees(result: Level, quotes: Iterable[Quote]) -> None:
    for quote in quotes:
        if ":" in quote.fee_source:
            result.used_fees.add(quote.fee_source.split(":", 1)[1])


def evaluate_series(
    inputs: Inputs,
    *,
    series_id: str,
    week: str,
    as_of: str,
    pins: dict[str, str] | None = None,
    allowed: set[str] | None = None,
    link_scope: set[str] | None = None,
) -> Level:
    series = inputs.series[series_id]
    return evaluate_item(
        inputs,
        sku=series["initial_sku"],
        region=series["region"],
        service=series["service"],
        base_week=series["initial_week"],
        week=week,
        as_of=as_of,
        pins=pins,
        allowed=allowed,
        link_scope=link_scope,
        label=f"{series_id}@{week}",
    )


def edition_pins(inputs: Inputs, edition: dict[str, Any]) -> dict[str, str]:
    """把刊期指定的费用说明书钉成该期的口径。"""
    pins: dict[str, str] = {}
    for statement_id in edition.get("charges", []):
        statement = inputs.fee_statements.get(statement_id)
        if statement is None:
            raise ValueError(f"刊期 {edition['id']} 引用未知费用说明书 {statement_id}")
        observation = statement["observation"]
        if observation in pins:
            raise ValueError(f"刊期 {edition['id']} 对观察 {observation} 指定了多份费用说明书")
        pins[observation] = statement_id
    return pins


def evaluate_edition(inputs: Inputs, edition_id: str) -> Level:
    """评估一个已发布刊期。已发布即冻结，后来的订正不改写它。"""
    edition = inputs.editions[edition_id]
    return evaluate_series(
        inputs,
        series_id=edition["series"],
        week=edition["week"],
        as_of=edition["published_on"],
        pins=edition_pins(inputs, edition),
        allowed=set(edition["observations"]),
        link_scope=set(edition["links"]),
    )


def edition_chain(inputs: Inputs, edition_id: str) -> list[str]:
    """原版 → 各次重发的顺序链。"""
    chain = [edition_id]
    seen = {edition_id}
    while True:
        children = [
            row["id"] for row in inputs.editions.values() if row.get("replaces") == chain[-1]
        ]
        if not children:
            return chain
        if len(children) > 1:
            raise ValueError(f"刊期 {chain[-1]} 有多个替代版本: {children}")
        if children[0] in seen:
            raise ValueError(f"刊期 {edition_id} 的替代关系成环")
        chain.append(children[0])
        seen.add(children[0])


def latest_edition(inputs: Inputs, edition_id: str, as_of: str) -> dict[str, Any]:
    chosen = inputs.editions[edition_id]
    for candidate in edition_chain(inputs, edition_id)[1:]:
        row = inputs.editions[candidate]
        if row["published_on"] <= as_of:
            chosen = row
    return chosen


# ---------------------------------------------------------------------------
# 多个旧款接同一新品：新品只提供一份证据
# ---------------------------------------------------------------------------


def shared_evidence_groups(levels: dict[str, Level]) -> dict[str, str]:
    """同一新品的成交只算一份证据。

    返回 ``篮子项 -> 共享组名``。同一合同中若有多个篮子项都以同一个新品观察为当
    期结果，这新一代的销量只能作为一份证据，不能分别补齐几份覆盖。
    """
    groups: dict[str, list[str]] = {}
    for key, level in levels.items():
        if level.kind != QUALITY or level.current is None or not level.generations:
            continue
        groups.setdefault(level.current.observation_id, []).append(key)
    out: dict[str, str] = {}
    for observation_id, keys in groups.items():
        if len(keys) < 2:
            continue
        for key in keys:
            out[key] = observation_id
    return out


def evaluate_sales_weighted(
    inputs: Inputs, *, week: str, base_week: str, as_of: str
) -> dict[str, Any]:
    """销售视角：按实际成交销量加权，与固定篮子指数分开呈现。

    这里只汇总有成交的同款对；换代造成的结构变化单独列出，不混进固定篮子。
    """
    pairs: list[dict[str, Any]] = []
    for sku in inputs.products:
        for region in {row["region"] for row in inputs.observations.values()}:
            base_rows = [
                row
                for row in inputs.observations.values()
                if (row["sku"], row["region"], row["week"]) == (sku, region, base_week)
            ]
            current_rows = [
                row
                for row in inputs.observations.values()
                if (row["sku"], row["region"], row["week"]) == (sku, region, week)
            ]
            if not base_rows or not current_rows:
                continue
            base_quote = _pick(
                [
                    quote
                    for quote in (
                        _quote(
                            inputs,
                            row["id"],
                            service=inputs.products[sku].get("service") or "basic",
                            as_of=as_of,
                            pins=None,
                            allowed=None,
                        )
                        for row in base_rows
                    )
                    if quote is not None
                ]
            )
            current_quote = _pick(
                [
                    quote
                    for quote in (
                        _quote(
                            inputs,
                            row["id"],
                            service=inputs.products[sku].get("service") or "basic",
                            as_of=as_of,
                            pins=None,
                            allowed=None,
                        )
                        for row in current_rows
                    )
                    if quote is not None
                ]
            )
            if base_quote is None or current_quote is None:
                continue
            pairs.append(
                {
                    "sku": sku,
                    "region": region,
                    "base_sales": base_quote.sales,
                    "current_sales": current_quote.sales,
                    "base_price": base_quote.price,
                    "current_price": current_quote.price,
                    "change_percent": round(
                        (current_quote.price / base_quote.price - 1) * 100, 2
                    ),
                }
            )
    total_sales = sum(row["current_sales"] for row in pairs)
    weighted = (
        round(
            sum(row["change_percent"] * row["current_sales"] for row in pairs) / total_sales, 2
        )
        if total_sales
        else None
    )
    return {
        "view": "sales_weighted",
        "base_week": base_week,
        "week": week,
        "note": "实际成交结构变化，仅供销售参考；不进入固定篮子合同指数",
        "pairs": pairs,
        "weighted_change_percent": weighted,
    }
