"""Durable Postgres adapter for the stage-6 human credit decision.

``record_decision`` is the ONLY write path and it is human-only: in ONE
transaction it inserts the append-only decision, its optional 1:1 approved-term
snapshot, and a ``HUMAN:CREDIT_APPROVER`` audit event.  It is idempotent on the
``(case_id, case_version)`` unique key -- a duplicate insert resolves to the
existing decision (``created=False``) and writes NEITHER a second snapshot nor a
second audit event.  ``load_decision_binding`` and ``load_decision`` issue
``select`` statements exclusively; the binding reads are read-only lookups over
``credit_cases`` and the three upstream artifact tables so the API can prove a
decision's version + artifact bindings before recording.  Nothing here satisfies
a gate, resolves a gap, confirms a fact, or drives orchestration.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.credit_decisions import (
    DecisionBinding,
    RecordedDecision,
    RecordedTermSnapshot,
)
from creditops.domain.credit_decisions import ApprovedTermSnapshot, HumanCreditDecision
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

#: A credit decision is a human authority action; the audit actor is the human
#: decider whose id is recorded, under this fixed actor type.
_ACTOR_TYPE = "HUMAN:CREDIT_APPROVER"
_ARTIFACT_TYPE = "HUMAN_CREDIT_DECISION"
_AUDIT_EVENT_TYPE = "HUMAN_CREDIT_DECISION_RECORDED"


class PostgresCreditDecisionRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- read-only binding lookups -------------------------------------------

    async def load_decision_binding(self, case_id: UUID) -> DecisionBinding | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                "select case_version from public.credit_cases where id = %s",
                (case_id,),
            )
            case_row = await cursor.fetchone()
            if case_row is None:
                return None
            case_version = int(case_row[0])
            memo_id = await self._latest_id(
                connection, "public.credit_ops_packages", case_id, case_version
            )
            risk_id = await self._latest_id(
                connection, "public.risk_review_assessments", case_id, case_version
            )
            underwriting_id = await self._latest_id(
                connection, "public.underwriting_assessments", case_id, case_version
            )
        return DecisionBinding(
            current_case_version=case_version,
            latest_memo_artifact_id=memo_id,
            latest_risk_assessment_id=risk_id,
            latest_underwriting_assessment_id=underwriting_id,
        )

    @staticmethod
    async def _latest_id(
        connection: DatabaseConnection, table: str, case_id: UUID, case_version: int
    ) -> UUID | None:
        cursor = await connection.execute(
            f"""
            select id from {table}
            where case_id = %s and case_version = %s
            order by created_at desc
            limit 1
            """,
            (case_id, case_version),
        )
        row = await cursor.fetchone()
        return cast(UUID, row[0]) if row is not None else None

    # -- read a recorded decision --------------------------------------------

    async def load_decision(
        self, case_id: UUID, case_version: int
    ) -> RecordedDecision | None:
        async with self._connection_factory() as connection:
            return await self._load_decision_on(
                connection, case_id, case_version, created=False
            )

    @staticmethod
    async def _load_decision_on(
        connection: DatabaseConnection,
        case_id: UUID,
        case_version: int,
        *,
        created: bool,
    ) -> RecordedDecision | None:
        cursor = await connection.execute(
            """
            select id, case_id, case_version, decision, rationale_vi, decided_by,
                   decided_by_role, memo_artifact_id, risk_assessment_id,
                   underwriting_assessment_id, conditions, created_at
            from public.human_credit_decisions
            where case_id = %s and case_version = %s
            """,
            (case_id, case_version),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        decision_id = cast(UUID, row[0])
        snapshot = await PostgresCreditDecisionRepository._load_snapshot_on(
            connection, decision_id
        )
        return RecordedDecision(
            id=decision_id,
            case_id=cast(UUID, row[1]),
            case_version=int(row[2]),
            decision=str(row[3]),
            rationale_vi=str(row[4]),
            decided_by=cast(UUID, row[5]),
            decided_by_role=str(row[6]),
            memo_artifact_id=cast("UUID | None", row[7]),
            risk_assessment_id=cast("UUID | None", row[8]),
            underwriting_assessment_id=cast("UUID | None", row[9]),
            conditions=tuple(str(condition) for condition in (row[10] or [])),
            created_at=cast(datetime, row[11]),
            snapshot=snapshot,
            created=created,
        )

    @staticmethod
    async def _load_snapshot_on(
        connection: DatabaseConnection, decision_id: UUID
    ) -> RecordedTermSnapshot | None:
        cursor = await connection.execute(
            """
            select id, decision_id, case_id, case_version, terms, snapshot_hash,
                   created_at
            from public.approved_term_snapshots
            where decision_id = %s
            """,
            (decision_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return RecordedTermSnapshot(
            id=cast(UUID, row[0]),
            decision_id=cast(UUID, row[1]),
            case_id=cast(UUID, row[2]),
            case_version=int(row[3]),
            terms=cast("dict[str, object]", row[4]),
            snapshot_hash=str(row[5]),
            created_at=cast(datetime, row[6]),
        )

    # -- the single human write path -----------------------------------------

    async def record_decision(
        self,
        *,
        decision: HumanCreditDecision,
        snapshot: ApprovedTermSnapshot | None,
    ) -> RecordedDecision:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.human_credit_decisions (
                      id, case_id, case_version, decision, rationale_vi,
                      decided_by, decided_by_role, memo_artifact_id,
                      risk_assessment_id, underwriting_assessment_id, conditions
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (case_id, case_version) do nothing
                    returning id, created_at
                    """,
                    (
                        decision.id,
                        decision.case_id,
                        decision.case_version,
                        decision.decision.value,
                        decision.rationale_vi,
                        decision.decided_by,
                        decision.decided_by_role,
                        decision.memo_artifact_id,
                        decision.risk_assessment_id,
                        decision.underwriting_assessment_id,
                        Jsonb(list(decision.conditions)),
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    # A decision already exists for this (case, version): resolve
                    # to it and write nothing further -- the idempotent repeat.
                    existing = await self._load_decision_on(
                        connection,
                        decision.case_id,
                        decision.case_version,
                        created=False,
                    )
                    if existing is None:
                        raise RuntimeError("decision idempotency row disappeared")
                    return existing

                created_at = cast(datetime, inserted[1])
                snapshot_record: RecordedTermSnapshot | None = None
                if snapshot is not None:
                    terms_json = snapshot.terms.model_dump(mode="json")
                    cursor = await connection.execute(
                        """
                        insert into public.approved_term_snapshots (
                          id, decision_id, case_id, case_version, terms,
                          snapshot_hash
                        ) values (%s, %s, %s, %s, %s, %s)
                        returning created_at
                        """,
                        (
                            snapshot.id,
                            snapshot.decision_id,
                            snapshot.case_id,
                            snapshot.case_version,
                            Jsonb(terms_json),
                            snapshot.snapshot_hash,
                        ),
                    )
                    snapshot_created = await cursor.fetchone()
                    snapshot_record = RecordedTermSnapshot(
                        id=snapshot.id,
                        decision_id=snapshot.decision_id,
                        case_id=snapshot.case_id,
                        case_version=snapshot.case_version,
                        terms=terms_json,
                        snapshot_hash=snapshot.snapshot_hash,
                        created_at=(
                            cast(datetime, snapshot_created[0])
                            if snapshot_created is not None
                            else created_at
                        ),
                    )

                await connection.execute(
                    """
                    insert into public.audit_events (
                      case_id, case_version, event_type, actor_type, actor_id,
                      artifact_type, artifact_id, event_data
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        decision.case_id,
                        decision.case_version,
                        _AUDIT_EVENT_TYPE,
                        _ACTOR_TYPE,
                        decision.decided_by,
                        _ARTIFACT_TYPE,
                        decision.id,
                        Jsonb(
                            {
                                "decision": decision.decision.value,
                                "decidedByRole": decision.decided_by_role,
                                "hasApprovedTermSnapshot": snapshot is not None,
                                "approvedTermSnapshotId": (
                                    str(snapshot.id) if snapshot is not None else None
                                ),
                                "memoArtifactId": (
                                    str(decision.memo_artifact_id)
                                    if decision.memo_artifact_id is not None
                                    else None
                                ),
                                "riskAssessmentId": (
                                    str(decision.risk_assessment_id)
                                    if decision.risk_assessment_id is not None
                                    else None
                                ),
                                "underwritingAssessmentId": (
                                    str(decision.underwriting_assessment_id)
                                    if decision.underwriting_assessment_id is not None
                                    else None
                                ),
                            }
                        ),
                    ),
                )

        return RecordedDecision(
            id=decision.id,
            case_id=decision.case_id,
            case_version=decision.case_version,
            decision=decision.decision.value,
            rationale_vi=decision.rationale_vi,
            decided_by=decision.decided_by,
            decided_by_role=decision.decided_by_role,
            memo_artifact_id=decision.memo_artifact_id,
            risk_assessment_id=decision.risk_assessment_id,
            underwriting_assessment_id=decision.underwriting_assessment_id,
            conditions=decision.conditions,
            created_at=created_at,
            snapshot=snapshot_record,
            created=True,
        )
