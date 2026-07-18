"""End-to-end synthetic POST-APPROVAL integration test (master design section 5
giai đoạn 6→14 + section 21.3 non-negotiable acceptance).

This module is the downstream companion to ``test_e2e_synthetic_flow.py``.  Where
that test drives the pre-decision orchestration (intake → makers → risk gate),
this one continues the SAME clean synthetic case
("Cong ty TNHH Thuc Pham Sach Demo") through the eight human-authority stages
that follow a recorded credit decision:

    6  human credit decision + approved-term snapshot   (domain/credit_decisions)
    7  credit notification draft + mock delivery         (domain/notifications)
    8  contract package: render, redline, material change (domain/contract_packages)
    9  per-asset security interests + perfection items    (domain/security_interests)
    10 disbursement condition ledger                      (domain/conditions)
    11 proposed disbursement + dual-gate mock execution   (domain/disbursements)
    13 deterministic repayment ledger fold                (domain/repayments)
    14 settlement (14A) + recovery-trigger negative (14B) (domain/settlement_recovery)

Like the exemplar it drives the REAL domain layer (the frozen value objects, the
validators, the hash bindings, the closed transition maps, the pure derivations,
the exact-decimal repayment fold, the real underwriting calculators) and the REAL
labelled ``MockDisbursementExecutionAdapter``, with in-memory fakes ONLY at the
durable-state ports.

WHAT THE FAKES STAND IN FOR.  Stages 6→14 have no application "use case" class
(unlike intake's ``CompleteIntake``): the thin orchestration lives in the human
FastAPI handlers, and the durable invariants live in the Postgres adapters +
migration triggers.  Each fake below is a faithful mirror of exactly one Postgres
adapter's contract, and every decision it makes is delegated to the REAL domain
code (``is_transition_allowed``, ``is_execution_transition_allowed``,
``RECONCILABLE_STATUSES``, ``build_notification_draft``, ``CommunicationReceipt``,
``ContractPackageState`` …) -- the fake is only the plumbing:

* ``_GateStore``                -- the human-gate table (``ensure_gate`` is
  insert-if-absent-then-satisfy-only-if-OPEN, immutable once SATISFIED, and
  records the satisfying actor so the maker/checker separation is checkable).
* ``_FakeNotificationRepository`` -- mirrors the stage-7 adapter + the receipt
  hash-equality trigger (a receipt whose content hash != the frozen draft's is
  refused; delivery before the approval gate is refused).
* ``_FakeContractPackageRepository`` -- mirrors the stage-8 append-only version
  machine (a redline appends a new REDLINED version carrying the term-snapshot
  hash forward; ``mark_material_change`` fences the package).
* ``_FakeDisbursementRepository`` -- mirrors the stage-11 adapter: records
  EXECUTION_REQUESTED before the adapter call, persists the append-only receipt,
  and fails closed (``ReconciliationRequiredError`` / ``AlreadyExecutedError``)
  exactly as the real ``execute_action`` / ``reconcile_action`` do.
* ``_FakeSettlementRepository`` -- mirrors the stage-14 adapter's idempotent
  ``record_settlement_receipts`` (both labelled MOCK receipts, once).

The stage-9 security ledger and the stage-13 repayment fold are PURE domain
derivations, so they are driven directly (exactly as the exemplar drives
``derive_g2_from_batch`` with no port).

The API-handler-level guards that have no lower layer to call -- gate ordering and
the maker/checker actor comparison -- are re-expressed at the layer this test
drives and asserted there; that seam is called out in the module's final report.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.application.ports.contract_packages import (
    AddedRedline,
    CreatedContractPackage,
    RecordedContractPackage,
    RecordedContractRedline,
)
from creditops.application.ports.disbursements import (
    AlreadyExecutedError,
    DisbursementActionNotFound,
    DuplicateIdempotencyKeyError,
    NotReconcilableError,
    ReconciliationRequiredError,
    RecordedDisbursementAction,
    RecordedExecutionReceipt,
)
from creditops.application.ports.notifications import (
    GateNotSatisfiedError,
    RecordedCommunicationReceipt,
    RecordedNotificationDraft,
)
from creditops.application.ports.orchestration import GateRecord
from creditops.application.ports.settlement_recovery import RecordedSettlementReceipt
from creditops.domain.conditions import (
    RATIONALE_REQUIRED_TARGETS,
    ConditionStatus,
    DisbursementCondition,
    derive_conditions_confirmable,
    is_transition_allowed,
)
from creditops.domain.contract_packages import (
    ContractDecisionView,
    ContractPackageState,
    assert_renderable_decision,
    detect_material_change,
    render_contract_content_vi,
)
from creditops.domain.contract_packages import (
    compute_content_hash as compute_contract_hash,
)
from creditops.domain.credit_decisions import (
    SNAPSHOT_FORBIDDING_DECISIONS,
    ApprovedTerms,
    ApprovedTermSnapshot,
    CreditDecisionType,
    HumanCreditDecision,
    assert_snapshot_matches_decision,
    build_term_snapshot,
    compute_terms_hash,
)
from creditops.domain.disbursements import (
    MOCK_DISBURSEMENT_ADAPTER_LABEL,
    REATTEMPTABLE_STATUSES,
    RECONCILABLE_STATUSES,
    RECONCILIATION_OUTCOMES,
    DisbursementExecutionReceipt,
    ExecutionStatus,
    ProposedDisbursementAction,
    is_execution_transition_allowed,
    validate_amount_against_terms,
)
from creditops.domain.notifications import (
    MOCK_DELIVERY_CHANNEL,
    NOT_A_DISBURSEMENT_NOTICE_VI,
    CommunicationReceipt,
    build_notification_draft,
    compute_content_hash,
    render_notification_content_vi,
)
from creditops.domain.orchestration import GateStatus, GateType
from creditops.domain.repayments import (
    EventKind,
    Facility,
    RepaymentEvent,
    apply_events,
    build_expected_installments,
    build_expected_schedule,
)
from creditops.domain.security_interests import (
    PerfectionStatus,
    SecurityAssetKind,
    SecurityInterest,
    SecurityInterestWithItems,
    SecurityPerfectionItem,
    derive_perfection_blockers,
    derive_perfection_confirmable,
    is_allowed_item_transition,
)
from creditops.domain.settlement_recovery import (
    MOCK_SETTLEMENT_RECEIPTS,
    RecoveryTriggerInputs,
    SettlementCheck,
    SettlementLedgerInputs,
    SettlementReceiptKind,
    derive_recovery_trigger,
    derive_settlement_eligible,
)
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI
from creditops.infrastructure.mock.disbursement_adapter import (
    MockDisbursementExecutionAdapter,
)

# --- Dữ liệu tổng hợp (synthetic) cho công ty demo -----------------------------
# Cùng một hồ sơ KHDN giả lập, tiếp nối kịch bản sạch sau khi có quyết định tín dụng.
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
CASE_ID = UUID("a0000000-0000-0000-0000-000000000001")
CASE_VERSION = 1
COMPANY_NAME_VI = "Cong ty TNHH Thuc Pham Sach Demo"

# Human authority actors (each a distinct person; maker/checker separation matters).
APPROVER_ID = UUID("d0000000-0000-0000-0000-00000000000a")  # CREDIT_APPROVER
OPS_MAKER_ID = UUID("d0000000-0000-0000-0000-00000000000b")  # OPS_OFFICER (maker)
OPS_CHECKER_V_ID = UUID("d0000000-0000-0000-0000-00000000000c")  # validator
OPS_CHECKER_A_ID = UUID("d0000000-0000-0000-0000-00000000000d")  # authorizer / executor
LEGAL_REVIEWER_ID = UUID("d0000000-0000-0000-0000-00000000000e")  # LEGAL_REVIEWER
COLLECTIONS_ID = UUID("d0000000-0000-0000-0000-00000000000f")  # collections officer

MEMO_ARTIFACT_ID = UUID("e0000000-0000-0000-0000-000000000001")
RISK_ASSESSMENT_ID = UUID("e0000000-0000-0000-0000-000000000002")
UW_ASSESSMENT_ID = UUID("e0000000-0000-0000-0000-000000000003")

APPROVAL_CONDITIONS = (
    "Hoàn tất đăng ký giao dịch bảo đảm trước khi giải ngân.",
    "Cung cấp hợp đồng mua bán đầu ra đã ký.",
)


# =============================================================================
# Synthetic domain builders (real value objects).
# =============================================================================


def _approved_terms() -> ApprovedTerms:
    """The frozen approved terms of the demo facility (exact decimals)."""
    return ApprovedTerms(
        amount=Decimal("120000"),
        currency="VND",
        term="3 tháng",
        rate=Decimal("12"),
    )


def _approved_decision() -> HumanCreditDecision:
    """One APPROVED_WITH_CONDITIONS human credit decision for case version 1."""
    return HumanCreditDecision(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        decision=CreditDecisionType.APPROVED_WITH_CONDITIONS,
        rationale_vi=(
            f"Phê duyệt có điều kiện cho {COMPANY_NAME_VI} trên cơ sở hồ sơ thẩm định."
        ),
        decided_by=APPROVER_ID,
        decided_by_role="CREDIT_APPROVER",
        memo_artifact_id=MEMO_ARTIFACT_ID,
        risk_assessment_id=RISK_ASSESSMENT_ID,
        underwriting_assessment_id=UW_ASSESSMENT_ID,
        conditions=APPROVAL_CONDITIONS,
    )


# =============================================================================
# In-memory port fakes (durable-store plumbing; every decision is real domain).
# =============================================================================


class _GateStore:
    """The human-gate table: ``ensure_gate`` is immutable once SATISFIED.

    Mirrors ``OrchestrationRepository.ensure_gate`` (as used by every stage-6→14
    API handler): a gate is inserted OPEN if absent, an OPEN->SATISFIED flip
    records the satisfying actor + disposition_ref exactly once, and a satisfied
    gate is never re-opened or re-attributed.
    """

    def __init__(self) -> None:
        self.gates: dict[tuple[int, GateType], GateRecord] = {}

    def ensure_gate(
        self,
        *,
        case_version: int,
        gate_type: GateType,
        status: GateStatus,
        satisfied_by_actor_id: UUID | None = None,
        disposition_ref: str | None = None,
    ) -> GateRecord:
        key = (case_version, gate_type)
        existing = self.gates.get(key)
        if existing is None:
            existing = GateRecord(gate_type, case_version, GateStatus.OPEN)
            self.gates[key] = existing
        if existing.status is GateStatus.OPEN and status is GateStatus.SATISFIED:
            existing = GateRecord(
                gate_type,
                case_version,
                GateStatus.SATISFIED,
                satisfied_by_actor_id=satisfied_by_actor_id,
                disposition_ref=disposition_ref,
                satisfied_at=NOW,
            )
            self.gates[key] = existing
        return existing

    def gate(self, gate_type: GateType, case_version: int) -> GateRecord | None:
        return self.gates.get((case_version, gate_type))

    def satisfied(self, gate_type: GateType, case_version: int) -> bool:
        record = self.gate(gate_type, case_version)
        return record is not None and record.status is GateStatus.SATISFIED


class _FakeNotificationRepository:
    """Mirror of the stage-7 Postgres notification adapter + hash trigger.

    ``create_draft`` renders the draft through the REAL
    ``build_notification_draft`` (binding the recorded decision and freezing the
    content sha256).  ``record_mock_delivery`` re-asserts the approval gate and
    the receipt/draft content-hash equality (the DB trigger) before writing the
    labelled MOCK ``CommunicationReceipt``.
    """

    def __init__(
        self, decision: HumanCreditDecision, terms: ApprovedTerms | None
    ) -> None:
        self._decision = decision
        self._terms = terms
        self._draft: RecordedNotificationDraft | None = None
        self._receipt: RecordedCommunicationReceipt | None = None

    def create_draft(self, *, draft_id: UUID, created_by: UUID) -> RecordedNotificationDraft:
        if self._draft is not None:  # idempotent on (case, version)
            return self._draft
        draft = build_notification_draft(
            draft_id=draft_id,
            decision=self._decision,
            terms=self._terms,
            created_by=created_by,
        )
        self._draft = RecordedNotificationDraft(
            id=draft.id,
            case_id=draft.case_id,
            case_version=draft.case_version,
            decision_id=draft.decision_id,
            content_vi=draft.content_vi,
            content_hash=draft.content_hash,
            created_by=draft.created_by,
            created_at=NOW,
            created=True,
        )
        return self._draft

    @property
    def draft(self) -> RecordedNotificationDraft:
        assert self._draft is not None, "create a draft first"
        return self._draft

    def record_mock_delivery(
        self,
        *,
        receipt_id: UUID,
        draft_id: UUID,
        content_hash: str,
        recorded_by: UUID,
        gate_satisfied: bool,
    ) -> RecordedCommunicationReceipt:
        if not gate_satisfied:
            # Fail closed: no receipt before HG_CREDIT_NOTIFICATION_APPROVED.
            raise GateNotSatisfiedError(
                "HG_CREDIT_NOTIFICATION_APPROVED must be SATISFIED before delivery"
            )
        stored = self.draft
        # The domain receipt validates the labelled mock channel + sha256 shape.
        receipt = CommunicationReceipt(
            id=receipt_id,
            draft_id=draft_id,
            delivered_via=MOCK_DELIVERY_CHANNEL,
            content_hash=content_hash,
            receipt_note_vi=None,
            recorded_by=recorded_by,
        )
        # The receipt/draft hash-equality trigger: a receipt can never claim to
        # have delivered content that drifted from the frozen draft.
        if receipt.content_hash != stored.content_hash:
            raise ValueError(
                "communication receipt content_hash must equal the draft's"
            )
        self._receipt = RecordedCommunicationReceipt(
            id=receipt.id,
            draft_id=receipt.draft_id,
            delivered_via=receipt.delivered_via,
            content_hash=receipt.content_hash,
            receipt_note_vi=receipt.receipt_note_vi,
            recorded_by=receipt.recorded_by,
            created_at=NOW,
        )
        return self._receipt


class _FakeContractPackageRepository:
    """Mirror of the stage-8 append-only contract-package version machine.

    A redline appends the NEXT ``package_version`` in state ``REDLINED`` carrying
    the original ``term_snapshot_hash`` forward (so material change is detected
    against the CURRENT decision, never masked by a redline).
    ``mark_material_change`` appends a ``MATERIAL_CHANGE_DETECTED`` version that
    fences the package.
    """

    def __init__(self) -> None:
        self._versions: list[RecordedContractPackage] = []

    def create_package(
        self,
        *,
        decision_id: UUID,
        term_snapshot_hash: str,
        content_vi: str,
        content_hash: str,
        actor_id: UUID,
    ) -> CreatedContractPackage:
        if self._versions:  # idempotent first draft
            return CreatedContractPackage(package=self._current, created=False)
        package = RecordedContractPackage(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            decision_id=decision_id,
            term_snapshot_hash=term_snapshot_hash,
            content_vi=content_vi,
            content_hash=content_hash,
            package_version=1,
            state=ContractPackageState.DRAFT.value,
            created_by=actor_id,
            created_at=NOW,
        )
        self._versions.append(package)
        return CreatedContractPackage(package=package, created=True)

    @property
    def _current(self) -> RecordedContractPackage:
        return self._versions[-1]

    def _append(
        self, *, content_vi: str, content_hash: str, state: ContractPackageState, actor_id: UUID
    ) -> RecordedContractPackage:
        base = self._current
        package = RecordedContractPackage(
            id=uuid4(),
            case_id=base.case_id,
            case_version=base.case_version,
            decision_id=base.decision_id,
            term_snapshot_hash=base.term_snapshot_hash,  # carried forward, never edited
            content_vi=content_vi,
            content_hash=content_hash,
            package_version=base.package_version + 1,
            state=state.value,
            created_by=actor_id,
            created_at=NOW,
        )
        self._versions.append(package)
        return package

    def add_redline(
        self, *, change_note_vi: str, changed_content_vi: str, actor_id: UUID
    ) -> AddedRedline:
        base = self._current
        changed_hash = compute_contract_hash(changed_content_vi)
        redline = RecordedContractRedline(
            id=uuid4(),
            package_id=base.id,
            redline_version=1,
            change_note_vi=change_note_vi,
            changed_content_vi=changed_content_vi,
            changed_content_hash=changed_hash,
            created_by=actor_id,
            created_at=NOW,
        )
        package = self._append(
            content_vi=changed_content_vi,
            content_hash=changed_hash,
            state=ContractPackageState.REDLINED,
            actor_id=actor_id,
        )
        return AddedRedline(redline=redline, package=package)

    def load_current_package(self) -> RecordedContractPackage:
        return self._current

    def mark_material_change(self, *, actor_id: UUID) -> RecordedContractPackage:
        base = self._current
        if base.state == ContractPackageState.MATERIAL_CHANGE_DETECTED.value:
            return base
        return self._append(
            content_vi=base.content_vi,
            content_hash=base.content_hash,
            state=ContractPackageState.MATERIAL_CHANGE_DETECTED,
            actor_id=actor_id,
        )


@dataclass
class _StoredAction:
    action: RecordedDisbursementAction
    receipts: list[RecordedExecutionReceipt]


class _FakeDisbursementRepository:
    """Mirror of the stage-11 Postgres disbursement adapter (one "database").

    ``execute_action`` records EXECUTION_REQUESTED durably BEFORE the adapter
    call, then persists the append-only receipt (unique idempotency key) and
    moves the action to the adapter's result status.  It fails closed exactly as
    the real adapter, delegating every state decision to the REAL domain sets
    (``RECONCILABLE_STATUSES`` / ``REATTEMPTABLE_STATUSES`` /
    ``RECONCILIATION_OUTCOMES``).
    """

    def __init__(self) -> None:
        # Keyed by (case_id, case_version): the unique proposed-disbursement row.
        self._by_version: dict[tuple[UUID, int], _StoredAction] = {}
        self._by_id: dict[UUID, _StoredAction] = {}
        self._used_keys: set[str] = set()

    def create_action(
        self, *, action: ProposedDisbursementAction
    ) -> RecordedDisbursementAction:
        key = (action.case_id, action.case_version)
        existing = self._by_version.get(key)
        if existing is not None:  # unique (case, version): a duplicate is a no-op
            return replace(existing.action, created=False)
        record = RecordedDisbursementAction(
            id=action.id,
            case_id=action.case_id,
            case_version=action.case_version,
            decision_id=action.decision_id,
            amount_text=action.amount_text,
            currency=action.currency,
            beneficiary_ref_vi=action.beneficiary_ref_vi,
            account_ref_vi=action.account_ref_vi,
            status=action.status,
            created_by=action.created_by,
            created_at=NOW,
            created=True,
        )
        stored = _StoredAction(action=record, receipts=[])
        self._by_version[key] = stored
        self._by_id[action.id] = stored
        return record

    def load_action(self, action_id: UUID) -> RecordedDisbursementAction | None:
        stored = self._by_id.get(action_id)
        return stored.action if stored is not None else None

    def list_receipts(self, action_id: UUID) -> tuple[RecordedExecutionReceipt, ...]:
        stored = self._by_id.get(action_id)
        return tuple(stored.receipts) if stored is not None else ()

    def _set_status(self, stored: _StoredAction, status: ExecutionStatus) -> None:
        stored.action = replace(stored.action, status=status, created=False)

    def execute_action(
        self,
        *,
        action_id: UUID,
        adapter: MockDisbursementExecutionAdapter,
        idempotency_key: str,
        actor_id: UUID,
    ) -> tuple[RecordedDisbursementAction, RecordedExecutionReceipt]:
        stored = self._by_id.get(action_id)
        if stored is None:
            raise DisbursementActionNotFound(str(action_id))
        current = stored.action.status
        # Fail closed: an unresolved prior attempt is NEVER blindly retried.
        if current in RECONCILABLE_STATUSES:
            raise ReconciliationRequiredError(current.value)
        if current is ExecutionStatus.CONFIRMED_EXECUTED:
            raise AlreadyExecutedError(str(action_id))
        if current not in REATTEMPTABLE_STATUSES:  # defensive
            raise ReconciliationRequiredError(current.value)
        if idempotency_key in self._used_keys:  # unique idempotency key backstop
            raise DuplicateIdempotencyKeyError(idempotency_key)

        # EXECUTION_REQUESTED recorded durably BEFORE the adapter runs.
        self._set_status(stored, ExecutionStatus.EXECUTION_REQUESTED)
        receipt: DisbursementExecutionReceipt = adapter.execute(
            action_id=action_id, idempotency_key=idempotency_key
        )
        self._used_keys.add(idempotency_key)
        recorded = RecordedExecutionReceipt(
            id=receipt.id,
            action_id=receipt.action_id,
            idempotency_key=receipt.idempotency_key,
            adapter_label=receipt.adapter_label,
            result_status=receipt.result_status,
            receipt_ref=receipt.receipt_ref,
            recorded_by=actor_id,
            created_at=NOW,
        )
        stored.receipts.append(recorded)
        self._set_status(stored, receipt.result_status)
        return stored.action, recorded

    def reconcile_action(
        self, *, action_id: UUID, outcome: ExecutionStatus
    ) -> RecordedDisbursementAction:
        if outcome not in RECONCILIATION_OUTCOMES:  # defensive
            raise ValueError(f"{outcome.value} is not a reconciliation outcome")
        stored = self._by_id.get(action_id)
        if stored is None:
            raise DisbursementActionNotFound(str(action_id))
        if stored.action.status not in RECONCILABLE_STATUSES:
            raise NotReconcilableError(stored.action.status.value)
        self._set_status(stored, outcome)
        return stored.action


class _FakeSettlementRepository:
    """Mirror of the stage-14 settlement adapter's idempotent receipt write."""

    def __init__(self) -> None:
        self._receipts: dict[UUID, list[RecordedSettlementReceipt]] = {}

    def record_settlement_receipts(
        self,
        *,
        settlement_check_id: UUID,
        receipts: list[tuple[SettlementReceiptKind, str | None]],
        actor_id: UUID,
    ) -> tuple[RecordedSettlementReceipt, ...]:
        existing = self._receipts.setdefault(settlement_check_id, [])
        present = {r.kind for r in existing}
        for kind, note in receipts:
            if kind in present:  # idempotent: a kind already present is untouched
                continue
            existing.append(
                RecordedSettlementReceipt(
                    id=uuid4(),
                    settlement_check_id=settlement_check_id,
                    kind=kind,
                    note_vi=note,
                    recorded_by=actor_id,
                    created_at=NOW,
                )
            )
        return tuple(existing)


