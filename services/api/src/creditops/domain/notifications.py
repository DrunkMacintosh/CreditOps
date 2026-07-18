"""Credit notification draft + mock communication receipt: the stage-7 records.

Master design section 5 giai đoạn 7 ("Thông báo tín dụng cho khách hàng"):

- A ``CreditNotificationDraft`` is derived ONLY from a recorded
  ``HumanCreditDecision`` whose decision PERMITS a notification -- i.e. an
  approval outcome (``APPROVED_AS_PROPOSED`` / ``APPROVED_WITH_CONDITIONS``,
  ``domain.credit_decisions.APPROVAL_DECISIONS``).  No agent sends anything; the
  draft is deterministic template output, never an LLM generation.
- The rendered content ALWAYS embeds two mandatory Vietnamese sentences: the
  canonical synthetic-data notice (``domain.synthetic_notice.SYNTHETIC_NOTICE_VI``)
  and the fixed disclaimer ``NOT_A_DISBURSEMENT_NOTICE_VI`` -- a notification is
  never a disbursement confirmation (spec: "Thông báo tín dụng không phải xác
  nhận giải ngân.").  Both are enforced as frozen-model invariants so a draft
  that lacks either can never be constructed (fail closed).
- Delivery is a LABELLED MOCK.  A ``CommunicationReceipt`` records the delivery
  through the single synthetic channel ``MOCK_DELIVERY_CHANNEL`` and pins the
  EXACT content sha256 of the draft it delivered; a receipt whose hash does not
  equal its draft's is a contract violation (enforced again in the migration).

Everything here is a pure, frozen value object.  Append-only semantics, the
one-draft-per-version idempotency key, and the receipt/draft hash equality live
in the migration and the Postgres adapter.

All data is synthetic and created solely for demonstration.
"""

from __future__ import annotations

import hashlib
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.credit_decisions import (
    APPROVAL_DECISIONS,
    ApprovedTerms,
    CreditDecisionType,
    HumanCreditDecision,
)
from creditops.domain.ids import CaseId
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI

type CreditNotificationDraftId = UUID
type CommunicationReceiptId = UUID

_SHA256_HEX = r"^[0-9a-f]{64}$"

#: The single synthetic delivery channel.  Delivery in this prototype is a
#: LABELLED MOCK: nothing is ever sent to a customer.  No official SHB channel
#: mapping exists; this constant is the only accepted ``delivered_via`` value and
#: the migration's CHECK mirrors it exactly.
MOCK_DELIVERY_CHANNEL: Final = "MOCK_CHANNEL"

#: The fixed UI/notice disclaimer that MUST appear in every notification body: a
#: credit notification is never a disbursement confirmation (master design
#: section 5 giai đoạn 7).  Pinned verbatim; change only through a reviewed
#: governance decision.
NOT_A_DISBURSEMENT_NOTICE_VI: Final = (
    "Thông báo tín dụng không phải xác nhận giải ngân."
)

#: Deterministic Vietnamese label for each permitting decision outcome.  Only
#: approval outcomes ever reach the renderer, so only those are mapped.
_DECISION_LABEL_VI: Final[dict[CreditDecisionType, str]] = {
    CreditDecisionType.APPROVED_AS_PROPOSED: "Phê duyệt theo đề xuất",
    CreditDecisionType.APPROVED_WITH_CONDITIONS: "Phê duyệt có điều kiện",
}

#: Rendered when an optional approved-term field was not provided.  The spec
#: forbids model-invented values, so an unknown field is labelled, never guessed.
_NOT_PROVIDED_VI: Final = "Không cung cấp"


def compute_content_hash(content_vi: str) -> str:
    """Canonical sha256 hex over the exact notification content (pure; no I/O).

    The hash is over the UTF-8 bytes of the rendered content verbatim, so the
    same content always hashes identically and any drift is detectable.
    """

    return hashlib.sha256(content_vi.encode("utf-8")).hexdigest()


