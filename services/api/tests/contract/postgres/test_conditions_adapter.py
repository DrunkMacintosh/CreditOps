"""Contract tests for the Postgres disbursement-condition-ledger adapter.

Mirrors ``tests/contract/postgres/test_credit_decisions_adapter.py``: a fake
connection captures the exact SQL and parameters the adapter issues, proving
each write is ONE transaction (row + append-only status event + HUMAN audit),
that a transition is a single select-for-update + update + event + audit
transaction, and that a forbidden edge raises before any write -- all without a
live Postgres.  All identifiers are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

import pytest

from creditops.application.ports.conditions import (
    ConditionNotFound,
    ForbiddenConditionTransition,
)
from creditops.domain.conditions import ConditionStatus, DisbursementCondition
from creditops.infrastructure.postgres.conditions import (
    PostgresConditionLedgerRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
DECISION = UUID("d0000000-0000-0000-0000-0000000000f1")
CONDITION = UUID("c0000000-0000-0000-0000-0000000000f1")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CHECKER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CASE_VERSION = 3
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


def _repo(connection: Connection) -> PostgresConditionLedgerRepository:
    return PostgresConditionLedgerRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _condition(status: ConditionStatus = ConditionStatus.PENDING) -> DisbursementCondition:
    return DisbursementCondition(
        id=CONDITION,
        case_id=CASE,
        case_version=CASE_VERSION,
        decision_id=DECISION,
        condition_text_vi="Hợp đồng bảo đảm đã ký (mô phỏng).",
        owner_vi="Chuyên viên vận hành",
        status=status,
    )


def _current_row(
    status: ConditionStatus,
    evidence: list[str] | None = None,
) -> tuple[object, ...]:
    return (
        CONDITION,
        CASE,
        CASE_VERSION,
        DECISION,
        "Hợp đồng bảo đảm đã ký (mô phỏng).",
        "Chuyên viên vận hành",
        None,
        status.value,
        evidence or [],
        NOW,
    )


@pytest.mark.asyncio
async def test_create_condition_is_one_transaction_with_event_and_audit() -> None:
    # insert -> created_at; event insert -> none; audit insert -> none.
    connection = Connection(results=[[(NOW,)], [], []])
    repo = _repo(connection)

    recorded = await repo.create_condition(
        condition=_condition(), actor_id=OFFICER, actor_role="OPS_OFFICER"
    )

    assert recorded.id == CONDITION
    assert recorded.status is ConditionStatus.PENDING
    assert recorded.created_at == NOW

    sql = _sql(connection)
    assert "insert into public.disbursement_conditions" in sql
    assert "insert into public.condition_status_events" in sql
    assert "insert into public.audit_events" in sql
    # The whole create runs inside exactly one transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    # The creation status event is from_status null -> PENDING.
    event_index = next(
        i for i, q in enumerate(connection.queries) if "condition_status_events" in q.lower()
    )
    event_params = connection.params[event_index]
    assert event_params is not None
    assert event_params[1] is None  # from_status
    assert event_params[2] == "PENDING"  # to_status
    # The audit actor type is the acting human role.
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    audit_params = connection.params[audit_index]
    assert audit_params is not None
    assert "HUMAN:OPS_OFFICER" in audit_params


@pytest.mark.asyncio
async def test_transition_is_one_transaction_select_update_event_audit() -> None:
    # select-for-update -> current row (EVIDENCE_SUBMITTED); update/event/audit -> none.
    connection = Connection(
        results=[[_current_row(ConditionStatus.EVIDENCE_SUBMITTED)], [], [], []]
    )
    repo = _repo(connection)

    updated = await repo.transition_condition(
        condition_id=CONDITION,
        case_id=CASE,
        case_version=CASE_VERSION,
        to_status=ConditionStatus.VERIFIED,
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
        rationale_vi=None,
        evidence_refs=None,
    )

    assert updated.status is ConditionStatus.VERIFIED

    sql = _sql(connection)
    assert "select" in sql and "for update" in sql
    assert "update public.disbursement_conditions" in sql
    assert "insert into public.condition_status_events" in sql
    assert "insert into public.audit_events" in sql
    # Row update + status event + audit are ONE transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    # The status event records the exact from/to pair and the verifier.
    event_index = next(
        i for i, q in enumerate(connection.queries) if "condition_status_events" in q.lower()
    )
    event_params = connection.params[event_index]
    assert event_params is not None
    assert event_params[1] == "EVIDENCE_SUBMITTED"  # from_status
    assert event_params[2] == "VERIFIED"  # to_status
    assert CHECKER in event_params


@pytest.mark.asyncio
async def test_transition_forbidden_edge_raises_and_writes_nothing() -> None:
    # select-for-update -> current row VERIFIED; a VERIFIED -> FAILED edge is
    # forbidden, so the adapter raises before any update/event/audit.
    connection = Connection(results=[[_current_row(ConditionStatus.VERIFIED)]])
    repo = _repo(connection)

    with pytest.raises(ForbiddenConditionTransition):
        await repo.transition_condition(
            condition_id=CONDITION,
            case_id=CASE,
            case_version=CASE_VERSION,
            to_status=ConditionStatus.FAILED,
            actor_id=CHECKER,
            actor_role="OPS_CHECKER",
            rationale_vi=None,
            evidence_refs=None,
        )

    sql = _sql(connection)
    assert "update public.disbursement_conditions" not in sql
    assert "insert into public.condition_status_events" not in sql
    assert "insert into public.audit_events" not in sql


@pytest.mark.asyncio
async def test_transition_missing_condition_raises_not_found() -> None:
    connection = Connection(results=[[]])  # select-for-update -> no row
    repo = _repo(connection)

    with pytest.raises(ConditionNotFound):
        await repo.transition_condition(
            condition_id=CONDITION,
            case_id=CASE,
            case_version=CASE_VERSION,
            to_status=ConditionStatus.EVIDENCE_SUBMITTED,
            actor_id=OFFICER,
            actor_role="OPS_OFFICER",
            rationale_vi=None,
            evidence_refs=None,
        )


@pytest.mark.asyncio
async def test_transition_preserves_evidence_when_not_provided() -> None:
    connection = Connection(
        results=[
            [_current_row(ConditionStatus.EVIDENCE_SUBMITTED, evidence=["ref-1"])],
            [],
            [],
            [],
        ]
    )
    repo = _repo(connection)

    updated = await repo.transition_condition(
        condition_id=CONDITION,
        case_id=CASE,
        case_version=CASE_VERSION,
        to_status=ConditionStatus.VERIFIED,
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
        rationale_vi=None,
        evidence_refs=None,
    )

    assert updated.evidence_refs == ("ref-1",)


@pytest.mark.asyncio
async def test_list_verifying_actor_ids_reads_verified_events() -> None:
    connection = Connection(results=[[(CHECKER,), (OFFICER,)]])
    repo = _repo(connection)

    actors = await repo.list_verifying_actor_ids(CASE, CASE_VERSION)

    assert actors == frozenset({CHECKER, OFFICER})
    sql = _sql(connection)
    assert "condition_status_events" in sql
    assert "to_status = 'verified'" in sql
