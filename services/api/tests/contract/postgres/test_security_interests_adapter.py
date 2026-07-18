"""Contract tests for the Postgres security-perfection persistence adapter.

Mirrors ``tests/contract/postgres/test_credit_decisions_adapter.py``: a fake
connection captures the exact SQL and parameters the adapter issues, proving each
write is ONE transaction with a ``HUMAN:<role>`` audit row, that ``transition_item``
validates the closed graph + the ``COMPLETED`` evidence rule before writing, and
that ``list_interests`` groups items under their interest -- all without a live
Postgres.  All identifiers are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.security_interests import (
    ForbiddenTransitionError,
    InterestNotAccessibleError,
    InvalidTransitionInputError,
    ItemNotAccessibleError,
)
from creditops.domain.security_interests import (
    PerfectionStatus,
    SecurityAssetKind,
    SecurityInterest,
    SecurityPerfectionItem,
)
from creditops.infrastructure.postgres.security_interests import (
    PostgresSecurityInterestRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
INTEREST = UUID("20000000-0000-0000-0000-0000000000f1")
ITEM = UUID("30000000-0000-0000-0000-0000000000f1")
ACTOR = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
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


def _repo(connection: Connection) -> PostgresSecurityInterestRepository:
    return PostgresSecurityInterestRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _interest() -> SecurityInterest:
    return SecurityInterest(
        id=INTEREST,
        case_id=CASE,
        case_version=CASE_VERSION,
        asset_description_vi="Quyền sử dụng đất (mô phỏng).",
        asset_kind=SecurityAssetKind.REAL_ESTATE,
        owner_name_vi="Bên bảo đảm Demo",
        valuation_reference="valuation-adapter://demo/1",
        created_by=ACTOR,
    )


def _item_row(
    *,
    status: str,
    evidence: list[str],
    completed_by: UUID | None = None,
    completed_at: datetime | None = None,
) -> tuple[object, ...]:
    return (
        ITEM,
        INTEREST,
        "Đăng ký biện pháp bảo đảm (mô phỏng).",
        status,
        evidence,
        None,
        None,
        None,
        completed_by,
        completed_at,
        NOW,
    )


@pytest.mark.asyncio
async def test_create_interest_is_one_transaction_with_audit() -> None:
    connection = Connection(results=[[(NOW,)], []])
    repo = _repo(connection)

    recorded = await repo.create_interest(
        interest=_interest(), actor_role="LEGAL_REVIEWER"
    )

    assert recorded.id == INTEREST
    assert recorded.created_at == NOW
    sql = _sql(connection)
    assert "insert into public.security_interests" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    audit_params = connection.params[audit_index]
    assert audit_params is not None
    assert "HUMAN:LEGAL_REVIEWER" in audit_params
    assert ACTOR in audit_params


@pytest.mark.asyncio
async def test_add_item_scopes_to_case_and_audits() -> None:
    # locate interest -> (case_id, case_version); insert item -> created_at; audit.
    connection = Connection(results=[[(CASE, CASE_VERSION)], [(NOW,)], []])
    repo = _repo(connection)
    item = SecurityPerfectionItem(
        id=ITEM,
        interest_id=INTEREST,
        requirement_vi="Đăng ký (mô phỏng).",
        status=PerfectionStatus.PENDING,
    )

    recorded = await repo.add_item(
        case_id=CASE, item=item, actor_id=ACTOR, actor_role="OPS_OFFICER"
    )

    assert recorded.status == "PENDING"
    sql = _sql(connection)
    assert "from public.security_interests" in sql
    assert "insert into public.security_perfection_items" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_add_item_unknown_interest_raises() -> None:
    connection = Connection(results=[[]])
    repo = _repo(connection)
    item = SecurityPerfectionItem(
        id=ITEM, interest_id=INTEREST, requirement_vi="X (mô phỏng)."
    )

    with pytest.raises(InterestNotAccessibleError):
        await repo.add_item(
            case_id=CASE, item=item, actor_id=ACTOR, actor_role="OPS_OFFICER"
        )
    assert "insert into public.security_perfection_items" not in _sql(connection)


@pytest.mark.asyncio
async def test_transition_evidence_attached_updates_and_audits() -> None:
    # current row PENDING, empty evidence, case scope; update -> full row; audit.
    connection = Connection(
        results=[
            [("PENDING", [], CASE, CASE_VERSION)],
            [_item_row(status="EVIDENCE_ATTACHED", evidence=["storage://demo/e1"])],
            [],
        ]
    )
    repo = _repo(connection)

    recorded = await repo.transition_item(
        case_id=CASE,
        item_id=ITEM,
        to_status=PerfectionStatus.EVIDENCE_ATTACHED,
        actor_id=ACTOR,
        actor_role="LEGAL_REVIEWER",
        rationale=None,
        evidence_refs=("storage://demo/e1",),
        filing_reference=None,
        effective_date=None,
        expiry_date=None,
    )

    assert recorded.status == "EVIDENCE_ATTACHED"
    sql = _sql(connection)
    assert "update public.security_perfection_items" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_transition_forbidden_graph_move_raises_before_update() -> None:
    # PENDING -> COMPLETED is not in the graph.
    connection = Connection(results=[[("PENDING", [], CASE, CASE_VERSION)]])
    repo = _repo(connection)

    with pytest.raises(ForbiddenTransitionError):
        await repo.transition_item(
            case_id=CASE,
            item_id=ITEM,
            to_status=PerfectionStatus.COMPLETED,
            actor_id=ACTOR,
            actor_role="LEGAL_REVIEWER",
            rationale=None,
            evidence_refs=("storage://demo/e1",),
            filing_reference=None,
            effective_date=None,
            expiry_date=None,
        )
    assert "update public.security_perfection_items" not in _sql(connection)


@pytest.mark.asyncio
async def test_transition_completed_without_any_evidence_raises() -> None:
    # EVIDENCE_ATTACHED -> COMPLETED but neither existing nor new evidence.
    connection = Connection(results=[[("EVIDENCE_ATTACHED", [], CASE, CASE_VERSION)]])
    repo = _repo(connection)

    with pytest.raises(InvalidTransitionInputError):
        await repo.transition_item(
            case_id=CASE,
            item_id=ITEM,
            to_status=PerfectionStatus.COMPLETED,
            actor_id=ACTOR,
            actor_role="LEGAL_REVIEWER",
            rationale=None,
            evidence_refs=(),
            filing_reference=None,
            effective_date=None,
            expiry_date=None,
        )
    assert "update public.security_perfection_items" not in _sql(connection)


@pytest.mark.asyncio
async def test_transition_not_required_without_rationale_raises() -> None:
    connection = Connection(results=[[("PENDING", [], CASE, CASE_VERSION)]])
    repo = _repo(connection)

    with pytest.raises(InvalidTransitionInputError):
        await repo.transition_item(
            case_id=CASE,
            item_id=ITEM,
            to_status=PerfectionStatus.NOT_REQUIRED_BY_HUMAN,
            actor_id=ACTOR,
            actor_role="LEGAL_REVIEWER",
            rationale="   ",
            evidence_refs=(),
            filing_reference=None,
            effective_date=None,
            expiry_date=None,
        )


@pytest.mark.asyncio
async def test_transition_unknown_item_raises() -> None:
    connection = Connection(results=[[]])
    repo = _repo(connection)

    with pytest.raises(ItemNotAccessibleError):
        await repo.transition_item(
            case_id=CASE,
            item_id=ITEM,
            to_status=PerfectionStatus.EVIDENCE_ATTACHED,
            actor_id=ACTOR,
            actor_role="LEGAL_REVIEWER",
            rationale=None,
            evidence_refs=("x",),
            filing_reference=None,
            effective_date=None,
            expiry_date=None,
        )


@pytest.mark.asyncio
async def test_list_interests_groups_items_under_interest() -> None:
    interest_row = (
        INTEREST,
        CASE,
        CASE_VERSION,
        "Quyền sử dụng đất (mô phỏng).",
        "REAL_ESTATE",
        "Bên bảo đảm Demo",
        "valuation-adapter://demo/1",
        None,
        ACTOR,
        NOW,
    )
    connection = Connection(
        results=[
            [interest_row],
            [
                _item_row(
                    status="COMPLETED",
                    evidence=["storage://demo/e1"],
                    completed_by=ACTOR,
                    completed_at=NOW,
                )
            ],
        ]
    )
    repo = _repo(connection)

    ledger = await repo.list_interests(CASE, CASE_VERSION)

    assert len(ledger) == 1
    assert ledger[0].interest.id == INTEREST
    assert len(ledger[0].items) == 1
    assert ledger[0].items[0].status == "COMPLETED"
    assert ledger[0].items[0].evidence_refs == ("storage://demo/e1",)


@pytest.mark.asyncio
async def test_list_interests_empty_short_circuits_item_query() -> None:
    connection = Connection(results=[[]])
    repo = _repo(connection)

    ledger = await repo.list_interests(CASE, CASE_VERSION)

    assert ledger == ()
    assert "security_perfection_items" not in _sql(connection)


@pytest.mark.asyncio
async def test_append_audit_writes_human_checker_row() -> None:
    connection = Connection(results=[[]])
    repo = _repo(connection)

    await repo.append_audit(
        OrchestrationAuditEvent(
            case_id=CASE,
            case_version=CASE_VERSION,
            event_type="SECURITY_PERFECTION_CONFIRMED",
            execution_id=uuid4(),
            artifact_type="SECURITY_PERFECTION_LEDGER",
            artifact_id=CASE,
        ),
        actor_id=ACTOR,
        actor_role="OPS_CHECKER",
    )

    audit_params = connection.params[0]
    assert audit_params is not None
    assert "HUMAN:OPS_CHECKER" in audit_params
    assert connection.transactions_opened == 1
