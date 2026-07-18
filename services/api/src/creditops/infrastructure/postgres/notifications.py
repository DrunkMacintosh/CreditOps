"""Durable Postgres adapter for the stage-7 credit notification + mock delivery.

Two write paths, both human-facing and both single-transaction:

- ``create_draft`` loads the current case version and its recorded
  ``HumanCreditDecision`` + approved-term snapshot, fails closed with
  ``DecisionDoesNotPermitNotificationError`` unless the decision is an approval
  outcome, then DETERMINISTICALLY renders the notification body
  (``domain.notifications.render_notification_content_vi`` -- no LLM) and
  idempotently inserts the draft on the ``(case_id, case_version)`` unique key.
  A duplicate resolves to the existing draft (``created=False``) and writes NO
  second audit event.
- ``record_mock_delivery`` writes the append-only ``CommunicationReceipt`` and a
  ``HUMAN:OPS_OFFICER`` audit event in ONE transaction.  It re-asserts the gate
  status passed in from the API (``GateNotSatisfiedError``) and always records the
  labelled mock channel; the receipt's ``content_hash`` must equal the draft's
  (enforced again by a DB trigger).  Delivery is idempotent on the draft.

The reads (``load_draft`` / ``load_receipt``) issue ``select`` only.  Nothing
here satisfies a gate, resolves a gap, confirms a fact, or drives orchestration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.notifications import (
    DecisionDoesNotPermitNotificationError,
    GateNotSatisfiedError,
    NotificationRepository,
    RecordedCommunicationReceipt,
    RecordedNotificationDraft,
)
from creditops.domain.credit_decisions import (
    APPROVAL_DECISIONS,
    ApprovedTerms,
    CreditDecisionType,
    HumanCreditDecision,
)
from creditops.domain.notifications import (
    MOCK_DELIVERY_CHANNEL,
    CommunicationReceipt,
    build_notification_draft,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

#: The audit actor: a credit notification draft/delivery is a human operations
#: action; the actor id recorded is the human creator/deliverer.
_ACTOR_TYPE = "HUMAN:OPS_OFFICER"
_DRAFT_ARTIFACT_TYPE = "CREDIT_NOTIFICATION_DRAFT"
_RECEIPT_ARTIFACT_TYPE = "COMMUNICATION_RECEIPT"
_DRAFT_AUDIT_EVENT_TYPE = "CREDIT_NOTIFICATION_DRAFTED"
_RECEIPT_AUDIT_EVENT_TYPE = "CREDIT_NOTIFICATION_MOCK_DELIVERED"


class PostgresNotificationRepository(NotificationRepository):
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- the draft write path -------------------------------------------------

    async def create_draft(
        self,
        *,
        draft_id: UUID,
        case_id: UUID,
        created_by: UUID,
    ) -> RecordedNotificationDraft:
        async with self._connection_factory() as connection:
            case_version = await self._current_case_version(connection, case_id)
            if case_version is None:
                raise DecisionDoesNotPermitNotificationError(
                    "no case exists for a notification"
                )
            decision, terms = await self._load_permitting_decision(
                connection, case_id, case_version
            )
            draft = build_notification_draft(
                draft_id=draft_id,
                decision=decision,
                terms=terms,
                created_by=created_by,
            )
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.credit_notification_drafts (
                      id, case_id, case_version, decision_id, content_vi,
                      content_hash, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (case_id, case_version) do nothing
                    returning id, created_at
                    """,
                    (
                        draft.id,
                        draft.case_id,
                        draft.case_version,
                        draft.decision_id,
                        draft.content_vi,
                        draft.content_hash,
                        draft.created_by,
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    existing = await self._load_draft_on(connection, case_id)
                    if existing is None:
                        raise RuntimeError("notification draft idempotency row vanished")
                    return existing
                await self._append_audit(
                    connection,
                    case_id=draft.case_id,
                    case_version=draft.case_version,
                    event_type=_DRAFT_AUDIT_EVENT_TYPE,
                    actor_id=draft.created_by,
                    artifact_type=_DRAFT_ARTIFACT_TYPE,
                    artifact_id=draft.id,
                    event_data={
                        "decisionId": str(draft.decision_id),
                        "contentHash": draft.content_hash,
                    },
                )
                created_at = cast(datetime, inserted[1])
        return RecordedNotificationDraft(
            id=draft.id,
            case_id=draft.case_id,
            case_version=draft.case_version,
            decision_id=draft.decision_id,
            content_vi=draft.content_vi,
            content_hash=draft.content_hash,
            created_by=draft.created_by,
            created_at=created_at,
            created=True,
        )

    # -- reads ----------------------------------------------------------------

    async def load_draft(self, case_id: UUID) -> RecordedNotificationDraft | None:
        async with self._connection_factory() as connection:
            return await self._load_draft_on(connection, case_id)

    async def load_receipt(
        self, draft_id: UUID
    ) -> RecordedCommunicationReceipt | None:
        async with self._connection_factory() as connection:
            return await self._load_receipt_on(connection, draft_id)

    # -- the mock delivery write path -----------------------------------------

    async def record_mock_delivery(
        self,
        *,
        receipt_id: UUID,
        draft_id: UUID,
        content_hash: str,
        receipt_note_vi: str | None,
        recorded_by: UUID,
        gate_satisfied: bool,
    ) -> RecordedCommunicationReceipt:
        if not gate_satisfied:
            raise GateNotSatisfiedError(
                "HG_CREDIT_NOTIFICATION_APPROVED must be SATISFIED before delivery"
            )
        # Build the domain receipt first: this validates the labelled mock
        # channel and the sha256 shape before any write.
        receipt = CommunicationReceipt(
            id=receipt_id,
            draft_id=draft_id,
            delivered_via=MOCK_DELIVERY_CHANNEL,
            content_hash=content_hash,
            receipt_note_vi=receipt_note_vi,
            recorded_by=recorded_by,
        )
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    select case_id, case_version, content_hash
                    from public.credit_notification_drafts
                    where id = %s
                    """,
                    (draft_id,),
                )
                draft_row = await cursor.fetchone()
                if draft_row is None:
                    raise RuntimeError("notification draft to deliver disappeared")
                case_id = cast(UUID, draft_row[0])
                case_version = int(draft_row[1])

                cursor = await connection.execute(
                    """
                    insert into public.communication_receipts (
                      id, draft_id, delivered_via, content_hash, receipt_note_vi,
                      recorded_by
                    ) values (%s, %s, %s, %s, %s, %s)
                    on conflict (draft_id) do nothing
                    returning id, created_at
                    """,
                    (
                        receipt.id,
                        receipt.draft_id,
                        receipt.delivered_via,
                        receipt.content_hash,
                        receipt.receipt_note_vi,
                        receipt.recorded_by,
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    existing = await self._load_receipt_on(connection, draft_id)
                    if existing is None:
                        raise RuntimeError("communication receipt idempotency row vanished")
                    return existing
                await self._append_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_RECEIPT_AUDIT_EVENT_TYPE,
                    actor_id=receipt.recorded_by,
                    artifact_type=_RECEIPT_ARTIFACT_TYPE,
                    artifact_id=receipt.id,
                    event_data={
                        "draftId": str(receipt.draft_id),
                        "deliveredVia": receipt.delivered_via,
                        "contentHash": receipt.content_hash,
                    },
                )
                created_at = cast(datetime, inserted[1])
        return RecordedCommunicationReceipt(
            id=receipt.id,
            draft_id=receipt.draft_id,
            delivered_via=receipt.delivered_via,
            content_hash=receipt.content_hash,
            receipt_note_vi=receipt.receipt_note_vi,
            recorded_by=receipt.recorded_by,
            created_at=created_at,
        )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    async def _current_case_version(
        connection: DatabaseConnection, case_id: UUID
    ) -> int | None:
        cursor = await connection.execute(
            "select case_version from public.credit_cases where id = %s",
            (case_id,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row is not None else None

    @staticmethod
    async def _load_permitting_decision(
        connection: DatabaseConnection, case_id: UUID, case_version: int
    ) -> tuple[HumanCreditDecision, ApprovedTerms | None]:
        cursor = await connection.execute(
            """
            select id, decision, rationale_vi, decided_by, decided_by_role,
                   conditions, memo_artifact_id, risk_assessment_id,
                   underwriting_assessment_id
            from public.human_credit_decisions
            where case_id = %s and case_version = %s
            """,
            (case_id, case_version),
        )
        row = await cursor.fetchone()
        if row is None:
            raise DecisionDoesNotPermitNotificationError(
                "no recorded credit decision for the current case version"
            )
        try:
            decision_type = CreditDecisionType(str(row[1]))
        except ValueError as exc:  # pragma: no cover - closed DB CHECK guards this
            raise DecisionDoesNotPermitNotificationError(
                "recorded decision has an unknown type"
            ) from exc
        if decision_type not in APPROVAL_DECISIONS:
            raise DecisionDoesNotPermitNotificationError(
                f"{decision_type.value} does not permit a credit notification"
            )
        decision = HumanCreditDecision(
            id=cast(UUID, row[0]),
            case_id=case_id,
            case_version=case_version,
            decision=decision_type,
            rationale_vi=str(row[2]),
            decided_by=cast(UUID, row[3]),
            decided_by_role=str(row[4]),
            memo_artifact_id=cast("UUID | None", row[6]),
            risk_assessment_id=cast("UUID | None", row[7]),
            underwriting_assessment_id=cast("UUID | None", row[8]),
            conditions=tuple(str(c) for c in (row[5] or [])),
        )
        terms = await PostgresNotificationRepository._load_terms_on(
            connection, decision.id
        )
        return decision, terms

    @staticmethod
    async def _load_terms_on(
        connection: DatabaseConnection, decision_id: UUID
    ) -> ApprovedTerms | None:
        cursor = await connection.execute(
            """
            select terms from public.approved_term_snapshots
            where decision_id = %s
            """,
            (decision_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        raw = cast("dict[str, object]", row[0])
        amount = raw.get("amount")
        rate = raw.get("rate")
        return ApprovedTerms(
            amount=Decimal(str(amount)) if amount is not None else None,
            currency=cast("str | None", raw.get("currency")),
            term=cast("str | None", raw.get("term")),
            rate=Decimal(str(rate)) if rate is not None else None,
        )

    @staticmethod
    async def _load_draft_on(
        connection: DatabaseConnection, case_id: UUID
    ) -> RecordedNotificationDraft | None:
        cursor = await connection.execute(
            """
            select id, case_id, case_version, decision_id, content_vi,
                   content_hash, created_by, created_at
            from public.credit_notification_drafts
            where case_id = %s
            order by case_version desc
            limit 1
            """,
            (case_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return RecordedNotificationDraft(
            id=cast(UUID, row[0]),
            case_id=cast(UUID, row[1]),
            case_version=int(row[2]),
            decision_id=cast(UUID, row[3]),
            content_vi=str(row[4]),
            content_hash=str(row[5]),
            created_by=cast(UUID, row[6]),
            created_at=cast(datetime, row[7]),
            created=False,
        )

    @staticmethod
    async def _load_receipt_on(
        connection: DatabaseConnection, draft_id: UUID
    ) -> RecordedCommunicationReceipt | None:
        cursor = await connection.execute(
            """
            select id, draft_id, delivered_via, content_hash, receipt_note_vi,
                   recorded_by, created_at
            from public.communication_receipts
            where draft_id = %s
            """,
            (draft_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return RecordedCommunicationReceipt(
            id=cast(UUID, row[0]),
            draft_id=cast(UUID, row[1]),
            delivered_via=str(row[2]),
            content_hash=str(row[3]),
            receipt_note_vi=cast("str | None", row[4]),
            recorded_by=cast(UUID, row[5]),
            created_at=cast(datetime, row[6]),
        )

    @staticmethod
    async def _append_audit(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        event_type: str,
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
                _ACTOR_TYPE,
                actor_id,
                artifact_type,
                artifact_id,
                Jsonb(event_data),
            ),
        )
