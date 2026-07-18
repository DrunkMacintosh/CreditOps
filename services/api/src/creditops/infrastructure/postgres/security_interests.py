"""Durable Postgres adapter for the stage-9 security-perfection ledger.

Every write is bounded, human-attributed, and single-transaction with its audit
row.  ``create_interest`` appends one per-asset interest; ``add_item`` appends one
perfection requirement (fail-closed if the interest is not part of the case);
``transition_item`` reads the current requirement, validates the move against the
closed domain transition graph, enforces the ``COMPLETED``/``NOT_REQUIRED_BY_HUMAN``
input rules, then advances it -- all in ONE transaction with a
``HUMAN:<role>`` audit event.  Reads are always scoped by ``case_id``.  Nothing
here satisfies a gate, records a decision, or drives orchestration.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.security_interests import (
    ForbiddenTransitionError,
    InterestNotAccessibleError,
    InvalidTransitionInputError,
    ItemNotAccessibleError,
    RecordedInterest,
    RecordedInterestWithItems,
    RecordedItem,
)
from creditops.domain.security_interests import (
    PerfectionStatus,
    SecurityInterest,
    SecurityPerfectionItem,
    is_allowed_item_transition,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_INTEREST_ARTIFACT_TYPE = "SECURITY_INTEREST"
_ITEM_ARTIFACT_TYPE = "SECURITY_PERFECTION_ITEM"
_INTEREST_CREATED_EVENT = "SECURITY_INTEREST_CREATED"
_ITEM_ADDED_EVENT = "SECURITY_PERFECTION_ITEM_ADDED"
_ITEM_TRANSITIONED_EVENT = "SECURITY_PERFECTION_ITEM_TRANSITIONED"

_INTEREST_COLUMNS = """
  id, case_id, case_version, asset_description_vi, asset_kind, owner_name_vi,
  valuation_reference, notes_vi, created_by, created_at
"""

_ITEM_COLUMNS = """
  id, interest_id, requirement_vi, status, evidence_refs, filing_reference,
  effective_date, expiry_date, completed_by, completed_at, created_at
