"""Contract tests for the Postgres post-credit-monitoring adapter.

Mirrors ``tests/contract/postgres/test_conditions_adapter.py``: a fake connection
captures the exact SQL and parameters the adapter issues, proving each write is
ONE transaction (row + audit, plus the rule-raised alert + its audit when a
deterministic rule fires), that the alert insert is deduped
(``on conflict do nothing`` -> no alert when the source already has one), and that
a forbidden alert edge raises before any write -- all without a live Postgres.
All identifiers are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from uuid import UUID

import pytest

from creditops.application.ports.monitoring import (
    AlertNotFound,
    ForbiddenAlertTransition,
)
from creditops.domain.monitoring import (
    AlertRule,
    AlertStatus,
    ComparisonOperator,
    Covenant,
    CovenantThreshold,
    EarlyWarningAlert,
    GeneratedObligation,
    MonitoringObservation,
    ObligationFrequency,
    ObligationSpec,
    evaluate_covenant,
)
from creditops.infrastructure.postgres.monitoring import PostgresMonitoringRepository

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
OBLIGATION = UUID("b0000000-0000-0000-0000-0000000000f1")
OBSERVATION = UUID("c0000000-0000-0000-0000-0000000000f1")
COVENANT = UUID("d0000000-0000-0000-0000-0000000000f1")
COVENANT_TEST = UUID("e0000000-0000-0000-0000-0000000000f1")
ALERT = UUID("f0000000-0000-0000-0000-0000000000f1")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REVIEWER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CASE_VERSION = 2
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class Transaction(AbstractAsyncContextManager[None]):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        self._connection.transaction_depth += 1
        self._connection.transactions_opened += 1

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.transaction_depth -= 1


class Connection:
    def __init__(self, results: list[list[tuple[object, ...]]] | None = None) -> None:
        self.results = list(results or [])
        self.queries: list[str] = []
        self.params: list[tuple[object, ...] | None] = []
        self.transaction_depth = 0
        self.transactions_opened = 0
        self.executed_in_transaction: list[bool] = []

    def transaction(self) -> Transaction:
        return Transaction(self)

    async def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> Cursor:
        self.queries.append(query)
        self.params.append(params)
        self.executed_in_transaction.append(self.transaction_depth > 0)
        return Cursor(self.results.pop(0) if self.results else [])


class ConnectionContext(AbstractAsyncContextManager[Connection]):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> Connection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _repo(connection: Connection) -> PostgresMonitoringRepository:
    return PostgresMonitoringRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _observation(obligation_id: UUID | None = OBLIGATION) -> MonitoringObservation:
    return MonitoringObservation(
        id=OBSERVATION,
        case_id=CASE,
        case_version=CASE_VERSION,
        obligation_id=obligation_id,
        observation_type_vi="Báo cáo tài chính",
        body_vi="Quan sát (mô phỏng).",
        effective_at=datetime(2026, 3, 1, tzinfo=UTC),
        observed_at=datetime(2026, 3, 5, tzinfo=UTC),
    )


def _overdue_alert() -> EarlyWarningAlert:
    return EarlyWarningAlert(
        id=ALERT,
        case_id=CASE,
        case_version=CASE_VERSION,
        rule=AlertRule.OVERDUE_OBLIGATION,
        detail_vi="Quá hạn (mô phỏng).",
        source_obligation_id=OBLIGATION,
        source_observation_id=OBSERVATION,
    )


def _breach_alert() -> EarlyWarningAlert:
    return EarlyWarningAlert(
        id=ALERT,
        case_id=CASE,
        case_version=CASE_VERSION,
        rule=AlertRule.COVENANT_BREACH,
        detail_vi="Vi phạm (mô phỏng).",
        source_covenant_test_id=COVENANT_TEST,
    )


def _threshold() -> CovenantThreshold:
    return CovenantThreshold(
        metric_key="DSCR",
        operator=ComparisonOperator.GTE,
        threshold_value=Decimal("1.20"),
        threshold_version=1,
    )


def _alert_row(status: AlertStatus) -> tuple[object, ...]:
    return (
        ALERT,
        CASE,
        CASE_VERSION,
        AlertRule.COVENANT_BREACH.value,
        status.value,
        "Vi phạm (mô phỏng).",
        COVENANT_TEST,
        None,
        None,
        NOW,
    )


# -- obligations --------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_obligations_is_one_transaction_with_audit() -> None:
    # obligation insert -> id/created_at; audit -> none.
    connection = Connection(results=[[(OBLIGATION, NOW)], []])
    repo = _repo(connection)

    created = await repo.create_obligations(
        case_id=CASE,
        case_version=CASE_VERSION,
        spec=ObligationSpec(
            frequency=ObligationFrequency.MONTHLY, requirement_text_vi="Báo cáo."
        ),
        obligations=(
            GeneratedObligation(
                sequence=1,
                due_date=date(2026, 2, 28),
                frequency=ObligationFrequency.MONTHLY,
                requirement_text_vi="Báo cáo.",
            ),
        ),
        actor_id=OFFICER,
        actor_role="MONITORING_OFFICER",
    )

    assert created[0].id == OBLIGATION
    sql = _sql(connection)
    assert "insert into public.monitoring_obligations" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


# -- observations -------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_time_observation_writes_only_observation_and_audit() -> None:
    # observation insert -> recorded_at; audit -> none.  No alert passed.
    connection = Connection(results=[[(NOW,)], []])
    repo = _repo(connection)

    recorded, alert = await repo.record_observation(
        observation=_observation(),
        overdue_alert=None,
        actor_id=OFFICER,
        actor_role="MONITORING_OFFICER",
    )

    assert alert is None
    assert recorded.recorded_at == NOW
    sql = _sql(connection)
    assert "insert into public.monitoring_observations" in sql
    assert "insert into public.early_warning_alerts" not in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_late_observation_raises_alert_in_same_transaction() -> None:
    # observation insert; observation audit; alert insert -> created_at; alert audit.
    connection = Connection(results=[[(NOW,)], [], [(NOW,)], []])
    repo = _repo(connection)

    recorded, alert = await repo.record_observation(
        observation=_observation(),
        overdue_alert=_overdue_alert(),
        actor_id=OFFICER,
        actor_role="MONITORING_OFFICER",
    )

    assert alert is not None
    assert alert.rule is AlertRule.OVERDUE_OBLIGATION
    sql = _sql(connection)
    assert "insert into public.early_warning_alerts" in sql
    assert "on conflict" in sql
    # observation + its audit + alert + alert audit are ONE transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


@pytest.mark.asyncio
async def test_overdue_alert_dedup_returns_none_on_conflict() -> None:
    # observation insert; audit; alert insert -> NO row (on conflict do nothing).
    connection = Connection(results=[[(NOW,)], [], []])
    repo = _repo(connection)

    recorded, alert = await repo.record_observation(
        observation=_observation(),
        overdue_alert=_overdue_alert(),
        actor_id=OFFICER,
        actor_role="MONITORING_OFFICER",
    )

    assert alert is None  # deduped: the obligation already carries an alert
    # No alert audit event is written when nothing was raised.
    alert_audits = [
        p
        for q, p in zip(connection.queries, connection.params, strict=True)
        if "audit_events" in q.lower()
        and p is not None
        and "EARLY_WARNING_ALERT_RAISED" in p
    ]
    assert alert_audits == []


# -- covenants ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_covenant_persists_threshold_and_audit() -> None:
    connection = Connection(results=[[(NOW,)], []])
    repo = _repo(connection)

    covenant = Covenant(
        id=COVENANT,
        case_id=CASE,
        case_version=CASE_VERSION,
        name_vi="Hệ số bao phủ nợ.",
        threshold=_threshold(),
    )
    recorded = await repo.create_covenant(
        covenant=covenant, actor_id=OFFICER, actor_role="MONITORING_OFFICER"
    )

    assert recorded.threshold_value == Decimal("1.20")
    assert recorded.operator is ComparisonOperator.GTE
    sql = _sql(connection)
    assert "insert into public.covenants" in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_passing_covenant_test_writes_no_alert() -> None:
    connection = Connection(results=[[(NOW,)], []])
    repo = _repo(connection)

    evaluation = evaluate_covenant(Decimal("1000"), Decimal("800"), _threshold())
    test, alert = await repo.record_covenant_test(
        test_id=COVENANT_TEST,
        covenant_id=COVENANT,
        case_id=CASE,
        case_version=CASE_VERSION,
        evaluation=evaluation,
        breach_alert=None,
        actor_id=OFFICER,
        actor_role="MONITORING_OFFICER",
    )

    assert test.passed is True
    assert alert is None
    sql = _sql(connection)
    assert "insert into public.covenant_tests" in sql
    assert "insert into public.early_warning_alerts" not in sql


@pytest.mark.asyncio
async def test_failing_covenant_test_raises_alert_in_same_transaction() -> None:
    # test insert; test audit; alert insert -> created_at; alert audit.
    connection = Connection(results=[[(NOW,)], [], [(NOW,)], []])
    repo = _repo(connection)

    evaluation = evaluate_covenant(Decimal("1000"), Decimal("900"), _threshold())
    test, alert = await repo.record_covenant_test(
        test_id=COVENANT_TEST,
        covenant_id=COVENANT,
        case_id=CASE,
        case_version=CASE_VERSION,
        evaluation=evaluation,
        breach_alert=_breach_alert(),
        actor_id=OFFICER,
        actor_role="MONITORING_OFFICER",
    )

    assert test.passed is False
    assert alert is not None
    assert alert.rule is AlertRule.COVENANT_BREACH
    sql = _sql(connection)
    assert "insert into public.covenant_tests" in sql
    assert "insert into public.early_warning_alerts" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


# -- alert disposition --------------------------------------------------------


@pytest.mark.asyncio
async def test_dispose_alert_is_one_transaction_select_update_disposition_audit() -> None:
    # select-for-update -> OPEN alert; update/disposition/audit -> none.
    connection = Connection(results=[[_alert_row(AlertStatus.OPEN)], [], [], []])
    repo = _repo(connection)

    updated = await repo.dispose_alert(
        alert_id=ALERT,
        case_id=CASE,
        case_version=CASE_VERSION,
        to_status=AlertStatus.ESCALATED,
        rationale_vi="Chuyển cấp (mô phỏng).",
        actor_id=REVIEWER,
        actor_role="MONITORING_REVIEWER",
    )

    assert updated.status is AlertStatus.ESCALATED
    sql = _sql(connection)
    assert "select" in sql and "for update" in sql
    assert "update public.early_warning_alerts" in sql
    assert "insert into public.alert_dispositions" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    # The disposition records the exact from/to pair and the mandatory rationale.
    disp_index = next(
        i for i, q in enumerate(connection.queries) if "alert_dispositions" in q.lower()
    )
    disp_params = connection.params[disp_index]
    assert disp_params is not None
    assert disp_params[1] == "OPEN"  # from_status
    assert disp_params[2] == "ESCALATED"  # to_status
    assert disp_params[3] == "Chuyển cấp (mô phỏng)."


@pytest.mark.asyncio
async def test_dispose_forbidden_edge_raises_and_writes_nothing() -> None:
    # DISMISSED_BY_HUMAN is terminal; ESCALATED is forbidden.
    connection = Connection(results=[[_alert_row(AlertStatus.DISMISSED_BY_HUMAN)]])
    repo = _repo(connection)

    with pytest.raises(ForbiddenAlertTransition):
        await repo.dispose_alert(
            alert_id=ALERT,
            case_id=CASE,
            case_version=CASE_VERSION,
            to_status=AlertStatus.ESCALATED,
            rationale_vi="x",
            actor_id=REVIEWER,
            actor_role="MONITORING_REVIEWER",
        )

    sql = _sql(connection)
    assert "update public.early_warning_alerts" not in sql
    assert "insert into public.alert_dispositions" not in sql


@pytest.mark.asyncio
async def test_dispose_missing_alert_raises_not_found() -> None:
    connection = Connection(results=[[]])  # select-for-update -> no row
    repo = _repo(connection)

    with pytest.raises(AlertNotFound):
        await repo.dispose_alert(
            alert_id=ALERT,
            case_id=CASE,
            case_version=CASE_VERSION,
            to_status=AlertStatus.ACKNOWLEDGED,
            rationale_vi="x",
            actor_id=REVIEWER,
            actor_role="MONITORING_REVIEWER",
        )