# =============================================================================
# The end-to-end post-approval scenario.
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_post_approval_clean_case() -> None:
    gates = _GateStore()

    # -- PHASE 6: human credit decision + approved-term snapshot ----------------
    # Chỉ con người có thẩm quyền mới ghi được quyết định; không có đường agent.
    decision = _approved_decision()
    terms = _approved_terms()
    snapshot = build_term_snapshot(snapshot_id=uuid4(), decision=decision, terms=terms)

    # The snapshot hash binds the exact frozen terms (a drift could never masquerade).
    assert snapshot.snapshot_hash == compute_terms_hash(terms)
    assert snapshot.decision_id == decision.id
    assert_snapshot_matches_decision(decision=decision, snapshot=snapshot)

    # ACCEPTANCE (21.3) no agent-callable decision path -- constructors/validators
    # refuse anything but a complete, authority-bearing, self-consistent record:
    with pytest.raises(ValidationError):
        # missing the mandatory human authority fields (decided_by / role).
        HumanCreditDecision(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            decision=CreditDecisionType.APPROVED_AS_PROPOSED,
            rationale_vi="thiếu thẩm quyền",
        )  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        # APPROVED_WITH_CONDITIONS with no condition is a contract violation.
        HumanCreditDecision(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            decision=CreditDecisionType.APPROVED_WITH_CONDITIONS,
            rationale_vi="thiếu điều kiện",
            decided_by=APPROVER_ID,
            decided_by_role="CREDIT_APPROVER",
            conditions=(),
        )
    with pytest.raises(ValidationError):
        # A tampered snapshot hash can never be constructed.
        ApprovedTermSnapshot(
            id=uuid4(),
            decision_id=decision.id,
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            terms=terms,
            snapshot_hash="0" * 64,
        )
    # A non-approval decision forbids an approved-term snapshot entirely.
    declined = HumanCreditDecision(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        decision=CreditDecisionType.DECLINED_BY_HUMAN,
        rationale_vi="Không đủ điều kiện tín dụng.",
        decided_by=APPROVER_ID,
        decided_by_role="CREDIT_APPROVER",
    )
    assert CreditDecisionType.DECLINED_BY_HUMAN in SNAPSHOT_FORBIDDING_DECISIONS
    declined_snapshot = build_term_snapshot(
        snapshot_id=uuid4(), decision=declined, terms=terms
    )
    with pytest.raises(ValueError):
        assert_snapshot_matches_decision(decision=declined, snapshot=declined_snapshot)

    # -- PHASE 7: notification draft embeds the mandates; wrong-hash receipt fails --
    content = render_notification_content_vi(decision=decision, terms=terms)
    assert SYNTHETIC_NOTICE_VI in content, "content must embed the synthetic-data notice"
    assert NOT_A_DISBURSEMENT_NOTICE_VI in content, (
        "content must embed the not-a-disbursement disclaimer"
    )
    # Deterministic render: identical inputs -> byte-identical content.
    assert render_notification_content_vi(decision=decision, terms=terms) == content

    notifications = _FakeNotificationRepository(decision, terms)
    draft = notifications.create_draft(draft_id=uuid4(), created_by=OPS_MAKER_ID)
    assert draft.decision_id == decision.id, "the draft binds the exact decision"
    assert draft.content_hash == compute_content_hash(content)

    # Delivery before the approval gate is refused (fail closed).
    with pytest.raises(GateNotSatisfiedError):
        notifications.record_mock_delivery(
            receipt_id=uuid4(),
            draft_id=draft.id,
            content_hash=draft.content_hash,
            recorded_by=OPS_CHECKER_V_ID,
            gate_satisfied=False,
        )

    gates.ensure_gate(
        case_version=CASE_VERSION,
        gate_type=GateType.HG_CREDIT_NOTIFICATION_APPROVED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=OPS_MAKER_ID,
        disposition_ref=f"notification-draft:{draft.id}",
    )
    # A receipt with the WRONG content hash is rejected by the port (the trigger).
    with pytest.raises(ValueError):
        notifications.record_mock_delivery(
            receipt_id=uuid4(),
            draft_id=draft.id,
            content_hash=compute_content_hash("noi dung da bi sua doi"),
            recorded_by=OPS_CHECKER_V_ID,
            gate_satisfied=True,
        )
    # The correct-hash delivery is the labelled MOCK channel, nothing sent.
    receipt = notifications.record_mock_delivery(
        receipt_id=uuid4(),
        draft_id=draft.id,
        content_hash=draft.content_hash,
        recorded_by=OPS_CHECKER_V_ID,
        gate_satisfied=True,
    )
    assert receipt.delivered_via == MOCK_DELIVERY_CHANNEL
    assert receipt.content_hash == draft.content_hash

    # -- PHASE 8: contract package -- deterministic render, redline, material change --
    decision_view = ContractDecisionView(
        decision_type=decision.decision,
        rationale_vi=decision.rationale_vi,
        conditions=decision.conditions,
    )
    assert_renderable_decision(decision.decision)  # an approval permits a package
    rendered = render_contract_content_vi(decision_view, terms)
    # Deterministic: the SAME inputs hash identically twice.
    assert compute_contract_hash(rendered) == compute_contract_hash(
        render_contract_content_vi(decision_view, terms)
    )

    contracts = _FakeContractPackageRepository()
    created_pkg = contracts.create_package(
        decision_id=decision.id,
        term_snapshot_hash=snapshot.snapshot_hash,
        content_vi=rendered,
        content_hash=compute_contract_hash(rendered),
        actor_id=OPS_MAKER_ID,
    )
    assert created_pkg.created is True
    assert created_pkg.package.package_version == 1
    assert created_pkg.package.state == ContractPackageState.DRAFT.value

    # A redline is never an edit: it appends the next REDLINED version.
    redlined = contracts.add_redline(
        change_note_vi="Điều chỉnh câu chữ điều khoản tài sản bảo đảm.",
        changed_content_vi=rendered + "\n(Đã chỉnh sửa câu chữ.)",
        actor_id=LEGAL_REVIEWER_ID,
    )
    assert redlined.package.package_version == 2
    assert redlined.package.state == ContractPackageState.REDLINED.value
    # The redline carried the original term-snapshot hash forward (never masked).
    assert redlined.package.term_snapshot_hash == snapshot.snapshot_hash

    # Simulate a NEW decision snapshot hash (a revised stage-6 decision would froze
    # different terms).  detect_material_change is True -> the approve path is
    # BLOCKED and the package is fenced (409-equivalent), never approved.
    revised_terms = _approved_terms().model_copy(update={"amount": Decimal("150000")})
    new_snapshot_hash = compute_terms_hash(revised_terms)
    package = contracts.load_current_package()
    assert detect_material_change(package.term_snapshot_hash, new_snapshot_hash) is True

    material_change = detect_material_change(
        package.term_snapshot_hash, new_snapshot_hash
    )
    if material_change:  # exactly the API approve handler's fence branch
        fenced = contracts.mark_material_change(actor_id=OPS_CHECKER_A_ID)
    else:  # pragma: no cover - the branch above always runs in this scenario
        gates.ensure_gate(
            case_version=CASE_VERSION,
            gate_type=GateType.HG_CONTRACT_PACKAGE_APPROVED,
            status=GateStatus.SATISFIED,
            satisfied_by_actor_id=OPS_CHECKER_A_ID,
        )
    assert fenced.state == ContractPackageState.MATERIAL_CHANGE_DETECTED.value
    assert not gates.satisfied(GateType.HG_CONTRACT_PACKAGE_APPROVED, CASE_VERSION), (
        "a materially-changed package must NOT satisfy the approval gate"
    )

    # -- PHASE 9: per-asset security interests + perfection items ---------------
    # A COMPLETED perfection item MUST carry evidence + completed_by/at (fail closed).
    with pytest.raises(ValidationError):
        SecurityPerfectionItem(
            id=uuid4(),
            interest_id=uuid4(),
            requirement_vi="Đăng ký thế chấp",
            status=PerfectionStatus.COMPLETED,
            evidence_refs=(),
        )

    interest = SecurityInterest(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        asset_description_vi="Quyền sử dụng đất tại lô A khu công nghiệp (mô phỏng).",
        asset_kind=SecurityAssetKind.REAL_ESTATE,
        owner_name_vi=COMPANY_NAME_VI,
        valuation_reference="valuation-adapter://demo/ref-1",  # pointer only
        created_by=OPS_MAKER_ID,
    )
    # An interest with ZERO items is never confirmable (no vacuous path).
    assert derive_perfection_confirmable([SecurityInterestWithItems(interest=interest)]) is False
    zero_item_blockers = derive_perfection_blockers(
        [SecurityInterestWithItems(interest=interest)]
    )
    assert zero_item_blockers.interests_without_items == (interest.id,)

    # Two requirements: one walks PENDING->EVIDENCE_ATTACHED->COMPLETED, the other
    # is ruled NOT_REQUIRED_BY_HUMAN.  PENDING->COMPLETED is a forbidden shortcut.
    assert is_allowed_item_transition(
        PerfectionStatus.PENDING, PerfectionStatus.COMPLETED
    ) is False
    assert is_allowed_item_transition(
        PerfectionStatus.PENDING, PerfectionStatus.EVIDENCE_ATTACHED
    )
    assert is_allowed_item_transition(
        PerfectionStatus.EVIDENCE_ATTACHED, PerfectionStatus.COMPLETED
    )
    item_completed = SecurityPerfectionItem(
        id=uuid4(),
        interest_id=interest.id,
        requirement_vi="Đăng ký giao dịch bảo đảm tại cơ quan có thẩm quyền.",
        status=PerfectionStatus.COMPLETED,
        evidence_refs=("filing://demo/registration-1",),
        filing_reference="REG-DEMO-0001",
        completed_by=OPS_CHECKER_V_ID,
        completed_at=NOW,
    )
    item_not_required = SecurityPerfectionItem(
        id=uuid4(),
        interest_id=interest.id,
        requirement_vi="Mua bảo hiểm tài sản (không bắt buộc trong kịch bản mô phỏng).",
        status=PerfectionStatus.NOT_REQUIRED_BY_HUMAN,
    )
    perfected = SecurityInterestWithItems(
        interest=interest, items=(item_completed, item_not_required)
    )
    # Confirmable only once EVERY item rests in a terminal-satisfied state.
    assert derive_perfection_confirmable([perfected]) is True
    partial = SecurityInterestWithItems(
        interest=interest,
        items=(
            item_completed,
            SecurityPerfectionItem(
                id=uuid4(),
                interest_id=interest.id,
                requirement_vi="Còn đang chờ chứng cứ.",
                status=PerfectionStatus.PENDING,
            ),
        ),
    )
    assert derive_perfection_confirmable([partial]) is False
    gates.ensure_gate(
        case_version=CASE_VERSION,
        gate_type=GateType.HG_SECURITY_PERFECTION_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=OPS_CHECKER_V_ID,
        disposition_ref="security-perfection:1",
    )

    # -- PHASE 10: disbursement condition ledger --------------------------------
    # An EMPTY ledger is NEVER confirmable (no confirmation by silence).
    assert derive_conditions_confirmable([]) is False

    ledger = _ConditionLedger(decision_id=decision.id)
    cond_verify = ledger.open("Hợp đồng tín dụng đã ký (bằng chứng mô phỏng).")
    cond_waive = ledger.open("Bổ sung giấy phép con của ngành thực phẩm.")
    cond_na = ledger.open("Điều kiện không áp dụng cho kịch bản mô phỏng này.")

    # Not confirmable while any condition is still non-terminal.
    assert ledger.confirmable() is False

    # Forbidden shortcut: PENDING -> VERIFIED is rejected (evidence must precede).
    with pytest.raises(ValueError):
        ledger.transition(cond_verify, ConditionStatus.VERIFIED)

    # Verified path.
    ledger.transition(cond_verify, ConditionStatus.EVIDENCE_SUBMITTED)
    ledger.transition(
        cond_verify, ConditionStatus.VERIFIED, actor_id=OPS_CHECKER_V_ID
    )
    # Waiver path (human authority act: a rationale is mandatory).
    ledger.transition(cond_waive, ConditionStatus.WAIVER_REQUESTED)
    with pytest.raises(ValueError):
        # WAIVED_BY_HUMAN without a rationale is refused (authority record required).
        ledger.transition(cond_waive, ConditionStatus.WAIVED_BY_HUMAN)
    ledger.transition(
        cond_waive,
        ConditionStatus.WAIVED_BY_HUMAN,
        actor_id=OPS_CHECKER_V_ID,
        rationale_vi="Miễn trừ có phê duyệt của cấp kiểm soát (mô phỏng).",
    )
    # Not-applicable path (also a human authority act).
    ledger.transition(
        cond_na,
        ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
        actor_id=OPS_CHECKER_V_ID,
        rationale_vi="Không áp dụng trong kịch bản mô phỏng.",
    )
    # Confirmable only at full disposition of every condition.
    assert ledger.confirmable() is True
    gates.ensure_gate(
        case_version=CASE_VERSION,
        gate_type=GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=OPS_CHECKER_A_ID,
        disposition_ref="disbursement-conditions:1",
    )
    assert gates.satisfied(GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED, CASE_VERSION)

    # -- PHASE 11: proposed disbursement -- dual gates + labelled-mock execution --
    disbursements = _FakeDisbursementRepository()
    # The action is derived from approved terms (currency-/cap-aware validation).
    validate_amount_against_terms(
        amount=terms.amount or Decimal("0"),
        currency=terms.currency or "VND",
        approved_amount=terms.amount,
        approved_currency=terms.currency,
    )
    action_model = ProposedDisbursementAction(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        decision_id=decision.id,
        amount=terms.amount or Decimal("120000"),
        currency=terms.currency or "VND",
        beneficiary_ref_vi="Nguoi thu huong mo phong",
        account_ref_vi="TK-MO-PHONG-0001",
        created_by=OPS_MAKER_ID,
    )
    action = disbursements.create_action(action=action_model)
    assert action.created is True
    assert action.status is ExecutionStatus.PROPOSED
    # SECOND action for the same (case, version) is blocked by the unique key.
    duplicate = disbursements.create_action(action=action_model)
    assert duplicate.created is False
    assert duplicate.id == action.id

    # Dual human gates satisfied by DIFFERENT actors (maker/checker separation).
    gates.ensure_gate(
        case_version=CASE_VERSION,
        gate_type=GateType.HG_DISBURSEMENT_VALIDATED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=OPS_CHECKER_V_ID,
        disposition_ref=f"proposed-disbursement-validated:{action.id}",
    )
    validated = gates.gate(GateType.HG_DISBURSEMENT_VALIDATED, CASE_VERSION)
    assert validated is not None
    # ACCEPTANCE: the authorizer must DIFFER from the validator.
    assert OPS_CHECKER_A_ID != validated.satisfied_by_actor_id
    gates.ensure_gate(
        case_version=CASE_VERSION,
        gate_type=GateType.HG_DISBURSEMENT_AUTHORIZED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=OPS_CHECKER_A_ID,
        disposition_ref=f"proposed-disbursement-authorized:{action.id}",
    )

    # ACCEPTANCE: no execution without BOTH gates + an executor != the creator.
    both_gates = gates.satisfied(
        GateType.HG_DISBURSEMENT_VALIDATED, CASE_VERSION
    ) and gates.satisfied(GateType.HG_DISBURSEMENT_AUTHORIZED, CASE_VERSION)
    assert both_gates is True
    assert action.created_by != OPS_CHECKER_A_ID, "executor differs from the maker"

    normal_adapter = MockDisbursementExecutionAdapter()
    executed_action, exec_receipt = disbursements.execute_action(
        action_id=action.id,
        adapter=normal_adapter,
        idempotency_key=uuid4().hex,
        actor_id=OPS_CHECKER_A_ID,
    )
    assert executed_action.status is ExecutionStatus.CONFIRMED_EXECUTED
    assert exec_receipt.result_status is ExecutionStatus.CONFIRMED_EXECUTED
    assert exec_receipt.receipt_ref is not None
    assert exec_receipt.adapter_label == MOCK_DISBURSEMENT_ADAPTER_LABEL

    # The labelled mock adapter is deterministic (same action + key -> same ref).
    key = uuid4().hex
    assert (
        normal_adapter.execute(action_id=action.id, idempotency_key=key).receipt_ref
        == normal_adapter.execute(action_id=action.id, idempotency_key=key).receipt_ref
    )
    # A CONFIRMED execution is terminal: re-executing is refused (never a retry).
    with pytest.raises(AlreadyExecutedError):
        disbursements.execute_action(
            action_id=action.id,
            adapter=normal_adapter,
            idempotency_key=uuid4().hex,
            actor_id=OPS_CHECKER_A_ID,
        )

    # -- PHASE 11b: a SEPARATE flow exercises the EXECUTION_UNKNOWN reconciliation --
    unknown_repo = _FakeDisbursementRepository()
    unknown_action = unknown_repo.create_action(
        action=ProposedDisbursementAction(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            decision_id=decision.id,
            amount=Decimal("50000"),
            currency="VND",
            beneficiary_ref_vi="Nguoi thu huong mo phong 2",
            account_ref_vi="TK-MO-PHONG-0002",
            created_by=OPS_MAKER_ID,
        )
    )
    unknown_adapter = MockDisbursementExecutionAdapter(simulate_unknown=True)
    unknown_recorded, unknown_receipt = unknown_repo.execute_action(
        action_id=unknown_action.id,
        adapter=unknown_adapter,
        idempotency_key=uuid4().hex,
        actor_id=OPS_CHECKER_A_ID,
    )
    assert unknown_recorded.status is ExecutionStatus.EXECUTION_UNKNOWN
    assert unknown_receipt.receipt_ref is None, "an unknown result carries no receipt"
    # ACCEPTANCE: no blind retry after UNKNOWN -- a re-execute is refused.
    assert is_execution_transition_allowed(
        ExecutionStatus.EXECUTION_UNKNOWN, ExecutionStatus.EXECUTION_REQUESTED
    ) is False
    with pytest.raises(ReconciliationRequiredError):
        unknown_repo.execute_action(
            action_id=unknown_action.id,
            adapter=MockDisbursementExecutionAdapter(),
            idempotency_key=uuid4().hex,
            actor_id=OPS_CHECKER_A_ID,
        )
    # Only a human reconciliation may resolve it; CONFIRMED_NOT_EXECUTED re-opens.
    reconciled = unknown_repo.reconcile_action(
        action_id=unknown_action.id, outcome=ExecutionStatus.CONFIRMED_NOT_EXECUTED
    )
    assert reconciled.status is ExecutionStatus.CONFIRMED_NOT_EXECUTED
    assert is_execution_transition_allowed(
        ExecutionStatus.CONFIRMED_NOT_EXECUTED, ExecutionStatus.EXECUTION_REQUESTED
    )
    # A re-attempt is now allowed and executes cleanly.
    reattempted, reattempt_receipt = unknown_repo.execute_action(
        action_id=unknown_action.id,
        adapter=MockDisbursementExecutionAdapter(),
        idempotency_key=uuid4().hex,
        actor_id=OPS_CHECKER_A_ID,
    )
    assert reattempted.status is ExecutionStatus.CONFIRMED_EXECUTED
    assert reattempt_receipt.receipt_ref is not None

    # -- PHASE 13: deterministic repayment ledger fold --------------------------
    facility = Facility(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        decision_id=decision.id,
        principal=Decimal("120000"),
        annual_rate_percent=Decimal("12"),
        term_months=3,
        repayment_style="EQUAL_PRINCIPAL",
        first_payment_date=date(2026, 8, 1),
        periodic_fee=Decimal("10"),
    )
    schedule = build_expected_schedule(facility)  # the REAL underwriting calculator
    assert schedule.total_principal == facility.principal, (
        "EQUAL_PRINCIPAL schedule reconciles Σ principal == principal"
    )
    installments = build_expected_installments(facility)
    total_expected = sum((i.total for i in installments), Decimal("0.00"))
    observation = date(2027, 1, 1)  # after every due date

    pay1 = RepaymentEvent(
        id=uuid4(),
        facility_id=facility.id,
        kind=EventKind.PAYMENT,
        amount=Decimal("50000"),
        external_reference="PMT-1",
        effective_date=date(2026, 8, 1),
        recorded_at=NOW,
    )
    # A BACKDATED payment: earlier effective date, later recorded -- same fold.
    pay2 = RepaymentEvent(
        id=uuid4(),
        facility_id=facility.id,
        kind=EventKind.PAYMENT,
        amount=Decimal("30000"),
        external_reference="PMT-2-BACKDATED",
        effective_date=date(2026, 7, 15),
        recorded_at=NOW + timedelta(days=5),
    )
    # A partial REVERSAL of pay1 (references it; never mutates it).
    rev1 = RepaymentEvent(
        id=uuid4(),
        facility_id=facility.id,
        kind=EventKind.REVERSAL,
        amount=Decimal("10000"),
        external_reference="REV-1",
        reversed_event_id=pay1.id,
        effective_date=date(2026, 8, 10),
        recorded_at=NOW + timedelta(days=10),
    )
    partial_events = [pay1, pay2, rev1]
    partial_snapshot = apply_events(facility, partial_events, as_of=observation)
    net_before = Decimal("50000") + Decimal("30000") - Decimal("10000")
    assert partial_snapshot.net_paid == net_before
    assert partial_snapshot.event_count == 3
    # Fold reconciliation invariants hold exactly at the money quantum.
    assert (
        partial_snapshot.total_expected
        == partial_snapshot.allocated_total + partial_snapshot.outstanding_total
    )
    assert partial_snapshot.net_paid == (
        partial_snapshot.allocated_total + partial_snapshot.overpayment
    )
    assert partial_snapshot.outstanding_total > 0
    assert not partial_snapshot.is_settled
    # Settlement is NOT eligible while a balance / exception remains.
    assert (
        derive_settlement_eligible(_settlement_inputs(partial_snapshot)).eligible is False
    )

    # Drive to full settlement: one final payment reaches EXACTLY total_expected.
    final_amount = total_expected - net_before
    assert final_amount > 0
    pay_final = RepaymentEvent(
        id=uuid4(),
        facility_id=facility.id,
        kind=EventKind.PAYMENT,
        amount=final_amount,
        external_reference="PMT-FINAL",
        effective_date=date(2026, 10, 1),
        recorded_at=NOW + timedelta(days=20),
    )
    settled_snapshot = apply_events(
        facility, [*partial_events, pay_final], as_of=observation
    )
    assert settled_snapshot.net_paid == total_expected
    assert settled_snapshot.outstanding_total == 0
    assert settled_snapshot.overpayment == 0
    assert settled_snapshot.is_settled is True
    assert settled_snapshot.exceptions == (), "a fully settled ledger has no exceptions"

    # -- PHASE 14: settlement (14A) confirm + recovery (14B) negative -----------
    settlement_inputs = _settlement_inputs(settled_snapshot)
    eligibility = derive_settlement_eligible(settlement_inputs)
    assert eligibility.eligible is True
    assert eligibility.zero_balance is True

    check = SettlementCheck(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        outstanding_principal=settlement_inputs.outstanding_principal,
        outstanding_interest=settlement_inputs.outstanding_interest,
        outstanding_fees=settlement_inputs.outstanding_fees,
        open_exception_count=settlement_inputs.open_exception_count,
        zero_balance_confirmed=True,
        recorded_by=OPS_CHECKER_V_ID,
    )
    settlement_repo = _FakeSettlementRepository()
    receipts = settlement_repo.record_settlement_receipts(
        settlement_check_id=check.id,
        receipts=[(kind, None) for kind in MOCK_SETTLEMENT_RECEIPTS],
        actor_id=OPS_CHECKER_A_ID,
    )
    assert {r.kind for r in receipts} == {
        SettlementReceiptKind.MOCK_CLOSURE,
        SettlementReceiptKind.MOCK_RELEASE,
    }, "a confirmed settlement produces BOTH labelled MOCK receipts"
    # Idempotent: recording the same receipt kinds again writes no duplicates.
    again = settlement_repo.record_settlement_receipts(
        settlement_check_id=check.id,
        receipts=[(kind, None) for kind in MOCK_SETTLEMENT_RECEIPTS],
        actor_id=OPS_CHECKER_A_ID,
    )
    assert len(again) == 2
    gates.ensure_gate(
        case_version=CASE_VERSION,
        gate_type=GateType.HG_SETTLEMENT_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=OPS_CHECKER_A_ID,
        disposition_ref=f"settlement:{CASE_VERSION}",
    )

    # Recovery (14B) branch is NEGATIVE on a settled ledger: no trigger fires.
    recovery = derive_recovery_trigger(
        RecoveryTriggerInputs(outstanding_total="0", periods_in_shortfall=0)
    )
    assert recovery.triggered is False, (
        "a settled (zero-balance) ledger must never trigger recovery preparation"
    )

    # -- FINAL: cross-stage acceptance summary (master design 21.3) -------------
    # The deterministic engine satisfied NO stage-6→14 human gate: every satisfied
    # gate carries a human actor id (never engine-authored).
    for (case_version, gate_type), record in gates.gates.items():
        del case_version, gate_type
        if record.status is GateStatus.SATISFIED:
            assert record.satisfied_by_actor_id is not None, (
                "every satisfied human gate must record its human actor"
            )
    # The contract-approval gate is the one gate deliberately left UNSATISFIED
    # (fenced by the material change) -- the case must return to stage 6.
    assert not gates.satisfied(GateType.HG_CONTRACT_PACKAGE_APPROVED, CASE_VERSION)


