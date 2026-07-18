"""Role-gated API tests for the officer intake evidence review endpoints.

All cases, documents, evidence, and conflicts here are synthetic.  The tests
prove the four endpoints serve exactly the wire shapes the frontend parser
(``apps/web/lib/api/schemas.ts``) requires, that confirmation honours the domain
invariants (every candidate dispositioned once; ``CORRECTED`` needs a value +
rationale), is assignment-scoped and idempotent, and fails closed on a stale
document version.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.evidence_review import router as evidence_router
from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
    OPS_OFFICER_ROLE,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.evidence_review import (
    CandidateReview,
    ConfirmationInput,
    ConflictSourceView,
    ConflictView,
    DocumentReviewView,
    EvidenceFactView,
    RecordedConfirmations,
    StaleDocumentVersionError,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.enums import DocumentStage, FactDisposition
from creditops.domain.evidence import PageRegion
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000004")
CASE_VERSION = 2
DOCUMENT_ID = UUID("20000000-0000-0000-0000-000000000004")
DOC_VERSION_ID = UUID("21000000-0000-0000-0000-000000000004")
DOC_VERSION = 3
CANDIDATE_ID = UUID("22000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _region() -> PageRegion:
    return PageRegion(page=1, x=0.1, y=0.2, width=0.3, height=0.04)


def _review() -> DocumentReviewView:
    return DocumentReviewView(
        document_id=DOCUMENT_ID,
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        document_version_id=DOC_VERSION_ID,
        document_version=DOC_VERSION,
        stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
        file_name="giay_de_nghi.pdf",
        page_count=3,
        candidates=(
            CandidateReview(
                candidate_id=CANDIDATE_ID,
                field_key="requested_amount",
                proposed_value="5000000000",
                confidence=0.9,
                source=_region(),
            ),
        ),
    )


def _evidence_fact() -> EvidenceFactView:
    return EvidenceFactView(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        candidate_id=CANDIDATE_ID,
        confirmation_id=uuid4(),
        document_version_id=DOC_VERSION_ID,
        field_key="requested_amount",
        value="5000000000",
        candidate_value="5000000000",
        source=_region(),
        confirmed_at=NOW,
        stale=False,
    )


def _conflict() -> ConflictView:
    return ConflictView(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        field_key="requested_amount",
        sources=(
            ConflictSourceView(
                document_version_id=DOC_VERSION_ID, value="5000000000", source=_region()
            ),
            ConflictSourceView(
                document_version_id=uuid4(), value="4000000000", source=None
            ),
        ),
        detected_at=NOW,
        stale=False,
    )


class FakeEvidenceReviewRepository:
    def __init__(
        self,
        *,
        review: DocumentReviewView | None = None,
        evidence: tuple[EvidenceFactView, ...] = (),
        conflicts: tuple[ConflictView, ...] = (),
        stale: bool = False,
    ) -> None:
        self._review = review
        self._evidence = evidence
        self._conflicts = conflicts
        self._stale = stale
        self.recorded: list[tuple[ConfirmationInput, ...]] = []
        self.evidence_calls: list[tuple[UUID, int]] = []
        self.conflict_calls: list[tuple[UUID, int]] = []
        self._confirmed: set[UUID] = set()

    async def load_document_review(
        self, document_id: UUID
    ) -> DocumentReviewView | None:
        if self._review is None or document_id != self._review.document_id:
            return None
        return self._review

    async def record_confirmations(
        self,
        *,
        document_version_id: UUID,
        confirmations: tuple[ConfirmationInput, ...],
        actor_id: UUID,
        expected_document_stage: DocumentStage,
    ) -> RecordedConfirmations:
        if self._stale:
            raise StaleDocumentVersionError(document_version_id)
        self.recorded.append(confirmations)
        new = [c for c in confirmations if c.candidate_id not in self._confirmed]
        for confirmation in new:
            self._confirmed.add(confirmation.candidate_id)
        confirmed_fact_ids = tuple(
            uuid4()
            for c in new
            if c.disposition in (FactDisposition.ACCEPTED, FactDisposition.CORRECTED)
        )
        return RecordedConfirmations(
            confirmation_ids=tuple(uuid4() for _ in confirmations),
            confirmed_fact_ids=confirmed_fact_ids,
            created=bool(new),
        )

    async def load_case_evidence(
        self, case_id: UUID, case_version: int
    ) -> tuple[EvidenceFactView, ...]:
        self.evidence_calls.append((case_id, case_version))
        return self._evidence

    async def load_case_conflicts(
        self, case_id: UUID, case_version: int
    ) -> tuple[ConflictView, ...]:
        self.conflict_calls.append((case_id, case_version))
        return self._conflicts


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=CASE_VERSION,
            assigned_officer_id=OFFICER_A,
            requested_amount="5000000000",
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
    signing_key: rsa.RSAPrivateKey, *, repository: FakeEvidenceReviewRepository
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
    # The lead wires this router into main.py; the test includes it directly so
    # it exercises the real router without touching the composition root.
    application.include_router(evidence_router)
    application.state.evidence_review_repository = repository
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
            "roles": roles or [INTAKE_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _auth(signing_key: rsa.RSAPrivateKey, **kwargs: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, **kwargs)}"}  # type: ignore[arg-type]


# --- GET /documents/{id}/review --------------------------------------------


def test_review_payload_matches_frontend_parser_shape(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/documents/{DOCUMENT_ID}/review", headers=_auth(signing_key)
    )

    assert response.status_code == 200
    body = response.json()
    # parseDocumentReview required keys (schemas.ts).
    assert body["documentId"] == str(DOCUMENT_ID)
    assert body["caseId"] == str(CASE_ID)
    assert body["documentVersionId"] == str(DOC_VERSION_ID)
    assert body["documentVersion"] == DOC_VERSION
    assert body["stage"] == "READY_FOR_OFFICER_REVIEW"
    assert body["fileName"] == "giay_de_nghi.pdf"
    assert body["pageCount"] == 3
    assert isinstance(body["candidates"], list) and len(body["candidates"]) == 1
    # parseCandidateFact required keys, camelCase.
    candidate = body["candidates"][0]
    assert candidate["id"] == str(CANDIDATE_ID)
    assert candidate["caseId"] == str(CASE_ID)
    assert candidate["caseVersion"] == CASE_VERSION
    assert candidate["documentVersionId"] == str(DOC_VERSION_ID)
    assert candidate["fieldKey"] == "requested_amount"
    assert candidate["proposedValue"] == "5000000000"
    assert candidate["confidence"] == 0.9
    assert candidate["source"] == {
        "page": 1,
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.04,
    }


def test_review_reader_may_be_any_case_participant(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/documents/{DOCUMENT_ID}/review",
        headers=_auth(signing_key, roles=[OPS_OFFICER_ROLE]),
    )

    assert response.status_code == 200


def test_review_missing_document_is_indistinguishable_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/documents/{uuid4()}/review", headers=_auth(signing_key)
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_review_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/documents/{DOCUMENT_ID}/review",
        headers=_auth(signing_key, subject=uuid4()),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


# --- POST /documents/{id}/confirmations ------------------------------------


def _confirm_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "expectedDocumentVersion": DOC_VERSION,
        "dispositions": [{"candidateId": str(CANDIDATE_ID), "disposition": "ACCEPTED"}],
    }
    body.update(overrides)
    return body


def test_confirmation_happy_path_records_batch_and_returns_201(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key),
        json=_confirm_body(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["confirmedCount"] == 1
    assert body["documentVersionId"] == str(DOC_VERSION_ID)
    # Exactly one confirmation batch recorded, one disposition, the assigned
    # officer's actor id used for the write (and its audit, per the adapter).
    assert len(repository.recorded) == 1
    (recorded_batch,) = repository.recorded
    assert len(recorded_batch) == 1
    assert recorded_batch[0].candidate_id == CANDIDATE_ID
    assert recorded_batch[0].disposition is FactDisposition.ACCEPTED


def test_corrected_disposition_carries_value_and_rationale(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key),
        json=_confirm_body(
            dispositions=[
                {
                    "candidateId": str(CANDIDATE_ID),
                    "disposition": "CORRECTED",
                    "correctedValue": "4500000000",
                    "rationale": "OCR đọc nhầm chữ số cuối.",
                }
            ]
        ),
    )

    assert response.status_code == 201
    (recorded_batch,) = repository.recorded
    assert recorded_batch[0].disposition is FactDisposition.CORRECTED
    assert recorded_batch[0].corrected_value == "4500000000"
    assert recorded_batch[0].rationale == "OCR đọc nhầm chữ số cuối."


def test_corrected_without_rationale_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key),
        json=_confirm_body(
            dispositions=[
                {
                    "candidateId": str(CANDIDATE_ID),
                    "disposition": "CORRECTED",
                    "correctedValue": "4500000000",
                }
            ]
        ),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "CORRECTION_REQUIRES_VALUE_AND_RATIONALE"
    assert repository.recorded == []


def test_unknown_candidate_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key),
        json=_confirm_body(
            dispositions=[{"candidateId": str(uuid4()), "disposition": "ACCEPTED"}]
        ),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_CANDIDATE"
    assert repository.recorded == []


def test_partial_confirmation_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    review = DocumentReviewView(
        document_id=DOCUMENT_ID,
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        document_version_id=DOC_VERSION_ID,
        document_version=DOC_VERSION,
        stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
        file_name=None,
        page_count=None,
        candidates=(
            CandidateReview(
                candidate_id=CANDIDATE_ID,
                field_key="requested_amount",
                proposed_value="5000000000",
                confidence=0.9,
                source=_region(),
            ),
            CandidateReview(
                candidate_id=uuid4(),
                field_key="borrower_name",
                proposed_value="Nguyễn Văn A",
                confidence=0.8,
                source=_region(),
            ),
        ),
    )
    repository = FakeEvidenceReviewRepository(review=review)
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key),
        json=_confirm_body(),  # only one of two candidates dispositioned
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INCOMPLETE_CONFIRMATION"
    assert repository.recorded == []


def test_double_submit_is_idempotent_created_false(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)
    headers = _auth(signing_key)
    url = f"/api/v1/documents/{DOCUMENT_ID}/confirmations"

    first = client.post(url, headers=headers, json=_confirm_body())
    second = client.post(url, headers=headers, json=_confirm_body())

    assert first.status_code == 201
    assert first.json()["created"] is True
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert len(repository.recorded) == 2


def test_non_intake_role_is_forbidden(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key, roles=[RISK_REVIEWER_ROLE]),
        json=_confirm_body(),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.recorded == []


def test_confirmation_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key, subject=uuid4()),
        json=_confirm_body(),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert repository.recorded == []


def test_stale_expected_version_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key),
        json=_confirm_body(expectedDocumentVersion=DOC_VERSION - 1),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_DOCUMENT_VERSION"
    assert body["details"]["currentDocumentVersion"] == DOC_VERSION
    assert repository.recorded == []


def test_adapter_stale_signal_maps_to_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review(), stale=True)
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/documents/{DOCUMENT_ID}/confirmations",
        headers=_auth(signing_key),
        json=_confirm_body(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "STALE_DOCUMENT_VERSION"


# --- GET /cases/{id}/evidence + /cases/{id}/conflicts ----------------------


def test_evidence_list_matches_shape_and_is_version_scoped(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    fact = _evidence_fact()
    repository = FakeEvidenceReviewRepository(review=_review(), evidence=(fact,))
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/evidence", headers=_auth(signing_key)
    )

    assert response.status_code == 200
    body = response.json()
    assert repository.evidence_calls == [(CASE_ID, CASE_VERSION)]
    (item,) = body["items"]
    # parseConfirmedFact required keys, camelCase.
    for key in (
        "id",
        "caseId",
        "caseVersion",
        "candidateId",
        "confirmationId",
        "documentVersionId",
        "fieldKey",
        "value",
        "candidateValue",
        "source",
        "confirmedAt",
        "stale",
    ):
        assert key in item
    assert item["caseVersion"] == CASE_VERSION
    assert item["value"] == "5000000000"
    assert item["stale"] is False
    assert item["source"]["page"] == 1


def test_conflict_list_matches_normalized_shape_and_is_version_scoped(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeEvidenceReviewRepository(review=_review(), conflicts=(_conflict(),))
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/conflicts", headers=_auth(signing_key)
    )

    assert response.status_code == 200
    body = response.json()
    assert repository.conflict_calls == [(CASE_ID, CASE_VERSION)]
    (item,) = body["items"]
    # parseConflict required keys; a conflict preserves >= 2 sources.
    assert item["caseId"] == str(CASE_ID)
    assert item["caseVersion"] == CASE_VERSION
    assert item["fieldKey"] == "requested_amount"
    assert isinstance(item["sources"], list) and len(item["sources"]) == 2
    assert item["detectedAt"] is not None
    assert item["stale"] is False
    # parseConflictSource: documentVersionId, value, source (pageRegion | null).
    assert item["sources"][0]["documentVersionId"] == str(DOC_VERSION_ID)
    assert item["sources"][0]["value"] == "5000000000"
    assert item["sources"][0]["source"]["page"] == 1
    assert item["sources"][1]["source"] is None


def test_case_reads_reject_non_participant_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/evidence",
        headers=_auth(signing_key, roles=["AUDITOR"]),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_case_reads_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeEvidenceReviewRepository(review=_review())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/conflicts",
        headers=_auth(signing_key, subject=uuid4()),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
