"""Contract tests for the Postgres human-credit-decision persistence adapter.

Mirrors ``tests/contract/postgres/test_intake_adapter.py``: a fake connection
captures the exact SQL and parameters the adapter issues, proving the single
transaction (decision + optional snapshot + ``HUMAN:CREDIT_APPROVER`` audit),
the on-conflict idempotency, and that the idempotent repeat writes no second
snapshot or audit -- all without a live Postgres.  All identifiers are
synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.domain.credit_decisions import (
    ApprovedTerms,
    CreditDecisionType,
    HumanCreditDecision,
    build_term_snapshot,
)
from creditops.infrastructure.postgres.credit_decisions import (
    PostgresCreditDecisionRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000e1")
DECIDER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_VERSION = 3
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _decision(
    *,
    decision: CreditDecisionType = CreditDecisionType.APPROVED_WITH_CONDITIONS,
    conditions: tuple[str, ...] = ("Bổ sung bảo đảm (mô phỏng).",),
) -> HumanCreditDecision:
    return HumanCreditDecision(
        id=uuid4(),
        case_id=CASE,
        case_version=CASE_VERSION,
        decision=decision,
        rationale_vi="Phê duyệt có điều kiện (dữ liệu mô phỏng).",
        decided_by=DECIDER,
        decided_by_role="CREDIT_APPROVER",
        conditions=conditions,
    )


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


def _repo(connection: Connection) -> PostgresCreditDecisionRepository:
    return PostgresCreditDecisionRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _decision_row(decision: HumanCreditDecision) -> tuple[object, ...]:
    return (
        decision.id,
        decision.case_id,
        decision.case_version,
        decision.decision.value,
        decision.rationale_vi,
        decision.decided_by,
        decision.decided_by_role,
        None,
        None,
        None,
        list(decision.conditions),
        NOW,
    )


@pytest.mark.asyncio
async def test_record_decision_with_snapshot_is_one_transaction_with_audit() -> None:
    decision = _decision()
    snapshot = build_term_snapshot(
        snapshot_id=uuid4(),
        decision=decision,
        terms=ApprovedTerms(amount=Decimal("5000000000"), currency="VND"),
    )
    # decision insert -> inserted; snapshot insert -> created_at; audit -> none.
    connection = Connection(results=[[(decision.id, NOW)], [(NOW,)], []])
    repo = _repo(connection)

    recorded = await repo.record_decision(decision=decision, snapshot=snapshot)

    assert recorded.created is True
    assert recorded.snapshot is not None
    assert recorded.snapshot.snapshot_hash == snapshot.snapshot_hash

    sql = _sql(connection)
    assert "insert into public.human_credit_decisions" in sql
    assert "on conflict (case_id, case_version) do nothing" in sql
    assert "insert into public.approved_term_snapshots" in sql
    assert "insert into public.audit_events" in sql
    # The whole record runs inside exactly one transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    # The audit event is a HUMAN:CREDIT_APPROVER actor whose id is the decider.
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    audit_params = connection.params[audit_index]
    assert audit_params is not None
    assert "HUMAN:CREDIT_APPROVER" in audit_params
    assert DECIDER in audit_params
    assert decision.id in audit_params


@pytest.mark.asyncio
async def test_record_decision_without_terms_writes_no_snapshot() -> None:
    decision = _decision(
        decision=CreditDecisionType.APPROVED_AS_PROPOSED, conditions=()
    )
    # decision insert -> inserted; audit -> none.
    connection = Connection(results=[[(decision.id, NOW)], []])
    repo = _repo(connection)

    recorded = await repo.record_decision(decision=decision, snapshot=None)

    assert recorded.created is True
    assert recorded.snapshot is None
    sql = _sql(connection)
    assert "insert into public.approved_term_snapshots" not in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_record_decision_is_idempotent_on_existing_version() -> None:
    decision = _decision(
        decision=CreditDecisionType.APPROVED_AS_PROPOSED, conditions=()
    )
    snapshot = build_term_snapshot(
        snapshot_id=uuid4(), decision=decision, terms=ApprovedTerms(currency="VND")
    )
    # decision insert -> conflict (no row); reselect decision -> row; snapshot -> none.
    connection = Connection(results=[[], [_decision_row(decision)], []])
    repo = _repo(connection)

    recorded = await repo.record_decision(decision=decision, snapshot=snapshot)

    assert recorded.created is False
    assert recorded.id == decision.id
    sql = _sql(connection)
    # No second snapshot and no second audit on the idempotent repeat.
    assert "insert into public.approved_term_snapshots" not in sql
    assert "insert into public.audit_events" not in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_load_decision_binding_reads_case_and_latest_artifacts() -> None:
    memo_id, risk_id, uw_id = uuid4(), uuid4(), uuid4()
    connection = Connection(
        results=[[(2,)], [(memo_id,)], [(risk_id,)], [(uw_id,)]]
    )
    repo = _repo(connection)

    binding = await repo.load_decision_binding(CASE)

    assert binding is not None
    assert binding.current_case_version == 2
    assert binding.latest_memo_artifact_id == memo_id
    assert binding.latest_risk_assessment_id == risk_id
    assert binding.latest_underwriting_assessment_id == uw_id
    sql = _sql(connection)
    assert "from public.credit_cases" in sql
    assert "from public.credit_ops_packages" in sql
    assert "from public.risk_review_assessments" in sql
    assert "from public.underwriting_assessments" in sql


@pytest.mark.asyncio
async def test_load_decision_binding_returns_none_for_unknown_case() -> None:
    connection = Connection(results=[[]])
    repo = _repo(connection)

    assert await repo.load_decision_binding(CASE) is None