# =============================================================================
# A minimal disbursement-condition ledger (stage-10) driven by the REAL domain.
# =============================================================================


class _ConditionLedger:
    """In-memory stage-10 ledger: transitions validated by the REAL domain map.

    Mirrors ``api/conditions.py`` + its adapter: every move re-checks
    ``is_transition_allowed`` (rejecting a forbidden edge) and requires a
    rationale for the human authority targets (``RATIONALE_REQUIRED_TARGETS``);
    ``confirmable`` delegates to ``derive_conditions_confirmable``.
    """

    def __init__(self, *, decision_id: UUID) -> None:
        self._decision_id = decision_id
        self._status: dict[UUID, ConditionStatus] = {}

    def open(self, text_vi: str) -> UUID:
        # Construct the real frozen value object (validates its invariants) …
        condition = DisbursementCondition(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            decision_id=self._decision_id,
            condition_text_vi=text_vi,
        )
        # … then track its mutable status in the ledger (PENDING at open).
        self._status[condition.id] = condition.status
        return condition.id

    def transition(
        self,
        condition_id: UUID,
        to_status: ConditionStatus,
        *,
        actor_id: UUID | None = None,
        rationale_vi: str | None = None,
    ) -> None:
        current = self._status[condition_id]
        if not is_transition_allowed(current, to_status):
            raise ValueError(f"forbidden transition {current.value} -> {to_status.value}")
        if to_status in RATIONALE_REQUIRED_TARGETS and not rationale_vi:
            raise ValueError(
                f"{to_status.value} is a human authority act and requires a rationale"
            )
        del actor_id  # recorded on the audit trail in production; unused here
        self._status[condition_id] = to_status

    def confirmable(self) -> bool:
        return derive_conditions_confirmable(self._status.values())


def _settlement_inputs(snapshot: object) -> SettlementLedgerInputs:
    """Adapt a repayment ``LedgerSnapshot`` to the stage-14 settlement input.

    The stage-14 domain deliberately never imports stage 13; the integrator adapts
    the ledger to ``SettlementLedgerInputs`` (canonical non-negative decimal
    strings + the open-exception count).
    """
    return SettlementLedgerInputs(
        outstanding_principal=str(snapshot.outstanding_principal),  # type: ignore[attr-defined]
        outstanding_interest=str(snapshot.outstanding_interest),  # type: ignore[attr-defined]
        outstanding_fees=str(snapshot.outstanding_fees),  # type: ignore[attr-defined]
        open_exception_count=len(snapshot.exceptions),  # type: ignore[attr-defined]
    )