"""


def _interest_from_row(row: tuple[object, ...]) -> RecordedInterest:
    return RecordedInterest(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        asset_description_vi=str(row[3]),
        asset_kind=str(row[4]),
        owner_name_vi=cast("str | None", row[5]),
        valuation_reference=cast("str | None", row[6]),
        notes_vi=cast("str | None", row[7]),
        created_by=cast(UUID, row[8]),
        created_at=cast(datetime, row[9]),
    )


def _item_from_row(row: tuple[object, ...]) -> RecordedItem:
    return RecordedItem(
        id=cast(UUID, row[0]),
        interest_id=cast(UUID, row[1]),
        requirement_vi=str(row[2]),
        status=str(row[3]),
        evidence_refs=tuple(
            str(ref) for ref in cast("list[object]", row[4] or [])
        ),
        filing_reference=cast("str | None", row[5]),
        effective_date=cast("date | None", row[6]),
        expiry_date=cast("date | None", row[7]),
        completed_by=cast("UUID | None", row[8]),
        completed_at=cast("datetime | None", row[9]),
        created_at=cast(datetime, row[10]),
    )


class PostgresSecurityInterestRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- writes --------------------------------------------------------------

    async def create_interest(
        self, *, interest: SecurityInterest, actor_role: str
    ) -> RecordedInterest:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.security_interests (
                      id, case_id, case_version, asset_description_vi, asset_kind,
                      owner_name_vi, valuation_reference, notes_vi, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        interest.id,
                        interest.case_id,
                        interest.case_version,
                        interest.asset_description_vi,
                        interest.asset_kind.value,
                        interest.owner_name_vi,
                        interest.valuation_reference,
                        interest.notes_vi,
                        interest.created_by,
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = cast(datetime, inserted[0]) if inserted else None
                await self._insert_audit(
                    connection,
                    case_id=interest.case_id,
                    case_version=interest.case_version,
                    event_type=_INTEREST_CREATED_EVENT,
                    actor_role=actor_role,
                    actor_id=interest.created_by,
                    artifact_type=_INTEREST_ARTIFACT_TYPE,
                    artifact_id=interest.id,
                    event_data={
                        "assetKind": interest.asset_kind.value,
                        "hasValuationReference": interest.valuation_reference
                        is not None,
                    },
                )
        return RecordedInterest(
            id=interest.id,
            case_id=interest.case_id,
            case_version=interest.case_version,
            asset_description_vi=interest.asset_description_vi,
            asset_kind=interest.asset_kind.value,
            owner_name_vi=interest.owner_name_vi,
            valuation_reference=interest.valuation_reference,
            notes_vi=interest.notes_vi,
            created_by=interest.created_by,
            created_at=cast(datetime, created_at),
        )

    async def add_item(
        self,
        *,
        case_id: UUID,
        item: SecurityPerfectionItem,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedItem:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                interest = await self._locate_interest(
                    connection, interest_id=item.interest_id, case_id=case_id
                )
                if interest is None:
                    raise InterestNotAccessibleError(str(item.interest_id))
                _, case_version = interest
                cursor = await connection.execute(
                    """
                    insert into public.security_perfection_items (
                      id, interest_id, requirement_vi, status, evidence_refs,
                      filing_reference, effective_date, expiry_date
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        item.id,
                        item.interest_id,
                        item.requirement_vi,
                        item.status.value,
                        Jsonb(list(item.evidence_refs)),
                        item.filing_reference,
                        item.effective_date,
                        item.expiry_date,
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = cast(datetime, inserted[0]) if inserted else None
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_ITEM_ADDED_EVENT,
                    actor_role=actor_role,
                    actor_id=actor_id,
                    artifact_type=_ITEM_ARTIFACT_TYPE,
                    artifact_id=item.id,
                    event_data={
                        "interestId": str(item.interest_id),
                        "status": item.status.value,
                    },
                )
        return RecordedItem(
            id=item.id,
            interest_id=item.interest_id,
            requirement_vi=item.requirement_vi,
            status=item.status.value,
            evidence_refs=item.evidence_refs,
            filing_reference=item.filing_reference,
            effective_date=item.effective_date,
            expiry_date=item.expiry_date,
            completed_by=item.completed_by,
            completed_at=item.completed_at,
            created_at=cast(datetime, created_at),
        )

    async def transition_item(
        self,
        *,
        case_id: UUID,
        item_id: UUID,
        to_status: PerfectionStatus,
        actor_id: UUID,
        actor_role: str,
        rationale: str | None,
        evidence_refs: tuple[str, ...],
        filing_reference: str | None,
        effective_date: date | None,
        expiry_date: date | None,
    ) -> RecordedItem:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    select spi.status, spi.evidence_refs, si.case_id,
                           si.case_version
                    from public.security_perfection_items as spi
                    join public.security_interests as si
                      on si.id = spi.interest_id
                    where spi.id = %s and si.case_id = %s
                    """,
                    (item_id, case_id),
                )
                current = await cursor.fetchone()
                if current is None:
                    raise ItemNotAccessibleError(str(item_id))
                current_status = PerfectionStatus(str(current[0]))
                existing_evidence = tuple(str(ref) for ref in (current[1] or []))
                case_version = int(cast(int, current[3]))

                if not is_allowed_item_transition(current_status, to_status):
                    raise ForbiddenTransitionError(current_status, to_status)

                merged_evidence = existing_evidence + tuple(evidence_refs)
                if to_status is PerfectionStatus.COMPLETED and not merged_evidence:
                    raise InvalidTransitionInputError(
                        "a COMPLETED perfection item requires at least one evidence "
                        "reference"
                    )
                if (
                    to_status is PerfectionStatus.NOT_REQUIRED_BY_HUMAN
                    and not (rationale or "").strip()
                ):
                    raise InvalidTransitionInputError(
                        "marking a perfection item NOT_REQUIRED_BY_HUMAN requires a "
                        "rationale"
                    )

                cursor = await connection.execute(
                    f"""
                    update public.security_perfection_items
                    set status = %s,
                        evidence_refs = %s,
                        filing_reference = coalesce(%s, filing_reference),
                        effective_date = coalesce(%s, effective_date),
                        expiry_date = coalesce(%s, expiry_date),
                        completed_by =
                          case when %s = 'COMPLETED' then %s else completed_by end,
                        completed_at =
                          case when %s = 'COMPLETED'
                            then clock_timestamp() else completed_at end
                    where id = %s
                    returning {_ITEM_COLUMNS}
                    """,
                    (
                        to_status.value,
                        Jsonb(list(merged_evidence)),
                        filing_reference,
                        effective_date,
                        expiry_date,
                        to_status.value,
                        actor_id,
                        to_status.value,
                        item_id,
                    ),
                )
                updated = await cursor.fetchone()
                if updated is None:
                    raise ItemNotAccessibleError(str(item_id))
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_ITEM_TRANSITIONED_EVENT,
                    actor_role=actor_role,
                    actor_id=actor_id,
                    artifact_type=_ITEM_ARTIFACT_TYPE,
                    artifact_id=item_id,
                    event_data={
                        "fromStatus": current_status.value,
                        "toStatus": to_status.value,
                        "rationale": rationale,
                        "evidenceRefsAdded": list(evidence_refs),
                        "filingReference": filing_reference,
                    },
                )
        return _item_from_row(tuple(updated))

    # -- reads ---------------------------------------------------------------

    async def list_interests(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedInterestWithItems, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                f"""
                select {_INTEREST_COLUMNS}
                from public.security_interests
                where case_id = %s and case_version = %s
                order by created_at
                """,
                (case_id, case_version),
            )
            interest_rows = await cursor.fetchall()
            interests = [_interest_from_row(tuple(row)) for row in interest_rows]
            if not interests:
                return ()
            cursor = await connection.execute(
                f"""
                select {_ITEM_COLUMNS}
                from public.security_perfection_items
                where interest_id = any(%s)
                order by created_at
                """,
                ([interest.id for interest in interests],),
            )
            item_rows = await cursor.fetchall()

        items_by_interest: dict[UUID, list[RecordedItem]] = {}
        for row in item_rows:
            item = _item_from_row(tuple(row))
            items_by_interest.setdefault(item.interest_id, []).append(item)
        return tuple(
            RecordedInterestWithItems(
                interest=interest,
                items=tuple(items_by_interest.get(interest.id, [])),
            )
            for interest in interests
        )

    async def append_audit(
        self, event: OrchestrationAuditEvent, *, actor_id: UUID, actor_role: str
    ) -> None:
        event_data = dict(event.event_data)
        event_data["executionId"] = str(event.execution_id)
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await self._insert_audit(
                    connection,
                    case_id=event.case_id,
                    case_version=event.case_version,
                    event_type=event.event_type,
                    actor_role=actor_role,
                    actor_id=actor_id,
                    artifact_type=event.artifact_type,
                    artifact_id=event.artifact_id,
                    event_data=event_data,
                )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    async def _locate_interest(
        connection: DatabaseConnection, *, interest_id: UUID, case_id: UUID
    ) -> tuple[UUID, int] | None:
        cursor = await connection.execute(
            """
            select case_id, case_version
            from public.security_interests
            where id = %s and case_id = %s
            """,
            (interest_id, case_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return cast(UUID, row[0]), int(cast(int, row[1]))

    @staticmethod
    async def _insert_audit(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        event_type: str,
        actor_role: str,
        actor_id: UUID,
        artifact_type: str,
        artifact_id: UUID,
        event_data: dict[str, object],
    ) -> None:
        await connection.execute(
            """
            insert into public.audit_events (
              case_id, case_version, event_type, actor_type, actor_id,
              artifact_type, artifact_id, event_data
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                case_id,
                case_version,
                event_type,
                f"HUMAN:{actor_role}",
                actor_id,
                artifact_type,
                artifact_id,
                Jsonb(event_data),
            ),
        )
