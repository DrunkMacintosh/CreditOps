"""Deterministic-calculator tests for the Credit Underwriting Agent.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  Figures below
belong to the invented SME "Cong ty TNHH Banh Trang Trang Bom Demo" and have no
relation to any real company.
"""

from __future__ import annotations

from decimal import Decimal

from creditops.application.underwriting.calculators import (
    CalculatorInput,
    ComputedOutcome,
    FactRef,
    NotComputableOutcome,
    ScenarioAdjustment,
    TrendPoint,
    asset_turnover,
    cash_conversion_cycle,
    current_ratio,
    debt_to_assets,
    debt_to_equity,
    gross_margin,
    inventory_days,
    net_margin,
    operating_margin,
    payable_days,
    quick_ratio,
    receivable_days,
    return_on_assets,
    return_on_equity,
    scenario_projection,
    trend_analysis,
    working_capital_gap,
    working_capital_need,
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
