"""Role-gated API tests for the stage-13 RepaymentLedger surfaces.

Human-only authority: opening a facility requires ``OPS_OFFICER`` + a permitting
credit decision; appending payments / reversals and recording collection notes
require the collections/operations officer role (mapped to ``OPS_OFFICER``,
PROPOSED synthetic); reading the recomputed ledger requires any case participant.
The routers are mounted onto the app built by ``create_app`` here (``main.py``
wiring is a deferred lead decision), and the repositories are injected directly.

All customer data in this project is synthetic and created solely for
demonstration; the fixture case belongs to the invented SME "Cong ty TNHH Nong
San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.repayments import router as repayments_router
from creditops.application.orchestration.roles import (
    OPS_OFFICER_ROLE,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.credit_decisions import RecordedDecision
from creditops.application.ports.repayments import (
    RecordedCollectionNote,
    RecordedFacility,
    RecordedRepaymentEvent,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.repayments import Facility, RepaymentEvent
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
READER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OUTSIDER_ROLE = "OPS_CHECKER"  # a valid role, but not a case participant
CASE_ID = UUID("10000000-0000-0000-0000-0000000000f3")
DECISION_ID = UUID("d0000000-0000-0000-0000-0000000000f3")
ASSIGNED = frozenset({OFFICER_A, READER})
NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


class FakeRepaymentLedgerRepository:
    def __init__(self) -> None:
        self.facilities: dict[UUID, RecordedFacility] = {}
        self.events_by_ref: dict[tuple[UUID, str], RecordedRepaymentEvent] = {}
        self.events: list[RecordedRepaymentEvent] = []
        self.notes: list[RecordedCollectionNote] = []
        self._seq = 0

    async def create_facility(
        self, *, facility: Facility, actor_id: UUID, actor_role: str
    ) -> RecordedFacility:
        record = RecordedFacility(
            id=facility.id,
            case_id=facility.case_id,
            case_version=facility.case_version,
            decision_id=facility.decision_id,
            principal=facility.principal,
            annual_rate_percent=facility.annual_rate_percent,
            term_months=facility.term_months,
            periodic_fee=facility.periodic_fee,
            repayment_style=facility.repayment_style,
            first_payment_date=facility.first_payment_date,
            created_at=NOW,
        )
        self.facilities[facility.id] = record
        return record

    async def load_facility(
        self, facility_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedFacility | None:
        record = self.facilities.get(facility_id)
        if (
            record is None
            or record.case_id != case_id
            or record.case_version != case_version
        ):
            return None
        return record

    async def list_facilities(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedFacility, ...]:
        return tuple(
            f
            for f in self.facilities.values()
            if f.case_id == case_id and f.case_version == case_version
        )

    async def record_event(
        self, *, event: RepaymentEvent, actor_id: UUID, actor_role: str
    ) -> tuple[RecordedRepaymentEvent, bool]:
        key = (event.facility_id, event.external_reference)
        if key in self.events_by_ref:
            return self.events_by_ref[key], False
        self._seq += 1
        row = RecordedRepaymentEvent(
            id=event.id,
            facility_id=event.facility_id,
            kind=event.kind.value,
            amount=event.amount,
            external_reference=event.external_reference,
            reversed_event_id=event.reversed_event_id,
            effective_date=event.effective_date,
            recorded_at=NOW + timedelta(minutes=self._seq),
        )
        self.events_by_ref[key] = row
        self.events.append(row)
        return row, True

    async def list_events(
        self, facility_id: UUID
    ) -> tuple[RecordedRepaymentEvent, ...]:
        rows = [e for e in self.events if e.facility_id == facility_id]
        rows.sort(key=lambda e: (e.effective_date, e.recorded_at, e.id))
        return tuple(rows)

    async def record_collection_note(
        self,
        *,
        facility_id: UUID,
        case_id: UUID,
        case_version: int,
        note_kind: str,
        note_text_vi: str,
        proposed_action_vi: str | None,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedCollectionNote:
        note = RecordedCollectionNote(
            id=uuid4(),
            facility_id=facility_id,
            case_id=case_id,
            case_version=case_version,
            note_kind=note_kind,
            note_text_vi=note_text_vi,
            proposed_action_vi=proposed_action_vi,
            author_id=actor_id,
            author_role=actor_role,
            created_at=NOW,
        )
        self.notes.append(note)
        return note

    async def list_collection_notes(
        self, facility_id: UUID
    ) -> tuple[RecordedCollectionNote, ...]:
        return tuple(n for n in self.notes if n.facility_id == facility_id)


class FakeCreditDecisionRepository:
    def __init__(self, decision: str | None = "APPROVED_AS_PROPOSED") -> None:
        self.decision = decision

    async def load_decision(
        self, case_id: UUID, case_version: int
    ) -> RecordedDecision | None:
        if self.decision is None or case_id != CASE_ID:
            return None
        return RecordedDecision(
            id=DECISION_ID,
            case_id=case_id,
            case_version=case_version,
            decision=self.decision,
            rationale_vi="Phê duyệt (mô phỏng).",
            decided_by=OFFICER_A,
            decided_by_role="CREDIT_APPROVER",
            memo_artifact_id=None,
            risk_assessment_id=None,
            underwriting_assessment_id=None,
            conditions=(),
            created_at=NOW,
            snapshot=None,
            created=False,
        )

    async def load_decision_binding(self, case_id: UUID) -> None:
        return None

    async def record_decision(self, **kwargs: Any) -> None:
        raise AssertionError("not used by the repayments API")


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id not in ASSIGNED:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=actor_id,
            requested_amount="120000",
            purpose_vi="Vốn lưu động cho nông sản",
            created_at=NOW,
        )


class FakeUnitOfWork:
    cases = FakeCases()

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_client(
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeRepaymentLedgerRepository,
    decision_repository: FakeCreditDecisionRepository | None = None,
) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=JwksKeyResolver({"keys": [jwk]})
    )
    application = create_app(
        settings=Settings(app_env="test"),
        jwt_verifier=verifier,
        uow_factory=lambda actor: FakeUnitOfWork(),
    )
    application.include_router(repayments_router)
    application.state.repayment_ledger_repository = repository
    application.state.credit_decision_repository = (
        decision_repository or FakeCreditDecisionRepository()
    )
    return TestClient(application)


def token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER_A,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(subject),
            "roles": roles or [OPS_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _url() -> str:
    return f"/api/v1/cases/{CASE_ID}/repayments"


def _officer(signing_key: rsa.RSAPrivateKey) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, roles=[OPS_OFFICER_ROLE])}"}


def _reader(signing_key: rsa.RSAPrivateKey) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {token(signing_key, subject=READER, roles=[RISK_REVIEWER_ROLE])}"
        )
    }


def _facility_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "principal": "120000.00",
        "annualRatePercent": "12",
        "termMonths": 3,
        "repaymentStyle": "EQUAL_PRINCIPAL",
        "firstPaymentDate": "2026-08-01",
        "periodicFee": "100.00",
    }
    body.update(overrides)
    return body


def _create_facility(client: TestClient, signing_key: rsa.RSAPrivateKey) -> str:
    response = client.post(_url(), json=_facility_body(), headers=_officer(signing_key))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# -- facility creation --------------------------------------------------------


def test_create_facility_requires_permitting_decision(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        decision_repository=FakeCreditDecisionRepository(decision=None),
    )

    response = client.post(_url(), json=_facility_body(), headers=_officer(signing_key))

    assert response.status_code == 409
    assert response.json()["code"] == "FACILITY_REQUIRES_APPROVAL_DECISION"
    assert repository.facilities == {}


def test_create_facility_rejects_non_permitting_decision(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        decision_repository=FakeCreditDecisionRepository(decision="DECLINED_BY_HUMAN"),
    )

    response = client.post(_url(), json=_facility_body(), headers=_officer(signing_key))

    assert response.status_code == 409
    assert response.json()["code"] == "FACILITY_REQUIRES_APPROVAL_DECISION"


def test_create_facility_succeeds_with_approval(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(_url(), json=_facility_body(), headers=_officer(signing_key))

    assert response.status_code == 201
    body = response.json()
    assert body["decisionId"] == str(DECISION_ID)
    assert body["principal"] == "120000.00"
    assert body["repaymentStyle"] == "EQUAL_PRINCIPAL"


def test_create_facility_rejects_non_officer(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(_url(), json=_facility_body(), headers=_reader(signing_key))

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_create_facility_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json=_facility_body(),
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, subject=uuid4(), roles=[OPS_OFFICER_ROLE])}"
            )
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_create_facility_rejects_invalid_style(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json=_facility_body(repaymentStyle="INTEREST_FREE_FOREVER"),
        headers=_officer(signing_key),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_FACILITY"


# -- events -------------------------------------------------------------------


def _post_event(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    facility_id: str,
    **body: Any,
) -> Any:
    return client.post(
        f"{_url()}/{facility_id}/events", json=body, headers=_officer(signing_key)
    )


def test_record_payment_then_duplicate_is_idempotent(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    first = _post_event(
        client,
        signing_key,
        facility_id,
        kind="PAYMENT",
        amount="41300.00",
        externalReference="BANKREF-0001",
        effectiveDate="2026-08-01",
    )
    assert first.status_code == 201
    assert first.json()["created"] is True

    # Same external reference: idempotent -> the EXISTING row, 200, no new effect.
    duplicate = _post_event(
        client,
        signing_key,
        facility_id,
        kind="PAYMENT",
        amount="41300.00",
        externalReference="BANKREF-0001",
        effectiveDate="2026-08-01",
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert len(repository.events) == 1


def test_reversal_requires_known_payment_reference(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    orphan = _post_event(
        client,
        signing_key,
        facility_id,
        kind="REVERSAL",
        amount="10.00",
        externalReference="BANKREF-REV",
        effectiveDate="2026-08-05",
        reversedEventId=str(uuid4()),
    )
    assert orphan.status_code == 422
    assert orphan.json()["code"] == "INVALID_REVERSAL_REFERENCE"


def test_reversal_of_known_payment_succeeds(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    payment = _post_event(
        client,
        signing_key,
        facility_id,
        kind="PAYMENT",
        amount="41300.00",
        externalReference="BANKREF-0001",
        effectiveDate="2026-08-01",
    )
    payment_id = payment.json()["id"]

    reversal = _post_event(
        client,
        signing_key,
        facility_id,
        kind="REVERSAL",
        amount="41300.00",
        externalReference="BANKREF-REV",
        effectiveDate="2026-08-05",
        reversedEventId=payment_id,
    )
    assert reversal.status_code == 201
    assert reversal.json()["kind"] == "REVERSAL"


def test_record_event_rejects_non_officer(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    response = client.post(
        f"{_url()}/{facility_id}/events",
        json={
            "kind": "PAYMENT",
            "amount": "1.00",
            "externalReference": "X",
            "effectiveDate": "2026-08-01",
        },
        headers=_reader(signing_key),
    )
    assert response.status_code == 403


def test_record_event_on_missing_facility_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = _post_event(
        client,
        signing_key,
        str(uuid4()),
        kind="PAYMENT",
        amount="1.00",
        externalReference="X",
        effectiveDate="2026-08-01",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "FACILITY_NOT_FOUND"


# -- ledger read --------------------------------------------------------------


def test_ledger_recomputes_snapshot_and_exceptions(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)
    _post_event(
        client,
        signing_key,
        facility_id,
        kind="PAYMENT",
        amount="41300.00",
        externalReference="BANKREF-0001",
        effectiveDate="2026-08-01",
    )

    # As of 2026-09-15: period 1 is fully paid; period 2 (due 2026-09-01) is
    # overdue with nothing allocated -> a deterministic OVERDUE_INSTALLMENT.
    response = client.get(
        f"{_url()}/{facility_id}/ledger?asOf=2026-09-15", headers=_reader(signing_key)
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["netPaid"] == "41300.00"
    assert body["outstandingPrincipal"] == "80000.00"
    assert body["isSettled"] is False
    assert body["periods"][0]["status"] == "PAID"
    assert body["periods"][1]["overdue"] is True

    overdue = [e for e in body["exceptions"] if e["kind"] == "OVERDUE_INSTALLMENT"]
    assert overdue == [
        {
            "kind": "OVERDUE_INSTALLMENT",
            "period": 2,
            "amount": "40900.00",
            "detailVi": overdue[0]["detailVi"],
        }
    ]


def test_ledger_on_missing_facility_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"{_url()}/{uuid4()}/ledger?asOf=2026-09-15", headers=_reader(signing_key)
    )
    assert response.status_code == 404
    assert response.json()["code"] == "FACILITY_NOT_FOUND"


def test_ledger_rejects_non_participant(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    response = client.get(
        f"{_url()}/{facility_id}/ledger?asOf=2026-09-15",
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, subject=READER, roles=[OUTSIDER_ROLE])}"
            )
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


# -- collection notes ---------------------------------------------------------


def test_proposed_action_note_requires_action_label(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    response = client.post(
        f"{_url()}/{facility_id}/notes",
        json={"noteKind": "PROPOSED_ACTION", "noteText": "Đề xuất siết dòng tiền."},
        headers=_officer(signing_key),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_NOTE"


def test_proposed_action_note_is_recorded_and_read_back(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    created = client.post(
        f"{_url()}/{facility_id}/notes",
        json={
            "noteKind": "PROPOSED_ACTION",
            "noteText": "Khách hàng trễ hạn; đề xuất liên hệ và siết dòng tiền.",
            "proposedAction": "TIGHTEN_CASHFLOW_CONTROL",
        },
        headers=_officer(signing_key),
    )
    assert created.status_code == 201
    assert created.json()["proposedAction"] == "TIGHTEN_CASHFLOW_CONTROL"

    # The proposal is a read surface on the ledger; nothing is executed.
    ledger = client.get(
        f"{_url()}/{facility_id}/ledger?asOf=2026-08-01", headers=_reader(signing_key)
    )
    assert ledger.status_code == 200
    notes = ledger.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["noteKind"] == "PROPOSED_ACTION"


def test_observation_note_must_not_carry_action(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRepaymentLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    facility_id = _create_facility(client, signing_key)

    response = client.post(
        f"{_url()}/{facility_id}/notes",
        json={
            "noteKind": "OBSERVATION",
            "noteText": "Khách hàng đã thanh toán kỳ 1.",
            "proposedAction": "SOMETHING",
        },
        headers=_officer(signing_key),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_NOTE"
