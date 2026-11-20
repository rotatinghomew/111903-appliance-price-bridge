"""把两本台账的分析结果组织成一份可复核的报告。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import BasketResult, evaluate_contract
from .evidence import fee_disputes
from .index_eval import Level, evaluate_edition, evaluate_series
from .inputs import Inputs, load_inputs
from .publications import (
    contract_reference_evaluation,
    edition_states,
    reissue_plan,
    reference_basis,
)
from .settlements import (
    difference_for,
    notice_states,
    notice_trace,
    payment_window,
    receipt_summary,
    worklist,
)


@dataclass
class Analysis:
    inputs: Inputs

    # ---- 指数 ---------------------------------------------------------
    def editions(self) -> dict[str, dict[str, Any]]:
        return {ident: state.describe() for ident, state in edition_states(self.inputs).items()}

    def edition(self, edition_id: str) -> Level:
        return evaluate_edition(self.inputs, edition_id)

    def series(self, series_id: str, week: str, as_of: str) -> Level:
        return evaluate_series(self.inputs, series_id=series_id, week=week, as_of=as_of)

    def reissue_plan(self, as_of: str | None = None) -> dict[str, Any]:
        return reissue_plan(self.inputs, as_of)

    # ---- 合同 ---------------------------------------------------------
    def contract(self, contract_id: str, period: str | None = None, as_of: str | None = None) -> BasketResult:
        from datetime import date

        return evaluate_contract(
            self.inputs,
            contract_id,
            period=period,
            as_of=date.fromisoformat(as_of) if as_of else None,
        )

    def contract_reference(self, contract_id: str, as_of: str) -> dict[str, Any]:
        references = [
            row for row in self.inputs.contract_references if row["contract"] == contract_id
        ]
        if not references:
            raise KeyError(f"合同 {contract_id} 没有下一结算期基准约定")
        return contract_reference_evaluation(self.inputs, references[0], as_of)

    def reference_basis(self, contract_id: str, as_of: str):
        references = [
            row for row in self.inputs.contract_references if row["contract"] == contract_id
        ]
        if not references:
            raise KeyError(f"合同 {contract_id} 没有下一结算期基准约定")
        return reference_basis(self.inputs, references[0], as_of)

    # ---- 结算 ---------------------------------------------------------
    def difference(
        self,
        contract_id: str,
        period: str,
        *,
        recomputed_amount: float | None = None,
    ) -> dict[str, Any]:
        return difference_for(
            self.inputs, contract_id, period, recomputed_amount=recomputed_amount
        ).describe()

    def window(self, window_id: str) -> dict[str, Any]:
        return payment_window(self.inputs, window_id).describe()

    def notice(self, notice_id: str) -> dict[str, Any]:
        return notice_trace(self.inputs, notice_id)

    def worklist(self) -> dict[str, list[dict[str, Any]]]:
        return worklist(self.inputs)

    def receipts(self) -> dict[str, dict[str, Any]]:
        return receipt_summary(self.inputs)

    def settlement_states(self) -> dict[str, dict[str, Any]]:
        return notice_states(self.inputs)

    # ---- 汇总 ---------------------------------------------------------
    def report(self, *, as_of: str | None = None) -> dict[str, Any]:
        states = edition_states(self.inputs)
        as_of = as_of or max(
            [state.published_on for state in states.values()]
            + [row.get("received_on", "") for row in self.inputs.fee_statements.values()]
            + [row["release_signed_on"] for row in self.inputs.notice_disputes]
        )
        return {
            "as_of": as_of,
            "editions": {ident: state.describe() for ident, state in states.items()},
            "latest_editions": {
                state.month: state.edition_id
                for state in states.values()
                if state.is_latest
            },
            "fee_disputes": fee_disputes(self.inputs, _day(as_of)),
            "reissue_plan": reissue_plan(self.inputs, as_of),
            "contracts": {
                contract_id: self.contract(contract_id, as_of=as_of).describe()
                for contract_id in self.inputs.contracts
            },
            "contract_references": {
                row["contract"]: contract_reference_evaluation(self.inputs, row, as_of)
                for row in self.inputs.contract_references
            },
            "settlements": {
                ident: state["status_label"] for ident, state in notice_states(self.inputs).items()
            },
            "worklist": worklist(self.inputs),
        }


def _day(text: str):
    from datetime import date

    try:
        return date.fromisoformat(text)
    except ValueError:
        return date.max


def analyse(data_dir: str | None = None) -> Analysis:
    return Analysis(load_inputs(data_dir))
