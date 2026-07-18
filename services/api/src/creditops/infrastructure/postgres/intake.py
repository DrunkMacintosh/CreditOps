"""Durable Postgres adapter for assigned-intake completion.

``load_intake_evidence`` reads the confirmed intake evidence back into the
frozen domain models the handoff validator needs, reconstructing each Confirmed
Fact from its Candidate + Confirmation via ``ConfirmedFact.from_confirmation``
so the reconstruction is exactly what the DB's derive-and-protect trigger
stored (no field can silently drift).  ``persist_handoff`` writes ONE immutable
``public.handoffs`` row in state ``READY_FOR_SPECIALIST_REVIEW``; there is no
unique key on (case, version, state), so idempotency is a guarded exists-check
inside the transaction (a benign residual race would only ever write a second
identical immutable snapshot at the same version, and G1 derives from
``exists(...)`` regardless).  Every write is parameter-bound; nothing here can
confirm a fact, satisfy a gate, or resolve a conflict/gap.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.intake import (
    CurrentHandoff,
    IntakeAuditEvent,
    IntakeEvidenceView,
    PersistedHandoff,
)
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE
from creditops.domain.enums import FactDisposition
from creditops.domain.evidence import (
    CandidateFact,
    ConfirmationAuthority,
    ConfirmedFact,
    FactConfirmation,
    FactValue,
    PageRegion,
)
from creditops.domain.handoffs import HANDOFF_READY_STATE, HandoffArtifact
from creditops.infrastructure.postgres.orchestration import ConnectionFactory

#: The immutable handoff is a human intake action; provenance records the
#: assigned intake officer, never an agent.
_CREATED_BY_TYPE = f"HUMAN:{INTAKE_OFFICER_ROLE}"
#: The handoff FK requires a source processing task at the same case version;
#: the intake evidence is produced by the per-document ingestion pipeline, so
#: its task is the handoff's provenance source.
_SOURCE_TASK_TYPE = "DOCUMENT_INGESTION"


class IntakeHandoffSourceMissing(RuntimeError):
    """No ingestion task exists to anchor the handoff -- fail closed rather
    than fabricate provenance."""


class PostgresIntakeRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def load_intake_evidence(
        self, case_id: UUID, case_version: int
    ) -> IntakeEvidenceView:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select c.id, c.document_version_id, c.field_key, c.proposed_value,
                       c.confidence, r.page_number, r.x, r.y, r.width, r.height
                from public.candidate_facts as c
                join public.page_regions as r on r.id = c.page_region_id
                where c.case_id = %s and c.case_version = %s and c.stale_at is null
                order by c.created_at, c.id
                """,
                (case_id, case_version),
            )
            candidates: dict[UUID, CandidateFact] = {}
            for row in await cursor.fetchall():
                candidate = CandidateFact(
                    id=cast(UUID, row[0]),
                    case_id=case_id,
                    case_version=case_version,
                    document_version_id=cast(UUID, row[1]),
                    field_key=str(row[2]),
                    proposed_value=cast(FactValue, row[3]),
                    confidence=float(row[4]),
                    source=PageRegion(
                        page=int(row[5]),
                        x=float(row[6]),
                        y=float(row[7]),
                        width=float(row[8]),
                        height=float(row[9]),
                    ),
                )
                candidates[candidate.id] = candidate

            cursor = await connection.execute(
                """
                select f.id, f.candidate_fact_id, f.disposition, f.corrected_value,
                       f.actor_id, f.assigned_officer_id, f.authority_source,
                       f.authority_granted_at, f.confirmed_at
                from public.fact_confirmations as f
                where f.case_id = %s and f.case_version = %s
                order by f.created_at, f.id
                """,
                (case_id, case_version),
            )
            confirmations: dict[UUID, FactConfirmation] = {}
            for row in await cursor.fetchall():
                authority = ConfirmationAuthority(
                    case_id=case_id,
                    case_version=case_version,
                    actor_id=cast(UUID, row[4]),
                    assigned_officer_id=cast(UUID, row[5]),
                    granted_at=cast(datetime, row[7]),
                    source=str(row[6]),
                )
                confirmation = FactConfirmation(
                    id=cast(UUID, row[0]),
                    candidate_id=cast(UUID, row[1]),
                    disposition=FactDisposition(str(row[2])),
                    authority=authority,
                    confirmed_at=cast(datetime, row[8]),
                    corrected_value=cast("FactValue | None", row[3]),
                )
                confirmations[confirmation.id] = confirmation

            cursor = await connection.execute(
                """
                select cf.id, cf.candidate_fact_id, cf.confirmation_id
                from public.confirmed_facts as cf
                where cf.case_id = %s and cf.case_version = %s and cf.stale_at is null
                order by cf.created_at, cf.id
                """,
                (case_id, case_version),
            )
            confirmed_facts: list[ConfirmedFact] = []
            for row in await cursor.fetchall():
                candidate = candidates[cast(UUID, row[1])]
                confirmation = confirmations[cast(UUID, row[2])]
                confirmed_facts.append(
                    ConfirmedFact.from_confirmation(
                        id=cast(UUID, row[0]),
                        candidate=candidate,
                        confirmation=confirmation,
                    )
                )

            cursor = await connection.execute(
                """
                select id from public.evidence_conflicts
                where case_id = %s and case_version = %s and status = 'OPEN'
                order by created_at, id
                """,
                (case_id, case_version),
            )
            conflict_ids = tuple(cast(UUID, row[0]) for row in await cursor.fetchall())

            cursor = await connection.execute(
                """
                select id from public.evidence_gaps
                where case_id = %s and case_version = %s
                  and status in ('PROVISIONAL', 'FORMAL')
                order by created_at, id
                """,
                (case_id, case_version),
            )
            gap_ids = tuple(cast(UUID, row[0]) for row in await cursor.fetchall())

        return IntakeEvidenceView(
            case_id=case_id,
            case_version=case_version,
            candidates=tuple(candidates.values()),
            confirmations=tuple(confirmations.values()),
            confirmed_facts=tuple(confirmed_facts),
            conflict_ids=conflict_ids,
            gap_ids=gap_ids,
        )

    async def load_current_handoff(
        self, case_id: UUID, case_version: int
    ) -> CurrentHandoff | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, state, case_version, created_at
                from public.handoffs
                where case_id = %s and case_version = %s
                  and state = %s and stale_at is null
                order by created_at desc
                limit 1
                """,
                (case_id, case_version, HANDOFF_READY_STATE),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return CurrentHandoff(
            id=cast(UUID, row[0]),
            case_id=case_id,
            case_version=int(row[2]),
            state=str(row[1]),
            created_at=cast(datetime, row[3]),
        )

    async def has_current_handoff(self, case_id: UUID, case_version: int) -> bool:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select exists (
                  select 1 from public.handoffs
                  where case_id = %s and case_version = %s
                    and state = %s and stale_at is null
                )
                """,
                (case_id, case_version, HANDOFF_READY_STATE),
            )
            row = await cursor.fetchone()
        return bool(row and row[0])

    async def persist_handoff(
        self, handoff: HandoffArtifact, *, actor_id: UUID
    ) -> PersistedHandoff:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                # Idempotency guard: no unique key exists on (case, version,
                # state), so re-check inside the transaction and reuse the
                # existing immutable handoff instead of writing a second one.
                cursor = await connection.execute(
                    """
                    select id from public.handoffs
                    where case_id = %s and case_version = %s
                      and state = %s and stale_at is null
                    order by created_at
                    limit 1
                    """,
                    (handoff.case_id, handoff.case_version, HANDOFF_READY_STATE),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    return PersistedHandoff(
                        handoff_id=cast(UUID, existing[0]), created=False
                    )

                cursor = await connection.execute(
                    """
                    select id from public.processing_tasks
                    where case_id = %s and case_version = %s and task_type = %s
                    order by created_at desc
                    limit 1
                    """,
                    (handoff.case_id, handoff.case_version, _SOURCE_TASK_TYPE),
                )
                source_row = await cursor.fetchone()
                if source_row is None:
                    raise IntakeHandoffSourceMissing(
                        "no ingestion task anchors the intake handoff"
                    )
                source_task_id = cast(UUID, source_row[0])

                await connection.execute(
                    """
                    insert into public.handoffs (
                      id, case_id, case_version, source_task_id, state,
                      handoff_data, created_by_type, created_by_id
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        handoff.id,
                        handoff.case_id,
                        handoff.case_version,
                        source_task_id,
                        HANDOFF_READY_STATE,
                        Jsonb(handoff.model_dump(mode="json")),
                        _CREATED_BY_TYPE,
                        actor_id,
                    ),
                )
        return PersistedHandoff(handoff_id=handoff.id, created=True)

    async def append_audit(self, event: IntakeAuditEvent) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    insert into public.audit_events (
                      case_id, case_version, event_type, actor_type, actor_id,
                      artifact_type, artifact_id, event_data
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.case_id,
                        event.case_version,
                        event.event_type,
                        _CREATED_BY_TYPE,
                        event.actor_id,
                        event.artifact_type,
                        event.artifact_id,
                        Jsonb(dict(event.event_data)),
                    ),
                )
