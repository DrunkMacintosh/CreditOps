"""Durable Postgres adapter for the stage-10 disbursement ConditionLedger.

Every write is human-only and case/version scoped.  ``create_condition`` and
``transition_condition`` are each ONE transaction that writes the domain row,
its append-only ``condition_status_events`` row, and a ``HUMAN:<role>`` audit
event together -- never a partial write.  ``transition_condition`` re-checks the
domain transition map inside the transaction (defence in depth over the database
trigger and the application pre-check), so a lost race can never persist a
forbidden edge.  Nothing here confirms a gate or drives orchestration.

All identifiers and data are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.conditions import (
    ConditionNotFound,
    ForbiddenConditionTransition,
    RecordedCondition,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.conditions import (
    RATIONALE_REQUIRED_TARGETS,
    ConditionStatus,
    DisbursementCondition,
    is_transition_allowed,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_ARTIFACT_TYPE = "DISBURSEMENT_CONDITION"
_CREATED_EVENT_TYPE = "DISBURSEMENT_CONDITION_CREATED"
_TRANSITIONED_EVENT_TYPE = "DISBURSEMENT_CONDITION_TRANSITIONED"
#: The confirmation is an independent OPS-checker human act; ``append_audit`` is
#: used exclusively by the confirm surface, so the audit actor type is fixed.
_CONFIRM_ACTOR_TYPE = "HUMAN:OPS_CHECKER"


class PostgresConditionLedgerRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- writes ---------------------------------------------------------------

    async def create_condition(
        self,
        *,
        condition: DisbursementCondition,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedCondition:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.disbursement_conditions (
                      id, case_id, case_version, decision_id, condition_text_vi,
                      owner_vi, due_date, status, evidence_refs
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        condition.id,
                        condition.case_id,
                        condition.case_version,
                        condition.decision_id,
                        condition.condition_text_vi,
                        condition.owner_vi,
                        condition.due_date,
                        condition.status.value,
                        Jsonb(list(condition.evidence_refs)),
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = cast(datetime, inserted[0]) if inserted is not None else None
                await self._insert_event(
                    connection,
                    condition_id=condition.id,
                    from_status=None,
                    to_status=condition.status,
                    rationale_vi=None,
                    actor_id=actor_id,
                    actor_role=actor_role,
                )
                await self._insert_audit(
                    connection,
                    case_id=condition.case_id,
                    case_version=condition.case_version,
                    event_type=_CREATED_EVENT_TYPE,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_id=condition.id,
                    event_data={
                        "conditionId": str(condition.id),
                        "decisionId": str(condition.decision_id),
                        "status": condition.status.value,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return RecordedCondition(
            id=condition.id,
            case_id=condition.case_id,
            case_version=condition.case_version,
            decision_id=condition.decision_id,
            condition_text_vi=condition.condition_text_vi,
            owner_vi=condition.owner_vi,
            due_date=condition.due_date,
            status=condition.status,
            evidence_refs=condition.evidence_refs,
            created_at=cast(datetime, created_at),
        )

    async def transition_condition(
        self,
        *,
        condition_id: UUID,
        case_id: UUID,
        case_version: int,
        to_status: ConditionStatus,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str | None,
        evidence_refs: tuple[str, ...] | None,
    ) -> RecordedCondition:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    select id, case_id, case_version, decision_id,
                           condition_text_vi, owner_vi, due_date, status,
                           evidence_refs, created_at
                    from public.disbursement_conditions
                    where id = %s and case_id = %s and case_version = %s
                    for update
                    """,
                    (condition_id, case_id, case_version),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise ConditionNotFound(str(condition_id))
                current = _row_to_condition(row)
                if not is_transition_allowed(current.status, to_status):
                    # Fail-closed backstop for a lost race: nothing is written.
                    raise ForbiddenConditionTransition(
                        f"{current.status.value} -> {to_status.value}"
                    )
                if to_status in RATIONALE_REQUIRED_TARGETS and not rationale_vi:
                    # A human waiver / not-applicable ruling is an authority act;
                    # the rationale IS the authority record (also DB-enforced).
                    raise ValueError(
                        f"{to_status.value} requires an authority rationale"
                    )
                new_refs = (
                    current.evidence_refs if evidence_refs is None else evidence_refs
                )
                await connection.execute(
                    """
                    update public.disbursement_conditions
                    set status = %s, evidence_refs = %s
                    where id = %s and case_id = %s and case_version = %s
                    """,
                    (
                        to_status.value,
                        Jsonb(list(new_refs)),
                        condition_id,
                        case_id,
                        case_version,
                    ),
                )
                await self._insert_event(
                    connection,
                    condition_id=condition_id,
                    from_status=current.status,
                    to_status=to_status,
                    rationale_vi=rationale_vi,
                    actor_id=actor_id,
                    actor_role=actor_role,
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_TRANSITIONED_EVENT_TYPE,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_id=condition_id,
                    event_data={
                        "conditionId": str(condition_id),
                        "fromStatus": current.status.value,
                        "toStatus": to_status.value,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                        "hasRationale": rationale_vi is not None,
                    },
                )
        return RecordedCondition(
            id=current.id,
            case_id=current.case_id,
            case_version=current.case_version,
            decision_id=current.decision_id,
            condition_text_vi=current.condition_text_vi,
            owner_vi=current.owner_vi,
            due_date=current.due_date,
            status=to_status,
            evidence_refs=new_refs,
            created_at=current.created_at,
        )

    # -- reads ----------------------------------------------------------------

    async def list_conditions(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCondition, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, decision_id, condition_text_vi,
                       owner_vi, due_date, status, evidence_refs, created_at
                from public.disbursement_conditions
                where case_id = %s and case_version = %s
                order by created_at asc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_condition(row) for row in rows)

    async def load_condition(
        self, condition_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedCondition | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, decision_id, condition_text_vi,
                       owner_vi, due_date, status, evidence_refs, created_at
                from public.disbursement_conditions
                where id = %s and case_id = %s and case_version = %s
                """,
                (condition_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_condition(row) if row is not None else None

    async def list_verifying_actor_ids(
        self, case_id: UUID, case_version: int
    ) -> frozenset[UUID]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select distinct event.actor_id
                from public.condition_status_events as event
                join public.disbursement_conditions as condition
                  on condition.id = event.condition_id
                where condition.case_id = %s and condition.case_version = %s
                  and event.to_status = 'VERIFIED'
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return frozenset(cast(UUID, row[0]) for row in rows)

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        event_data = dict(event.event_data)
        event_data["executionId"] = str(event.execution_id)
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    insert into public.audit_events (
                      case_id, case_version, event_type, actor_type, actor_id,
                      artifact_type, artifact_id, event_data
                    ) values (%s, %s, %s, %s, null, %s, %s, %s)
                    """,
                    (
                        event.case_id,
                        event.case_version,
                        event.event_type,
                        _CONFIRM_ACTOR_TYPE,
                        event.artifact_type,
                        event.artifact_id,
                        Jsonb(event_data),
                    ),
                )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    async def _insert_event(
        connection: DatabaseConnection,
        *,
        condition_id: UUID,
        from_status: ConditionStatus | None,
        to_status: ConditionStatus,
        rationale_vi: str | None,
        actor_id: UUID,
        actor_role: str,
    ) -> None:
        await connection.execute(
            """
            insert into public.condition_status_events (
              condition_id, from_status, to_status, rationale_vi, actor_id,
              actor_role
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            (
                condition_id,
                from_status.value if from_status is not None else None,
                to_status.value,
                rationale_vi,
                actor_id,
                actor_role,
            ),
        )

    @staticmethod
    async def _insert_audit(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        event_type: str,
        actor_id: UUID,
        actor_role: str,
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
                _ARTIFACT_TYPE,
                artifact_id,
                Jsonb(event_data),
            ),
        )


def _row_to_condition(row: Sequence[Any]) -> RecordedCondition:
    return RecordedCondition(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        decision_id=cast(UUID, row[3]),
        condition_text_vi=str(row[4]),
        owner_vi=cast("str | None", row[5]),
        due_date=cast("date | None", row[6]),
        status=ConditionStatus(str(row[7])),
        evidence_refs=tuple(str(ref) for ref in (row[8] or [])),
        created_at=cast(datetime, row[9]),
    )
