"""读取工作区 JSON 资料，并把两张独立台账接在一起。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class InputError(ValueError):
    """资料本身不满足复核的前提（不是业务结论，而是无法读取）。"""


def _rows(value: Any, key: str, where: str, id_key: str = "id") -> list[dict[str, Any]]:
    rows = value.get(key) if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise InputError(f"{where}: 缺少列表 {key}")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(id_key), str):
            raise InputError(f"{where}: {key} 的每一行都需要字符串 {id_key}")
    return rows


def parse_day(text: str, where: str) -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError) as exc:  # pragma: no cover - 由资料错误触发
        raise InputError(f"{where}: 日期 {text!r} 不是 YYYY-MM-DD") from exc


def _index(rows: Iterable[dict[str, Any]], key: str, where: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ident = row.get(key)
        if not isinstance(ident, str) or not ident:
            raise InputError(f"{where}: 缺少 {key}")
        if ident in out:
            raise InputError(f"{where}: {key}={ident} 重复")
        out[ident] = row
    return out


@dataclass(frozen=True)
class Inputs:
    """两本台账的只读视图。"""

    data_dir: Path
    season: dict[str, Any]
    chain: dict[str, Any]
    products: dict[str, dict[str, Any]]
    observations: dict[str, dict[str, Any]]
    bridges: list[dict[str, Any]]
    contracts: dict[str, dict[str, Any]]
    fee_statements: dict[str, dict[str, Any]]
    fee_resolutions: list[dict[str, Any]]
    rate_agreements: dict[str, dict[str, Any]]
    settlements: dict[str, dict[str, Any]]
    payment_windows: dict[str, dict[str, Any]]
    bank_batches: dict[str, dict[str, Any]]
    bank_receipts: list[dict[str, Any]]
    notice_disputes: list[dict[str, Any]]
    series: dict[str, dict[str, Any]]
    quality_claims: dict[str, dict[str, Any]]
    claim_corrections: list[dict[str, Any]]
    confirmed_links: dict[str, dict[str, Any]]
    editions: dict[str, dict[str, Any]]
    contract_references: list[dict[str, Any]]

    def fee_versions_in_force(self, statement_id: str, on: date) -> tuple[str, ...]:
        """返回 ``on`` 当日对同一观察有效的费用说明书编号。

        订正只替代它自己 ``replaces`` 指向的那一份凭据。沿替代链向下走，只有当
        同一凭据在当日伸出两个及以上、都还没被替代的版本时，才构成版本争议；
        共同签认的费用裁定指定生效版本后才恢复单一版本。
        """
        statements = self.fee_statements
        if statement_id not in statements:
            raise InputError(f"未知费用说明书 {statement_id}")
        by_id = statements
        resolution_for = self._fee_resolution(by_id[statement_id].get("observation"), on)

        def children_of(ident: str) -> list[dict[str, Any]]:
            return sorted(
                (row for row in by_id.values() if row.get("replaces") == ident),
                key=lambda row: (row["received_on"], row["id"]),
            )

        def descendants(ident: str) -> set[str]:
            out: set[str] = set()
            pending = [ident]
            while pending:
                current = pending.pop()
                for row in children_of(current):
                    if row["id"] not in out:
                        out.add(row["id"])
                        pending.append(row["id"])
            return out

        def walk(ident: str) -> tuple[str, ...]:
            kids = [
                row
                for row in children_of(ident)
                if row["received_on"] <= on.isoformat()
            ]
            if not kids:
                return (ident,)
            if len(kids) == 1:
                return walk(kids[0]["id"])
            # 同一凭据在当日伸出多个未替代的版本：有共同签认的裁定就沿裁定
            # 指向的版本继续，否则每个叶子都留在争议中。
            if resolution_for is not None and resolution_for in descendants(ident):
                return walk(resolution_for)
            leaves: set[str] = set()
            for kid in kids:
                leaves.update(walk(kid["id"]))
            return tuple(sorted(leaves))

        # 从整条链的根开始，这样引用中间某一版时结论一致。
        root_id = statement_id
        seen: set[str] = {root_id}
        while by_id[root_id].get("replaces"):
            parent = by_id[root_id]["replaces"]
            if parent in seen:
                raise InputError(f"费用说明书 {statement_id} 的 replaces 成环")
            seen.add(parent)
            root_id = parent
        return walk(root_id)

    def _fee_resolution(self, observation: str | None, on: date) -> str | None:
        applicable = [
            row
            for row in self.fee_resolutions
            if row.get("observation") == observation
            and row.get("signed_on", "9999-12-31") <= on.isoformat()
        ]
        if not applicable:
            return None
        chosen = max(applicable, key=lambda row: (row["signed_on"], row["id"]))
        selected = chosen.get("selected")
        return selected if selected in self.fee_statements else None


def load_inputs(data_dir: str | Path | None = None) -> Inputs:
    """读取 ``overlap-season.json`` 与 ``publication_chain.json``。"""
    base = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    season_path = base / "overlap-season.json"
    chain_path = base / "publication_chain.json"
    try:
        season = json.loads(season_path.read_text(encoding="utf-8"))
        chain = json.loads(chain_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"找不到资料文件: {exc.filename}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"资料 JSON 无法解析: {exc}") from exc
    if not isinstance(season, dict) or not isinstance(chain, dict):
        raise InputError("两本台账都必须是 JSON 对象")

    products = _index(
        _rows(season, "products", "season", "sku") + _rows(chain, "products", "chain", "sku"),
        "sku",
        "products",
    )
    observations = _index(
        _rows(season, "observations", "season") + _rows(chain, "observations", "chain"),
        "id",
        "observations",
    )
    fee_statements = _index(
        _rows(season, "fee_statements", "season") + _rows(chain, "fee_statements", "chain"),
        "id",
        "fee_statements",
    )
    for row in observations.values():
        if row["sku"] not in products:
            raise InputError(f"观察 {row['id']} 引用了未知货号 {row['sku']}")
    for row in fee_statements.values():
        if row.get("observation") not in observations:
            raise InputError(f"费用说明书 {row['id']} 引用了未知观察 {row.get('observation')}")
        if row.get("replaces") and row["replaces"] not in fee_statements:
            raise InputError(f"费用说明书 {row['id']} 替代了未知编号 {row['replaces']}")

    inputs = Inputs(
        data_dir=base,
        season=season,
        chain=chain,
        products=products,
        observations=observations,
        bridges=list(season.get("bridges", [])) + list(chain.get("bridges", [])),
        contracts=_index(_rows(season, "contracts", "season"), "id", "contracts"),
        fee_statements=fee_statements,
        fee_resolutions=list(season.get("fee_resolutions", [])),
        rate_agreements=_index(_rows(season, "rate_agreements", "season"), "id", "rate_agreements"),
        settlements=_index(_rows(season, "settlements", "season"), "id", "settlements"),
        payment_windows=_index(
            _rows(season, "payment_windows", "season", "window")
            + _rows(chain, "payment_windows", "chain", "window"),
            "window",
            "payment_windows",
        ),
        bank_batches=_index(_rows(season, "bank_batches", "season"), "id", "bank_batches"),
        bank_receipts=list(season.get("bank_receipts", [])),
        notice_disputes=list(season.get("notice_disputes", [])),
        series=_index(_rows(chain, "series", "chain", "series"), "series", "series"),
        quality_claims=_index(_rows(chain, "quality_claims", "chain"), "id", "quality_claims"),
        claim_corrections=list(chain.get("claim_corrections", [])),
        confirmed_links=_index(_rows(chain, "confirmed_links", "chain"), "id", "confirmed_links"),
        editions=_index(_rows(chain, "editions", "chain"), "id", "editions"),
        contract_references=list(chain.get("contract_references", [])),
    )
    return inputs