def render_notification_content_vi(
    *,
    decision: HumanCreditDecision,
    terms: ApprovedTerms | None,
) -> str:
    """Deterministically assemble the Vietnamese notification body.

    Pure string assembly from the recorded decision and its approved terms --
    NO LLM, no randomness, stable across calls for identical inputs.  The body
    states the approval outcome, the approved amount / term / rate (or a labelled
    NOT-PROVIDED marker; never an invented value), the pre-signing / pre-
    disbursement conditions, and ALWAYS embeds both mandatory sentences.

    Fails closed: a non-approval decision can never yield notification content --
    only ``APPROVAL_DECISIONS`` are permitted to reach here.
    """

    if decision.decision not in APPROVAL_DECISIONS:
        raise ValueError(
            f"{decision.decision.value} does not permit a credit notification"
        )

    amount_line = _NOT_PROVIDED_VI
    rate_line = _NOT_PROVIDED_VI
    term_line = _NOT_PROVIDED_VI
    if terms is not None:
        if terms.amount is not None:
            amount_line = (
                f"{terms.amount} {terms.currency}"
                if terms.currency is not None
                else f"{terms.amount}"
            )
        if terms.rate is not None:
            rate_line = f"{terms.rate}"
        if terms.term is not None:
            term_line = terms.term

    if decision.conditions:
        conditions_block = "\n".join(f"- {c}" for c in decision.conditions)
    else:
        conditions_block = "Không có điều kiện bổ sung."

    lines = [
        "THÔNG BÁO TÍN DỤNG (DỮ LIỆU MÔ PHỎNG)",
        "",
        f"Kết quả phê duyệt của cấp có thẩm quyền: {_DECISION_LABEL_VI[decision.decision]}",
        "",
        f"Số tiền: {amount_line}",
        f"Thời hạn: {term_line}",
        f"Lãi suất hoặc nguyên tắc xác định: {rate_line}",
        "",
        "Điều kiện trước khi ký/giải ngân:",
        conditions_block,
        "",
        NOT_A_DISBURSEMENT_NOTICE_VI,
        "",
        SYNTHETIC_NOTICE_VI,
    ]
    return "\n".join(lines)


class CreditNotificationDraft(BaseModel):
    """One deterministic notification draft, bound 1:1 to a case version.

    The draft binds the exact decision it derives from and freezes the rendered
    content with its sha256.  Append-only once persisted; one per case version
    (a revision bumps the case version).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: CreditNotificationDraftId
    case_id: CaseId
    case_version: int = Field(ge=1)
    decision_id: UUID
    content_vi: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_HEX)
    created_by: UUID

    @model_validator(mode="after")
    def _content_is_hashed_and_labelled(self) -> Self:
        if self.content_hash != compute_content_hash(self.content_vi):
            raise ValueError(
                "content_hash must equal the canonical sha256 of content_vi"
            )
        if NOT_A_DISBURSEMENT_NOTICE_VI not in self.content_vi:
            raise ValueError(
                "notification content must embed the not-a-disbursement disclaimer"
            )
        if SYNTHETIC_NOTICE_VI not in self.content_vi:
            raise ValueError(
                "notification content must embed the synthetic-data notice"
            )
        return self


class CommunicationReceipt(BaseModel):
    """The immutable receipt of a LABELLED MOCK delivery, 1:1 with its draft.

    ``delivered_via`` is always ``MOCK_DELIVERY_CHANNEL`` (nothing is ever sent)
    and ``content_hash`` pins the EXACT content delivered -- it must equal the
    draft's hash, so a receipt can never claim to have delivered content that
    drifted from the frozen draft.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: CommunicationReceiptId
    draft_id: CreditNotificationDraftId
    delivered_via: str
    content_hash: str = Field(pattern=_SHA256_HEX)
    receipt_note_vi: str | None = Field(default=None, min_length=1, max_length=4000)
    recorded_by: UUID

    @model_validator(mode="after")
    def _delivery_is_the_labelled_mock(self) -> Self:
        if self.delivered_via != MOCK_DELIVERY_CHANNEL:
            raise ValueError(
                f"delivered_via must be the labelled mock channel "
                f"{MOCK_DELIVERY_CHANNEL!r}"
            )
        return self


def build_notification_draft(
    *,
    draft_id: UUID,
    decision: HumanCreditDecision,
    terms: ApprovedTerms | None,
    created_by: UUID,
) -> CreditNotificationDraft:
    """Render + hash a draft bound to ``decision`` (fails closed on non-approval)."""

    content_vi = render_notification_content_vi(decision=decision, terms=terms)
    return CreditNotificationDraft(
        id=draft_id,
        case_id=decision.case_id,
        case_version=decision.case_version,
        decision_id=decision.id,
        content_vi=content_vi,
        content_hash=compute_content_hash(content_vi),
        created_by=created_by,
    )


__all__ = [
    "MOCK_DELIVERY_CHANNEL",
    "NOT_A_DISBURSEMENT_NOTICE_VI",
    "CommunicationReceipt",
    "CommunicationReceiptId",
    "CreditNotificationDraft",
    "CreditNotificationDraftId",
    "build_notification_draft",
    "compute_content_hash",
    "render_notification_content_vi",
]
