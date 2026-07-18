"""Role-gated API tests for the Stage 1 prospect endpoints.

All customer data here is synthetic and created solely for demonstration.  The
tests prove the endpoints are INTAKE_OFFICER-gated, scoped to the actor's own
prospects (another officer's prospect is an indistinguishable 404), version
screening snapshots, require a rationale on the human contact decision, and
never surface a verdict field.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.prospects import router as prospects_router
from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.prospects import ProspectDetail, ProspectNotFound
from creditops.config import Settings
from creditops.domain.prospects import (
    ContactDecision,
    ContactDecisionRecord,
    Prospect,
    ScreeningSnapshot,
)
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OFFICER_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)

_VERDICT_KEYS = ("score", "verdict", "approved", "rating")


def _assert_no_verdict(payload: object) -> None:
    """Recursively assert no verdict-shaped key appears anywhere in a response."""
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            assert key.lower() not in _VERDICT_KEYS, f"verdict key leaked: {key}"
            _assert_no_verdict(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_verdict(item)


class FakeProspectRepository:
    """In-memory prospect store scoped by created_by, mirroring the port."""

    def __init__(self) -> None:
        self.prospects: dict[UUID, Prospect] = {}
        self.snapshots: dict[UUID, list[ScreeningSnapshot]] = {}
        self.decisions: dict[UUID, list[ContactDecisionRecord]] = {}

    async def create_prospect(
        self,
        *,
        name_vi: str,
        industry_vi: str | None,
        years_operating: int | None,
        revenue_band_vi: str | None,
        legal_status_vi: str | None,
        notes_vi: str | None,
        created_by: UUID,
    ) -> Prospect:
        prospect = Prospect(
            id=uuid4(),
            name_vi=name_vi,
            industry_vi=industry_vi,
            years_operating=years_operating,
            revenue_band_vi=revenue_band_vi,
            legal_status_vi=legal_status_vi,
            notes_vi=notes_vi,
            created_by=created_by,
            created_at=NOW,
        )
        self.prospects[prospect.id] = prospect
        return prospect

    async def list_prospects(
        self, *, created_by: UUID, cursor: UUID | None, limit: int
    ) -> tuple[tuple[Prospect, ...], UUID | None]:
        del cursor
        owned = tuple(
            p for p in self.prospects.values() if p.created_by == created_by
        )
        return owned[:limit], None

    async def load_prospect(
        self, *, prospect_id: UUID, created_by: UUID
    ) -> ProspectDetail | None:
        prospect = self.prospects.get(prospect_id)
        if prospect is None or prospect.created_by != created_by:
            return None
        snapshots = self.snapshots.get(prospect_id, [])
        latest = snapshots[-1] if snapshots else None
        return ProspectDetail(
            prospect=prospect,
            latest_snapshot=latest,
            decisions=tuple(self.decisions.get(prospect_id, [])),
        )

    async def append_screening_snapshot(
        self,
        *,
        prospect_id: UUID,
        created_by: UUID,
        screening_config_version: str,
        industry_vi: str | None,
        years_operating: int | None,
        revenue_band_vi: str | None,
        legal_status_vi: str | None,
        credit_history_vi: str | None,
        risk_appetite_note_vi: str | None,
        details: Mapping[str, object],
    ) -> ScreeningSnapshot:
        prospect = self.prospects.get(prospect_id)
        if prospect is None or prospect.created_by != created_by:
            raise ProspectNotFound
        existing = self.snapshots.setdefault(prospect_id, [])
        snapshot = ScreeningSnapshot(
            id=uuid4(),
            prospect_id=prospect_id,
            version=len(existing) + 1,
            screening_config_version=screening_config_version,
            industry_vi=industry_vi,
            years_operating=years_operating,
            revenue_band_vi=revenue_band_vi,
            legal_status_vi=legal_status_vi,
            credit_history_vi=credit_history_vi,
            risk_appetite_note_vi=risk_appetite_note_vi,
            details=dict(details),
            created_by=created_by,
            created_at=NOW,
        )
        existing.append(snapshot)
        return snapshot

    async def record_contact_decision(
        self,
        *,
        prospect_id: UUID,
        created_by: UUID,
        decision: ContactDecision,
        rationale_vi: str,
        decided_by: UUID,
    ) -> ContactDecisionRecord:
        prospect = self.prospects.get(prospect_id)
        if prospect is None or prospect.created_by != created_by:
            raise ProspectNotFound
        record = ContactDecisionRecord(
            id=uuid4(),
            prospect_id=prospect_id,
            decision=decision,
            rationale_vi=rationale_vi,
            decided_by=decided_by,
            created_at=NOW,
        )
        self.decisions.setdefault(prospect_id, []).append(record)
        return record


class FakeUnitOfWork:
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
    signing_key: rsa.RSAPrivateKey, *, repository: FakeProspectRepository
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
    application.include_router(prospects_router)
    application.state.prospect_repository = repository
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


def _auth(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER_A,
    roles: list[str] | None = None,
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token(signing_key, subject=subject, roles=roles)}"
    }


def _create_prospect(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER_A,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/prospects",
        headers=_auth(signing_key, subject=subject),
        json={"nameVi": "Cong ty Demo", "industryVi": "Nong nghiep"},
    )
    assert response.status_code == 201, response.text
    result: dict[str, object] = response.json()
    return result


def test_create_prospect_records_creator_provenance(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)

    body = _create_prospect(client, signing_key)

    assert body["nameVi"] == "Cong ty Demo"
    # Provenance is the created_by column (no case-scoped audit event exists).
    assert body["createdBy"] == str(OFFICER_A)
    assert len(repository.prospects) == 1
    _assert_no_verdict(body)


def test_create_requires_intake_officer_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        "/api/v1/prospects",
        headers=_auth(signing_key, roles=[RISK_REVIEWER_ROLE]),
        json={"nameVi": "Cong ty Demo"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.prospects == {}


def test_list_returns_only_own_prospects(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    mine = _create_prospect(client, signing_key)
    _create_prospect(client, signing_key, subject=OFFICER_B)

    response = client.get("/api/v1/prospects", headers=_auth(signing_key))

    assert response.status_code == 200
    body = response.json()
    ids = [p["id"] for p in body["prospects"]]
    assert ids == [mine["id"]]
    assert body["nextCursor"] is None


def test_get_another_officers_prospect_is_indistinguishable_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    other = _create_prospect(client, signing_key, subject=OFFICER_B)

    response = client.get(
        f"/api/v1/prospects/{other['id']}", headers=_auth(signing_key)
    )

    assert response.status_code == 404
    assert response.json()["code"] == "PROSPECT_NOT_ACCESSIBLE"


def test_get_prospect_returns_latest_snapshot_and_decisions(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    prospect = _create_prospect(client, signing_key)
    pid = prospect["id"]
    client.post(
        f"/api/v1/prospects/{pid}/screening-snapshots",
        headers=_auth(signing_key),
        json={"screeningConfigVersion": "cfg-v1"},
    )
    client.post(
        f"/api/v1/prospects/{pid}/screening-snapshots",
        headers=_auth(signing_key),
        json={"screeningConfigVersion": "cfg-v2"},
    )
    client.post(
        f"/api/v1/prospects/{pid}/contact-decisions",
        headers=_auth(signing_key),
        json={"decision": "CONTACT", "rationaleVi": "Ly do lien he"},
    )

    response = client.get(f"/api/v1/prospects/{pid}", headers=_auth(signing_key))

    assert response.status_code == 200
    body = response.json()
    assert body["latestSnapshot"]["version"] == 2
    assert body["latestSnapshot"]["screeningConfigVersion"] == "cfg-v2"
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["decision"] == "CONTACT"
    _assert_no_verdict(body)


def test_screening_snapshots_are_versioned(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    pid = _create_prospect(client, signing_key)["id"]

    first = client.post(
        f"/api/v1/prospects/{pid}/screening-snapshots",
        headers=_auth(signing_key),
        json={"screeningConfigVersion": "cfg-v1", "industryVi": "Nong nghiep"},
    )
    second = client.post(
        f"/api/v1/prospects/{pid}/screening-snapshots",
        headers=_auth(signing_key),
        json={"screeningConfigVersion": "cfg-v1"},
    )

    assert first.status_code == 201
    assert first.json()["version"] == 1
    assert second.status_code == 201
    assert second.json()["version"] == 2
    _assert_no_verdict(first.json())


def test_screening_snapshot_rejects_verdict_shaped_details(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    pid = _create_prospect(client, signing_key)["id"]

    response = client.post(
        f"/api/v1/prospects/{pid}/screening-snapshots",
        headers=_auth(signing_key),
        json={"screeningConfigVersion": "cfg-v1", "details": {"score": 90}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCREENING_NOT_DESCRIPTIVE"
    # Nothing was persisted.
    assert repository.snapshots == {}


def test_snapshot_on_another_officers_prospect_is_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    other = _create_prospect(client, signing_key, subject=OFFICER_B)

    response = client.post(
        f"/api/v1/prospects/{other['id']}/screening-snapshots",
        headers=_auth(signing_key),
        json={"screeningConfigVersion": "cfg-v1"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "PROSPECT_NOT_ACCESSIBLE"


def test_contact_decision_requires_rationale(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    pid = _create_prospect(client, signing_key)["id"]

    response = client.post(
        f"/api/v1/prospects/{pid}/contact-decisions",
        headers=_auth(signing_key),
        json={"decision": "DO_NOT_CONTACT", "rationaleVi": "   "},
    )

    assert response.status_code == 422
    assert repository.decisions == {}


def test_contact_decision_rejects_unknown_decision(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    pid = _create_prospect(client, signing_key)["id"]

    response = client.post(
        f"/api/v1/prospects/{pid}/contact-decisions",
        headers=_auth(signing_key),
        json={"decision": "MAYBE", "rationaleVi": "khong hop le"},
    )

    assert response.status_code == 422
    assert repository.decisions == {}


def test_contact_decision_records_without_side_effects(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeProspectRepository()
    client = _build_client(signing_key, repository=repository)
    pid = _create_prospect(client, signing_key)["id"]

    response = client.post(
        f"/api/v1/prospects/{pid}/contact-decisions",
        headers=_auth(signing_key),
        json={"decision": "DEFER", "rationaleVi": "Can them thong tin"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == "DEFER"
    assert body["decidedBy"] == str(OFFICER_A)
    # The only effect is the recorded decision -- no snapshot, nothing else.
    assert len(repository.decisions[UUID(pid)]) == 1
    assert repository.snapshots == {}
    _assert_no_verdict(body)
