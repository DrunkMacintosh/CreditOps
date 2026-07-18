"""Scoped evidence view -> deterministic calculator suite for the maker.

Maps Confirmed Facts (the ONLY authoritative fact class, per CONTEXT.md) onto
calculator inputs by canonical field key, runs the full deterministic suite,
and derives the missing-evidence records.  ASSUMPTION: the field-key taxonomy
below is SYNTHETIC — no official SHB chart of accounts has been supplied
(docs/OPEN_QUESTIONS.md).  All customer data in any fixture using these keys is
synthetic and created solely for demonstration.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from creditops.application.ports.underwriting import EvidenceFact, EvidenceView
from creditops.application.underwriting import calculators as calc
from creditops.domain.underwriting import GapBlockingLevel

#: calculator input name -> canonical confirmed-fact field key (current period).
CANONICAL_FIELD_KEYS: dict[str, str] = {
    "current_assets": "financials.current_assets",
    "current_liabilities": "financials.current_liabilities",
    "inventory": "financials.inventory",
    "total_debt": "financials.total_debt",
    "total_equity": "financials.total_equity",
    "total_assets": "financials.total_assets",
    "revenue": "financials.revenue",
    "gross_profit": "financials.gross_profit",
    "operating_profit": "financials.operating_profit",
    "net_profit": "financials.net_profit",
    "accounts_receivable": "financials.accounts_receivable",
    "accounts_payable": "financials.accounts_payable",
    "cost_of_goods_sold": "financials.cost_of_goods_sold",
    "own_working_capital": "financials.own_working_capital",
    "other_funding_sources": "financials.other_funding_sources",
}

#: prior-period field keys used for period-over-period trend analysis.
PRIOR_PERIOD_FIELD_KEYS: dict[str, str] = {
    "revenue": "financials.prior.revenue",
    "net_profit": "financials.prior.net_profit",
}

#: ASSUMPTION (synthetic): the canonical named downside scenario recomputes the
#: base metrics under an explicit -20% revenue adjustment.  Nothing
#: probabilistic is invented; the adjustment is visible and challengeable.
DOWNSIDE_SCENARIO_NAME = "doanh_thu_giam_20pct"
_DOWNSIDE_REVENUE_CHANGE = Decimal("-0.20")


@dataclass(frozen=True, slots=True)
class MissingEvidence:
    """A calculator input the evidence view could not supply."""

    input_name: str
    field_key: str
    reason: str
    blocking_level: GapBlockingLevel


@dataclass(frozen=True, slots=True)
class CalculatorSuite:
    """Every deterministic result computed for one evidence view."""

    results: tuple[calc.CalculatorResult, ...]
    trend_results: tuple[calc.TrendResult, ...]
    scenario_results: tuple[calc.ScenarioResult, ...]
    missing: tuple[MissingEvidence, ...]

    def result_ids(self) -> tuple[str, ...]:
        ids: list[str] = [item.result_id for item in self.results]
        ids.extend(item.result_id for item in self.trend_results)
        ids.extend(item.result_id for item in self.scenario_results)
        return tuple(ids)


def _decimal_of(fact: EvidenceFact) -> Decimal | None:
    if isinstance(fact.value, bool):
        return None
    try:
        return Decimal(str(fact.value))
    except InvalidOperation:
        return None


def _input_for(
    view: EvidenceView, name: str, field_key: str
) -> tuple[calc.CalculatorInput, MissingEvidence | None]:
    fact = next(
        (item for item in view.confirmed_facts if item.field_key == field_key), None
    )
    if fact is None:
        return (
            calc.CalculatorInput(name=name, value=None),
            MissingEvidence(
                input_name=name,
                field_key=field_key,
                reason="no confirmed fact",
                blocking_level=GapBlockingLevel.BLOCKING,
            ),
        )
    value = _decimal_of(fact)
    refs = (
        calc.FactRef(kind="CONFIRMED_FACT", ref_id=str(fact.confirmed_fact_id)),
    )
    if value is None:
        return (
            calc.CalculatorInput(name=name, value=None, fact_refs=refs),
            MissingEvidence(
                input_name=name,
                field_key=field_key,
                reason="confirmed fact value is not numeric",
                blocking_level=GapBlockingLevel.BLOCKING,
            ),
        )
    return calc.CalculatorInput(name=name, value=value, fact_refs=refs), None


def build_calculator_suite(view: EvidenceView) -> CalculatorSuite:
    """Run the standard deterministic suite over the scoped evidence view."""

    inputs: dict[str, calc.CalculatorInput] = {}
    missing: list[MissingEvidence] = []
    for name, field_key in CANONICAL_FIELD_KEYS.items():
        built, absent = _input_for(view, name, field_key)
        inputs[name] = built
        if absent is not None:
            missing.append(absent)

    rec_days = calc.receivable_days(inputs["accounts_receivable"], inputs["revenue"])
    inv_days = calc.inventory_days(inputs["inventory"], inputs["cost_of_goods_sold"])
    pay_days = calc.payable_days(
        inputs["accounts_payable"], inputs["cost_of_goods_sold"]
    )
    ccc = calc.cash_conversion_cycle(rec_days, inv_days, pay_days)
    wc_need = calc.working_capital_need(
        calc.CalculatorInput(
            name="annual_operating_outlay",
            value=inputs["cost_of_goods_sold"].value,
            fact_refs=inputs["cost_of_goods_sold"].fact_refs,
        ),
        ccc,
    )
    results: tuple[calc.CalculatorResult, ...] = (
        calc.current_ratio(inputs["current_assets"], inputs["current_liabilities"]),
        calc.quick_ratio(
            inputs["current_assets"],
            inputs["inventory"],
            inputs["current_liabilities"],
        ),
        calc.debt_to_equity(inputs["total_debt"], inputs["total_equity"]),
        calc.debt_to_assets(inputs["total_debt"], inputs["total_assets"]),
        calc.gross_margin(inputs["gross_profit"], inputs["revenue"]),
        calc.operating_margin(inputs["operating_profit"], inputs["revenue"]),
        calc.net_margin(inputs["net_profit"], inputs["revenue"]),
        calc.return_on_assets(inputs["net_profit"], inputs["total_assets"]),
        calc.return_on_equity(inputs["net_profit"], inputs["total_equity"]),
        rec_days,
        inv_days,
        pay_days,
        calc.asset_turnover(inputs["revenue"], inputs["total_assets"]),
        ccc,
        wc_need,
        calc.working_capital_gap(
            wc_need,
            inputs["own_working_capital"],
            inputs["other_funding_sources"],
        ),
    )

    trend_results: list[calc.TrendResult] = []
    for metric, prior_key in PRIOR_PERIOD_FIELD_KEYS.items():
        prior_input, prior_missing = _input_for(view, f"prior_{metric}", prior_key)
        if prior_missing is not None:
            # A missing prior period limits trend analysis but does not block
            # the current-period assessment.
            missing.append(
                MissingEvidence(
                    input_name=prior_missing.input_name,
                    field_key=prior_missing.field_key,
                    reason=prior_missing.reason,
                    blocking_level=GapBlockingLevel.CLARIFICATION,
                )
            )
        current_input = inputs[metric]
        trend_results.append(
            calc.trend_analysis(
                metric,
                [
                    calc.TrendPoint(
                        period="ky_truoc",
                        value=prior_input.value,
                        fact_refs=prior_input.fact_refs,
                    ),
                    calc.TrendPoint(
                        period="ky_hien_tai",
                        value=current_input.value,
                        fact_refs=current_input.fact_refs,
                    ),
                ],
            )
        )

    scenario = calc.scenario_projection(
        DOWNSIDE_SCENARIO_NAME,
        [inputs["revenue"], inputs["net_profit"], inputs["operating_profit"]],
        [
            calc.ScenarioAdjustment(
                metric="revenue", relative_change=_DOWNSIDE_REVENUE_CHANGE
            )
        ],
    )

    return CalculatorSuite(
        results=results,
        trend_results=tuple(trend_results),
        scenario_results=(scenario,),
        missing=tuple(missing),
    )
