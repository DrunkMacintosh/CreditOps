"""Deterministic-calculator tests for the Credit Underwriting Agent.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  Figures below
belong to the invented SME "Cong ty TNHH Banh Trang Trang Bom Demo" and have no
relation to any real company.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from creditops.application.underwriting.calculators import (
    CalculatorInput,
    CalculatorValidationError,
    ComputedOutcome,
    DebtServiceSchedule,
    DownsideScenarioSet,
    FactRef,
    FinancingGap,
    FinancingInputs,
    NotComputableOutcome,
    RepaymentCapacity,
    ScenarioAdjustment,
    ScenarioSpec,
    TrendPoint,
    WorkingCapitalBaseInputs,
    WorkingCapitalNeed,
    asset_turnover,
    cash_conversion_cycle,
    current_ratio,
    debt_service_schedule,
    debt_to_assets,
    debt_to_equity,
    downside_scenarios,
    financing_gap,
    gross_margin,
    inventory_days,
    net_margin,
    operating_margin,
    payable_days,
    quick_ratio,
    receivable_days,
    repayment_capacity,
    return_on_assets,
    return_on_equity,
    scenario_projection,
    trend_analysis,
    working_capital_gap,
    working_capital_need,
    working_capital_requirement,
)


def _input(name: str, value: str | None, *fact_ids: str) -> CalculatorInput:
    return CalculatorInput(
        name=name,
        value=None if value is None else Decimal(value),
        fact_refs=tuple(
            FactRef(kind="CONFIRMED_FACT", ref_id=fact_id) for fact_id in fact_ids
        ),
    )


class TestKnownAnswers:
    """Known-answer fixtures: same synthetic input, externally checked output."""

    def test_current_ratio(self) -> None:
        result = current_ratio(
            _input("current_assets", "1500", "fact-ca"),
            _input("current_liabilities", "1000", "fact-cl"),
        )
        assert result.outcome == ComputedOutcome(value=Decimal("1.500000"))

    def test_quick_ratio_subtracts_inventory(self) -> None:
        result = quick_ratio(
            _input("current_assets", "1500", "fact-ca"),
            _input("inventory", "600", "fact-inv"),
            _input("current_liabilities", "1000", "fact-cl"),
        )
        assert result.outcome == ComputedOutcome(value=Decimal("0.900000"))

    def test_leverage_ratios(self) -> None:
        d2e = debt_to_equity(
            _input("total_debt", "800", "fact-debt"),
            _input("total_equity", "400", "fact-eq"),
        )
        d2a = debt_to_assets(
            _input("total_debt", "800", "fact-debt"),
            _input("total_assets", "2000", "fact-assets"),
        )
        assert d2e.outcome == ComputedOutcome(value=Decimal("2.000000"))
        assert d2a.outcome == ComputedOutcome(value=Decimal("0.400000"))

    def test_profitability_ratios(self) -> None:
        revenue = _input("revenue", "10000", "fact-rev")
        assert gross_margin(
            _input("gross_profit", "2500", "fact-gp"), revenue
        ).outcome == ComputedOutcome(value=Decimal("0.250000"))
        assert operating_margin(
            _input("operating_profit", "1200", "fact-op"), revenue
        ).outcome == ComputedOutcome(value=Decimal("0.120000"))
        assert net_margin(
            _input("net_profit", "800", "fact-np"), revenue
        ).outcome == ComputedOutcome(value=Decimal("0.080000"))
        assert return_on_assets(
            _input("net_profit", "800", "fact-np"),
            _input("total_assets", "2000", "fact-assets"),
        ).outcome == ComputedOutcome(value=Decimal("0.400000"))
        assert return_on_equity(
            _input("net_profit", "800", "fact-np"),
            _input("total_equity", "400", "fact-eq"),
        ).outcome == ComputedOutcome(value=Decimal("2.000000"))

    def test_activity_ratios_scale_days(self) -> None:
        assert receivable_days(
            _input("accounts_receivable", "500", "fact-ar"),
            _input("revenue", "10000", "fact-rev"),
        ).outcome == ComputedOutcome(value=Decimal("18.250000"))
        assert inventory_days(
            _input("inventory", "600", "fact-inv"),
            _input("cost_of_goods_sold", "7300", "fact-cogs"),
        ).outcome == ComputedOutcome(value=Decimal("30.000000"))
        assert payable_days(
            _input("accounts_payable", "400", "fact-ap"),
            _input("cost_of_goods_sold", "7300", "fact-cogs"),
        ).outcome == ComputedOutcome(value=Decimal("20.000000"))
        assert asset_turnover(
            _input("revenue", "10000", "fact-rev"),
            _input("total_assets", "2000", "fact-assets"),
        ).outcome == ComputedOutcome(value=Decimal("5.000000"))

    def test_cash_conversion_cycle_and_working_capital(self) -> None:
        ccc = cash_conversion_cycle(
            receivable_days(
                _input("accounts_receivable", "500", "fact-ar"),
                _input("revenue", "10000", "fact-rev"),
            ),
            inventory_days(
                _input("inventory", "600", "fact-inv"),
                _input("cost_of_goods_sold", "7300", "fact-cogs"),
            ),
            payable_days(
                _input("accounts_payable", "400", "fact-ap"),
                _input("cost_of_goods_sold", "7300", "fact-cogs"),
            ),
        )
        assert ccc.outcome == ComputedOutcome(value=Decimal("28.250000"))
        need = working_capital_need(
            _input("annual_operating_outlay", "7300", "fact-cogs"), ccc
        )
        assert need.outcome == ComputedOutcome(value=Decimal("565.000000"))
        gap = working_capital_gap(
            need,
            _input("own_working_capital", "200", "fact-owc"),
            _input("other_funding_sources", "65", "fact-ofs"),
        )
        assert gap.outcome == ComputedOutcome(value=Decimal("300.000000"))


class TestDeterminism:
    def test_same_input_same_output_and_result_id(self) -> None:
        make = lambda: current_ratio(  # noqa: E731
            _input("current_assets", "1500.50", "fact-ca"),
            _input("current_liabilities", "999.10", "fact-cl"),
        )
        first, second = make(), make()
        assert first == second
        assert first.result_id == second.result_id

    def test_result_id_changes_with_inputs(self) -> None:
        base = current_ratio(
            _input("current_assets", "1500", "fact-ca"),
            _input("current_liabilities", "1000", "fact-cl"),
        )
        changed = current_ratio(
            _input("current_assets", "1501", "fact-ca"),
            _input("current_liabilities", "1000", "fact-cl"),
        )
        assert base.result_id != changed.result_id

    def test_decimal_precision_no_float_drift(self) -> None:
        result = net_margin(
            _input("net_profit", "1", "fact-np"),
            _input("revenue", "3", "fact-rev"),
        )
        assert result.outcome == ComputedOutcome(value=Decimal("0.333333"))

    def test_property_style_spot_check_ratio_definition(self) -> None:
        # Spot-check the invariant ratio(n, d) * d == n within quantization
        # tolerance over a spread of Decimal magnitudes.
        for numerator, denominator in [
            ("1", "7"),
            ("123456789.123456", "0.000321"),
            ("-500", "250"),
            ("0", "9999"),
        ]:
            result = current_ratio(
                _input("current_assets", numerator, "fact-ca"),
                _input("current_liabilities", denominator, "fact-cl"),
            )
            assert isinstance(result.outcome, ComputedOutcome)
            reconstructed = result.outcome.value * Decimal(denominator)
            assert abs(reconstructed - Decimal(numerator)) <= (
                Decimal("0.000001") * abs(Decimal(denominator))
            )


class TestNotComputable:
    def test_division_by_zero_is_explicit_never_zero(self) -> None:
        result = current_ratio(
            _input("current_assets", "1500", "fact-ca"),
            _input("current_liabilities", "0", "fact-cl"),
        )
        assert isinstance(result.outcome, NotComputableOutcome)
        assert "division by zero" in result.outcome.reason
        assert result.outcome.reason.startswith("not computable:")

    def test_missing_input_is_explicit(self) -> None:
        result = debt_to_equity(
            _input("total_debt", None),
            _input("total_equity", "400", "fact-eq"),
        )
        assert isinstance(result.outcome, NotComputableOutcome)
        assert "missing input total_debt" in result.outcome.reason

    def test_quick_ratio_missing_inventory(self) -> None:
        result = quick_ratio(
            _input("current_assets", "1500", "fact-ca"),
            _input("inventory", None),
            _input("current_liabilities", "1000", "fact-cl"),
        )
        assert isinstance(result.outcome, NotComputableOutcome)
        assert "inventory" in result.outcome.reason

    def test_not_computable_propagates_through_composition(self) -> None:
        ccc = cash_conversion_cycle(
            receivable_days(
                _input("accounts_receivable", None),
                _input("revenue", "10000", "fact-rev"),
            ),
            inventory_days(
                _input("inventory", "600", "fact-inv"),
                _input("cost_of_goods_sold", "7300", "fact-cogs"),
            ),
            payable_days(
                _input("accounts_payable", "400", "fact-ap"),
                _input("cost_of_goods_sold", "7300", "fact-cogs"),
            ),
        )
        assert isinstance(ccc.outcome, NotComputableOutcome)
        need = working_capital_need(_input("annual_operating_outlay", "7300"), ccc)
        assert isinstance(need.outcome, NotComputableOutcome)
        gap = working_capital_gap(
            need, _input("own_working_capital", "200"), _input("other_funding", "0")
        )
        assert isinstance(gap.outcome, NotComputableOutcome)


class TestProvenance:
    def test_result_carries_input_fact_refs(self) -> None:
        result = quick_ratio(
            _input("current_assets", "1500", "fact-ca"),
            _input("inventory", "600", "fact-inv"),
            _input("current_liabilities", "1000", "fact-cl"),
        )
        ref_ids = {ref.ref_id for ref in result.fact_refs}
        assert ref_ids == {"fact-ca", "fact-inv", "fact-cl"}

    def test_composed_result_unions_provenance(self) -> None:
        ccc = cash_conversion_cycle(
            receivable_days(
                _input("accounts_receivable", "500", "fact-ar"),
                _input("revenue", "10000", "fact-rev"),
            ),
            inventory_days(
                _input("inventory", "600", "fact-inv"),
                _input("cost_of_goods_sold", "7300", "fact-cogs"),
            ),
            payable_days(
                _input("accounts_payable", "400", "fact-ap"),
                _input("cost_of_goods_sold", "7300", "fact-cogs"),
            ),
        )
        ref_ids = {ref.ref_id for ref in ccc.fact_refs}
        assert ref_ids == {"fact-ar", "fact-rev", "fact-inv", "fact-cogs", "fact-ap"}


class TestTrendAnalysis:
    def test_deltas_and_growth_rates(self) -> None:
        result = trend_analysis(
            "revenue",
            [
                TrendPoint(
                    period="2024",
                    value=Decimal("8000"),
                    fact_refs=(FactRef(kind="CONFIRMED_FACT", ref_id="fact-rev-2024"),),
                ),
                TrendPoint(
                    period="2025",
                    value=Decimal("10000"),
                    fact_refs=(FactRef(kind="CONFIRMED_FACT", ref_id="fact-rev-2025"),),
                ),
            ],
        )
        (step,) = result.steps
        assert step.delta == ComputedOutcome(value=Decimal("2000.000000"))
        assert step.growth_rate == ComputedOutcome(value=Decimal("0.250000"))
        assert {ref.ref_id for ref in result.fact_refs} == {
            "fact-rev-2024",
            "fact-rev-2025",
        }

    def test_zero_base_growth_not_computable(self) -> None:
        result = trend_analysis(
            "net_profit",
            [
                TrendPoint(period="2024", value=Decimal(0)),
                TrendPoint(period="2025", value=Decimal("100")),
            ],
        )
        (step,) = result.steps
        assert step.delta == ComputedOutcome(value=Decimal("100.000000"))
        assert isinstance(step.growth_rate, NotComputableOutcome)

    def test_missing_period_value_not_computable(self) -> None:
        result = trend_analysis(
            "revenue",
            [
                TrendPoint(period="2024", value=None),
                TrendPoint(period="2025", value=Decimal("100")),
            ],
        )
        (step,) = result.steps
        assert isinstance(step.delta, NotComputableOutcome)
        assert "2024" in step.delta.reason

    def test_trend_is_deterministic(self) -> None:
        points = [
            TrendPoint(period="2023", value=Decimal("5")),
            TrendPoint(period="2024", value=Decimal("6")),
            TrendPoint(period="2025", value=Decimal("7")),
        ]
        assert trend_analysis("revenue", points) == trend_analysis("revenue", points)


class TestScenarioProjection:
    def test_named_downside_adjustment(self) -> None:
        result = scenario_projection(
            "revenue_down_20pct",
            [
                _input("revenue", "10000", "fact-rev"),
                _input("net_profit", "800", "fact-np"),
            ],
            [ScenarioAdjustment(metric="revenue", relative_change=Decimal("-0.2"))],
        )
        by_metric = {item.metric: item for item in result.metrics}
        assert by_metric["revenue"].adjusted == ComputedOutcome(
            value=Decimal("8000.000000")
        )
        # No probabilistic invention: unadjusted metrics pass through unchanged.
        assert by_metric["net_profit"].adjusted == ComputedOutcome(
            value=Decimal("800.000000")
        )
        assert {ref.ref_id for ref in result.fact_refs} == {"fact-rev", "fact-np"}

    def test_adjustment_for_unknown_metric_not_computable(self) -> None:
        result = scenario_projection(
            "bad_scenario",
            [_input("revenue", "10000", "fact-rev")],
            [ScenarioAdjustment(metric="ebitda", relative_change=Decimal("-0.1"))],
        )
        by_metric = {item.metric: item for item in result.metrics}
        assert isinstance(by_metric["ebitda"].adjusted, NotComputableOutcome)

    def test_scenario_is_deterministic(self) -> None:
        args = (
            "downside",
            [_input("revenue", "10000", "fact-rev")],
            [
                ScenarioAdjustment(
                    metric="revenue",
                    relative_change=Decimal("-0.15"),
                    absolute_change=Decimal("-50"),
                )
            ],
        )
        assert scenario_projection(*args) == scenario_projection(*args)
        by_metric = {
            item.metric: item for item in scenario_projection(*args).metrics
        }
        assert by_metric["revenue"].adjusted == ComputedOutcome(
            value=Decimal("8450.000000")
        )


# ---------------------------------------------------------------------------
# Stage-4 groups 3-5: working-capital need, financing gap, debt service,
# repayment capacity, downside scenarios.
#
# All fixtures below belong to the invented SME described in the module
# docstring.  Arithmetic is hand-computed in the comments so the expected
# Decimals are auditable without re-running the calculator.
# ---------------------------------------------------------------------------


def _base_wc_inputs() -> WorkingCapitalBaseInputs:
    # receivable 45d, inventory 60d, payable 30d; revenue 3,650,000 (=> 10,000/day
    # over 365 days); COGS ratio 0.70 (=> COGS 2,555,000, i.e. 7,000/day).
    return WorkingCapitalBaseInputs(
        inventory_days=_input("inventory_days", "60", "fact-inv-days"),
        receivable_days=_input("receivable_days", "45", "fact-rcv-days"),
        payable_days=_input("payable_days", "30", "fact-pay-days"),
        projected_revenue=_input("projected_revenue", "3650000", "fact-rev"),
        cogs_ratio=_input("cogs_ratio", "0.70", "fact-cogs-ratio"),
    )


def _funding_inputs() -> FinancingInputs:
    return FinancingInputs(
        own_funds=_input("own_funds", "200000", "fact-owc"),
        existing_facilities=_input("existing_facilities", "100000", "fact-fac"),
        requested_amount=_input("requested_amount", "400000", "fact-req"),
    )


class TestWorkingCapitalRequirement:
    def test_ccc_based_need_known_answer(self) -> None:
        base = _base_wc_inputs()
        result = working_capital_requirement(
            inventory_days=base.inventory_days,
            receivable_days=base.receivable_days,
            payable_days=base.payable_days,
            projected_revenue=base.projected_revenue,
            cogs_ratio=base.cogs_ratio,
        )
        # receivables = 45 * 3,650,000 / 365            = 450,000
        # inventory   = 60 * (3,650,000*0.70) / 365     = 420,000
        # payables    = 30 * (3,650,000*0.70) / 365     = 210,000
        # need        = 450,000 + 420,000 - 210,000     = 660,000
        # cash conversion days = 45 + 60 - 30           = 75
        assert isinstance(result, WorkingCapitalNeed)
        assert result.formula_version == "wc-need-v1"
        assert result.receivables == ComputedOutcome(value=Decimal("450000.000000"))
        assert result.inventory == ComputedOutcome(value=Decimal("420000.000000"))
        assert result.payables == ComputedOutcome(value=Decimal("210000.000000"))
        assert result.cash_conversion_days == ComputedOutcome(value=Decimal("75.000000"))
        assert result.outcome == ComputedOutcome(value=Decimal("660000.000000"))

    def test_need_equals_component_reconciliation(self) -> None:
        result = working_capital_requirement(
            inventory_days=_input("inventory_days", "60"),
            receivable_days=_input("receivable_days", "45"),
            payable_days=_input("payable_days", "30"),
            projected_revenue=_input("projected_revenue", "3650000"),
            cogs_ratio=_input("cogs_ratio", "0.70"),
        )
        assert isinstance(result.outcome, ComputedOutcome)
        assert isinstance(result.receivables, ComputedOutcome)
        assert isinstance(result.inventory, ComputedOutcome)
        assert isinstance(result.payables, ComputedOutcome)
        # need is exactly the sum of the quantized component balances.
        assert result.outcome.value == (
            result.receivables.value
            + result.inventory.value
            - result.payables.value
        )

    def test_missing_input_propagates_never_guesses(self) -> None:
        result = working_capital_requirement(
            inventory_days=_input("inventory_days", "60"),
            receivable_days=_input("receivable_days", "45"),
            payable_days=_input("payable_days", "30"),
            projected_revenue=_input("projected_revenue", None),
            cogs_ratio=_input("cogs_ratio", "0.70"),
        )
        assert isinstance(result.outcome, NotComputableOutcome)
        assert "projected_revenue" in result.outcome.reason
        # component outcomes are also explicitly not-computable, never 0.
        assert isinstance(result.receivables, NotComputableOutcome)
        assert isinstance(result.payables, NotComputableOutcome)

    def test_provenance_unions_input_fact_refs(self) -> None:
        base = _base_wc_inputs()
        result = working_capital_requirement(
            inventory_days=base.inventory_days,
            receivable_days=base.receivable_days,
            payable_days=base.payable_days,
            projected_revenue=base.projected_revenue,
            cogs_ratio=base.cogs_ratio,
        )
        assert {ref.ref_id for ref in result.fact_refs} == {
            "fact-inv-days",
            "fact-rcv-days",
            "fact-pay-days",
            "fact-rev",
            "fact-cogs-ratio",
        }

    def test_huge_values_stay_exact(self) -> None:
        # 1e12 revenue, all-COGS, 365-day receivables: receivables = revenue.
        result = working_capital_requirement(
            inventory_days=_input("inventory_days", "0"),
            receivable_days=_input("receivable_days", "365"),
            payable_days=_input("payable_days", "0"),
            projected_revenue=_input("projected_revenue", "1000000000000"),
            cogs_ratio=_input("cogs_ratio", "1"),
        )
        assert result.outcome == ComputedOutcome(value=Decimal("1000000000000.000000"))

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(CalculatorValidationError):
            working_capital_requirement(
                inventory_days=_input("inventory_days", "60"),
                receivable_days=_input("receivable_days", "45"),
                payable_days=_input("payable_days", "30"),
                projected_revenue=_input("projected_revenue", "-1"),
                cogs_ratio=_input("cogs_ratio", "0.70"),
            )


class TestFinancingGap:
    def _need(self) -> WorkingCapitalNeed:
        base = _base_wc_inputs()
        return working_capital_requirement(
            inventory_days=base.inventory_days,
            receivable_days=base.receivable_days,
            payable_days=base.payable_days,
            projected_revenue=base.projected_revenue,
            cogs_ratio=base.cogs_ratio,
        )

    def test_signed_difference_not_a_verdict(self) -> None:
        funding = _funding_inputs()
        result = financing_gap(
            self._need(),
            funding.own_funds,
            funding.existing_facilities,
            funding.requested_amount,
        )
        # implied external financing = 660,000 - 200,000 - 100,000 = 360,000
        # requested vs implied = 400,000 - 360,000 = +40,000 (requested exceeds
        # the implied need by 40,000 — a signed difference, not an approval).
        assert isinstance(result, FinancingGap)
        assert result.formula_version == "financing-gap-v1"
        assert result.implied_external_financing == ComputedOutcome(
            value=Decimal("360000.000000")
        )
        assert result.requested_vs_implied_difference == ComputedOutcome(
            value=Decimal("40000.000000")
        )

    def test_requested_below_implied_is_negative(self) -> None:
        result = financing_gap(
            self._need(),
            _input("own_funds", "0"),
            _input("existing_facilities", "0"),
            _input("requested_amount", "500000"),
        )
        # implied = 660,000; requested 500,000 => 500,000 - 660,000 = -160,000
        assert result.requested_vs_implied_difference == ComputedOutcome(
            value=Decimal("-160000.000000")
        )

    def test_need_not_computable_propagates(self) -> None:
        broken_need = working_capital_requirement(
            inventory_days=_input("inventory_days", None),
            receivable_days=_input("receivable_days", "45"),
            payable_days=_input("payable_days", "30"),
            projected_revenue=_input("projected_revenue", "3650000"),
            cogs_ratio=_input("cogs_ratio", "0.70"),
        )
        result = financing_gap(
            broken_need,
            _input("own_funds", "200000"),
            _input("existing_facilities", "100000"),
            _input("requested_amount", "400000"),
        )
        assert isinstance(result.implied_external_financing, NotComputableOutcome)
        assert isinstance(result.requested_vs_implied_difference, NotComputableOutcome)

    def test_missing_funding_input_propagates(self) -> None:
        result = financing_gap(
            self._need(),
            _input("own_funds", None),
            _input("existing_facilities", "100000"),
            _input("requested_amount", "400000"),
        )
        assert isinstance(result.implied_external_financing, NotComputableOutcome)
        assert "own_funds" in result.implied_external_financing.reason

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(CalculatorValidationError):
            financing_gap(
                self._need(),
                _input("own_funds", "-1"),
                _input("existing_facilities", "100000"),
                _input("requested_amount", "400000"),
            )


class TestDebtServiceSchedule:
    def test_equal_principal_reconciles_and_closes_to_zero(self) -> None:
        # principal 1,000; 12%/yr => 1%/month; 3 months; equal principal.
        # per instalment = round(1000/3, .01) = 333.33 (final absorbs remainder).
        # P1: bal 1000 -> interest 10.00, principal 333.33, close 666.67
        # P2: bal 666.67 -> interest 6.67, principal 333.33, close 333.34
        # P3: bal 333.34 -> interest 3.33, principal 333.34, close 0.00
        # sum principal = 1000.00 ; sum interest = 20.00
        result = debt_service_schedule(
            principal=_input("principal", "1000", "fact-principal"),
            annual_rate_percent=_input("annual_rate_percent", "12", "fact-rate"),
            term_months=_input("term_months", "3", "fact-term"),
            repayment_style="EQUAL_PRINCIPAL",
        )
        assert isinstance(result, DebtServiceSchedule)
        assert result.formula_version == "debt-service-v1"
        assert len(result.rows) == 3
        principals = [row.principal for row in result.rows]
        interests = [row.interest for row in result.rows]
        balances = [row.balance for row in result.rows]
        assert principals == [
            Decimal("333.33"),
            Decimal("333.33"),
            Decimal("333.34"),
        ]
        assert interests == [Decimal("10.00"), Decimal("6.67"), Decimal("3.33")]
        assert balances == [Decimal("666.67"), Decimal("333.34"), Decimal("0.00")]
        # Exact reconciliation: sum of principal rows == principal.
        assert result.total_principal == Decimal("1000")
        assert result.total_interest == Decimal("20.00")
        assert result.rows[-1].balance == Decimal("0")
        # Headline outcome is total debt service (principal + interest).
        assert result.outcome == ComputedOutcome(value=Decimal("1020.00"))

    def test_balloon_defers_principal_to_final_row(self) -> None:
        # interest-only 1% on 1000 for 2 months, principal in month 3.
        result = debt_service_schedule(
            principal=_input("principal", "1000"),
            annual_rate_percent=_input("annual_rate_percent", "12"),
            term_months=_input("term_months", "3"),
            repayment_style="BALLOON",
        )
        principals = [row.principal for row in result.rows]
        interests = [row.interest for row in result.rows]
        balances = [row.balance for row in result.rows]
        assert principals == [Decimal("0.00"), Decimal("0.00"), Decimal("1000.00")]
        assert interests == [Decimal("10.00"), Decimal("10.00"), Decimal("10.00")]
        assert balances == [Decimal("1000.00"), Decimal("1000.00"), Decimal("0.00")]
        assert result.total_principal == Decimal("1000")
        assert result.total_interest == Decimal("30.00")

    def test_zero_term_rejected(self) -> None:
        with pytest.raises(CalculatorValidationError):
            debt_service_schedule(
                principal=_input("principal", "1000"),
                annual_rate_percent=_input("annual_rate_percent", "12"),
                term_months=_input("term_months", "0"),
                repayment_style="EQUAL_PRINCIPAL",
            )

    def test_fractional_term_rejected(self) -> None:
        with pytest.raises(CalculatorValidationError):
            debt_service_schedule(
                principal=_input("principal", "1000"),
                annual_rate_percent=_input("annual_rate_percent", "12"),
                term_months=_input("term_months", "3.5"),
                repayment_style="EQUAL_PRINCIPAL",
            )

    def test_negative_principal_rejected(self) -> None:
        with pytest.raises(CalculatorValidationError):
            debt_service_schedule(
                principal=_input("principal", "-1000"),
                annual_rate_percent=_input("annual_rate_percent", "12"),
                term_months=_input("term_months", "3"),
                repayment_style="EQUAL_PRINCIPAL",
            )

    def test_missing_input_not_computable(self) -> None:
        result = debt_service_schedule(
            principal=_input("principal", None),
            annual_rate_percent=_input("annual_rate_percent", "12"),
            term_months=_input("term_months", "3"),
            repayment_style="EQUAL_PRINCIPAL",
        )
        assert isinstance(result.outcome, NotComputableOutcome)
        assert "principal" in result.outcome.reason
        assert result.rows == ()
        assert result.total_principal is None
        assert result.total_interest is None

    def test_large_principal_stays_exact(self) -> None:
        # 1e12 principal, equal principal, 4 months divides evenly.
        result = debt_service_schedule(
            principal=_input("principal", "1000000000000"),
            annual_rate_percent=_input("annual_rate_percent", "0"),
            term_months=_input("term_months", "4"),
            repayment_style="EQUAL_PRINCIPAL",
        )
        assert result.total_principal == Decimal("1000000000000")
        assert all(row.principal == Decimal("250000000000.00") for row in result.rows)
        assert result.rows[-1].balance == Decimal("0")


class TestRepaymentCapacity:
    def test_per_period_coverage_and_shortfall_count(self) -> None:
        # cashflow / debt service:
        #   500/400 = 1.25   (ok)
        #   300/400 = 0.75   (shortfall: cashflow < debt service)
        #   800/400 = 2.00   (ok)
        result = repayment_capacity(
            projected_operating_cashflow_by_period=[
                _input("2025Q1", "500", "fact-cf-1"),
                _input("2025Q2", "300", "fact-cf-2"),
                _input("2025Q3", "800", "fact-cf-3"),
            ],
            debt_service_by_period=[
                _input("2025Q1", "400", "fact-ds-1"),
                _input("2025Q2", "400", "fact-ds-2"),
                _input("2025Q3", "400", "fact-ds-3"),
            ],
        )
        assert isinstance(result, RepaymentCapacity)
        assert result.formula_version == "repayment-capacity-v1"
        coverage = [period.coverage_ratio for period in result.periods]
        assert coverage == [
            ComputedOutcome(value=Decimal("1.250000")),
            ComputedOutcome(value=Decimal("0.750000")),
            ComputedOutcome(value=Decimal("2.000000")),
        ]
        assert [period.shortfall for period in result.periods] == [False, True, False]
        assert result.shortfall_period_count == 1
        assert result.not_computable_period_count == 0

    def test_negative_cashflow_period_is_a_shortfall(self) -> None:
        result = repayment_capacity(
            projected_operating_cashflow_by_period=[_input("p1", "-50")],
            debt_service_by_period=[_input("p1", "100")],
        )
        (period,) = result.periods
        assert period.coverage_ratio == ComputedOutcome(value=Decimal("-0.500000"))
        assert period.shortfall is True
        assert result.shortfall_period_count == 1

    def test_zero_debt_service_not_computable_no_judgment(self) -> None:
        result = repayment_capacity(
            projected_operating_cashflow_by_period=[_input("p1", "500")],
            debt_service_by_period=[_input("p1", "0")],
        )
        (period,) = result.periods
        assert isinstance(period.coverage_ratio, NotComputableOutcome)
        assert period.shortfall is None
        assert result.not_computable_period_count == 1
        assert result.shortfall_period_count == 0

    def test_missing_cashflow_not_computable(self) -> None:
        result = repayment_capacity(
            projected_operating_cashflow_by_period=[_input("p1", None)],
            debt_service_by_period=[_input("p1", "100")],
        )
        (period,) = result.periods
        assert isinstance(period.coverage_ratio, NotComputableOutcome)
        assert period.shortfall is None

    def test_length_mismatch_rejected(self) -> None:
        with pytest.raises(CalculatorValidationError):
            repayment_capacity(
                projected_operating_cashflow_by_period=[_input("p1", "500")],
                debt_service_by_period=[
                    _input("p1", "400"),
                    _input("p2", "400"),
                ],
            )

    def test_negative_debt_service_rejected(self) -> None:
        with pytest.raises(CalculatorValidationError):
            repayment_capacity(
                projected_operating_cashflow_by_period=[_input("p1", "500")],
                debt_service_by_period=[_input("p1", "-400")],
            )


class TestDownsideScenarios:
    def test_recomputes_need_and_gap_per_scenario(self) -> None:
        result = downside_scenarios(
            _base_wc_inputs(),
            [
                ScenarioSpec(label="base_repeat"),
                ScenarioSpec(
                    label="revenue_down_cost_up_receivables_stretched",
                    revenue_down_pct=Decimal("0.10"),
                    cost_up_pct=Decimal("0.10"),
                    receivable_days_up=Decimal("15"),
                ),
            ],
            funding=_funding_inputs(),
        )
        assert isinstance(result, DownsideScenarioSet)
        assert result.formula_version == "downside-scenarios-v1"
        assert result.base_need.outcome == ComputedOutcome(value=Decimal("660000.000000"))
        assert result.base_financing_gap is not None
        by_label = {scenario.label: scenario for scenario in result.scenarios}

        # zero-shock scenario reproduces the base need exactly.
        assert by_label["base_repeat"].need.outcome == ComputedOutcome(
            value=Decimal("660000.000000")
        )

        # downside scenario:
        #   revenue 3,650,000*0.90 = 3,285,000 (=> 9,000/day)
        #   cogs ratio 0.70*1.10   = 0.77      (=> COGS 2,529,450 => 6,930/day)
        #   receivable days 45+15  = 60
        #   receivables = 60*9,000        = 540,000
        #   inventory   = 60*6,930        = 415,800
        #   payables    = 30*6,930        = 207,900
        #   need        = 540,000+415,800-207,900 = 747,900
        downside = by_label["revenue_down_cost_up_receivables_stretched"]
        assert downside.need.outcome == ComputedOutcome(value=Decimal("747900.000000"))
        assert downside.financing_gap is not None
        # implied external = 747,900 - 200,000 - 100,000 = 447,900
        # requested vs implied = 400,000 - 447,900 = -47,900 (gap widens)
        assert downside.financing_gap.implied_external_financing == ComputedOutcome(
            value=Decimal("447900.000000")
        )
        assert downside.financing_gap.requested_vs_implied_difference == ComputedOutcome(
            value=Decimal("-47900.000000")
        )

    def test_specs_are_data_and_labelled(self) -> None:
        spec = ScenarioSpec(label="stress", revenue_down_pct=Decimal("0.2"))
        result = downside_scenarios(_base_wc_inputs(), [spec])
        assert result.scenarios[0].spec == spec
        assert result.scenarios[0].label == "stress"
        # no funding supplied => no financing gap computed.
        assert result.base_financing_gap is None
        assert result.scenarios[0].financing_gap is None

    def test_scenario_shock_out_of_range_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            ScenarioSpec(label="impossible", revenue_down_pct=Decimal("1.5"))

    def test_missing_base_input_propagates_through_scenarios(self) -> None:
        base = WorkingCapitalBaseInputs(
            inventory_days=_input("inventory_days", "60"),
            receivable_days=_input("receivable_days", "45"),
            payable_days=_input("payable_days", "30"),
            projected_revenue=_input("projected_revenue", None),
            cogs_ratio=_input("cogs_ratio", "0.70"),
        )
        result = downside_scenarios(base, [ScenarioSpec(label="s1")])
        assert isinstance(result.base_need.outcome, NotComputableOutcome)
        assert isinstance(result.scenarios[0].need.outcome, NotComputableOutcome)

    def test_deterministic(self) -> None:
        args = (_base_wc_inputs(), [ScenarioSpec(label="s", revenue_down_pct=Decimal("0.1"))])
        first = downside_scenarios(*args, funding=_funding_inputs())
        second = downside_scenarios(*args, funding=_funding_inputs())
        assert first == second
        assert first.result_id == second.result_id
