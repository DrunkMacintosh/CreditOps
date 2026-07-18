"""Durable-state contract for the stage-7 credit notification + mock delivery.

The port exposes only what the human-facing notification API needs:

- ``create_draft`` idempotently renders + persists the notification draft for a
  case's current version.  It loads the recorded ``HumanCreditDecision`` for that
  version and raises ``DecisionDoesNotPermitNotificationError`` unless the
  decision is an approval outcome -- the spec forbids creating a notification
  when the decision does not permit one (master design section 5 giai đoạn 7).
- ``load_draft`` reads the current-version draft (participant read side).
- ``load_receipt`` reads a draft's mock delivery receipt, if any.
- ``record_mock_delivery`` writes the ``CommunicationReceipt`` plus its audit row
  in ONE transaction.  It requires the ``HG_CREDIT_NOTIFICATION_APPROVED`` gate
  to be SATISFIED for the version -- the gate status is passed in from the API
  layer and re-asserted here (``GateNotSatisfiedError``) so the write fails closed
  even if the API check is bypassed.  ``delivered_via`` is always the labelled
  mock channel; nothing is ever sent.

No method here can be called by an agent or satisfy the gate; the gate write is
a separate human authority action through the orchestration repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class DecisionDoesNotPermitNotificationError(RuntimeError):
    """No recorded decision for the current version permits a notification.

    Raised by ``create_draft`` when there is no ``HumanCreditDecision`` for the
    case's current version, or the recorded decision is not an approval outcome
    (``APPROVED_AS_PROPOSED`` / ``APPROVED_WITH_CONDITIONS``).  The API maps this
    to 409 ``DECISION_DOES_NOT_PERMIT_NOTIFICATION`` -- fail closed.
    """


class GateNotSatisfiedError(RuntimeError):
    """The ``HG_CREDIT_NOTIFICATION_APPROVED`` gate is not SATISFIED.

    Raised by ``record_mock_delivery`` when the gate status passed in from the
    API layer is not SATISFIED -- a defensive re-assertion so no receipt is ever
    written before the human approval gate, even if the API precondition is
    somehow bypassed (fail closed).
    """


@dataclass(frozen=True, slots=True)
class RecordedNotificationDraft:
    """Durable read model for one persisted notification draft.

    ``created`` is ``False`` when a draft already existed for the current
    (case, version) and was returned instead of writing a second one -- the
    idempotent record-or-get path.
    """

    id: UUID
    case_id: UUID
    case_version: int
    decision_id: UUID
    content_vi: str
    content_hash: str
    created_by: UUID
    created_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class RecordedCommunicationReceipt:
    """Durable read model for one persisted mock delivery receipt."""

    id: UUID
    draft_id: UUID
    delivered_via: str
    content_hash: str
    receipt_note_vi: str | None
    recorded_by: UUID
    created_at: datetime


class NotificationRepository(Protocol):
    """The credit notification's full durable-state surface."""

    async def create_draft(
        self,
        *,
        draft_id: UUID,
        case_id: UUID,
        created_by: UUID,
    ) -> RecordedNotificationDraft: ...

    async def load_draft(self, case_id: UUID) -> RecordedNotificationDraft | None: ...

    async def load_receipt(
        self, draft_id: UUID
    ) -> RecordedCommunicationReceipt | None: ...

    async def record_mock_delivery(
        self,
        *,
        receipt_id: UUID,
        draft_id: UUID,
        content_hash: str,
        receipt_note_vi: str | None,
        recorded_by: UUID,
        gate_satisfied: bool,
    ) -> RecordedCommunicationReceipt: ...
