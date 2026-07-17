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
from creditops.application.ports.repositories import AuditEvent, CaseRecord, ForbiddenError
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OFFICER_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class FakeCaseRepository:
    def __init__(self, records: dict[UUID, CaseRecord]) -> None:
        self.records = records

    async def create(
        self,
        *,
        actor_id: UUID,
        assigned_officer_id: UUID,
        requested_amount: str,
        purpose_vi: str,
    ) -> CaseRecord:
        del actor_id
        record = CaseRecord(
            id=uuid4(),
            version=1,
            assigned_officer_id=assigned_officer_id,
            requested_amount=requested_amount,
            purpose_vi=purpose_vi,
            created_at=datetime.now(UTC),
        )
        self.records[record.id] = record
        return record

    async def require_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord:
        record = self.records.get(case_id)
        if record is None or record.assigned_officer_id != actor_id:
            raise ForbiddenError
        return record

    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        try:
            return await self.require_assigned(case_id, actor_id)
        except ForbiddenError:
            return None

    async def list_assigned(
        self, actor_id: UUID, *, cursor: UUID | None, limit: int
    ) -> tuple[list[CaseRecord], UUID | None]:
        records = sorted(
            (record for record in self.records.values() if record.assigned_officer_id == actor_id),
            key=lambda record: (record.created_at, record.id),
            reverse=True,
        )
        if cursor is not None:
            cursor_index = next(
                (index for index, record in enumerate(records) if record.id == cursor),
                len(records),
            )
            records = records[cursor_index + 1 :]
        page = records[:limit]
        next_cursor = page[-1].id if len(records) > limit else None
        return page, next_cursor


class FakeAuditRepository:
    def __init__(self, events: list[AuditEvent]) -> None:
        self.events = events

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class FakeUnitOfWork:
    def __init__(
        self,
        records: dict[UUID, CaseRecord],
        events: list[AuditEvent],
    ) -> None:
        self.cases = FakeCaseRepository(records)
        self.audit = FakeAuditRepository(events)

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class FakeUnitOfWorkFactory:
    def __init__(self) -> None:
        self.records: dict[UUID, CaseRecord] = {}
        self.events: list[AuditEvent] = []

    def __call__(self, actor: Any) -> FakeUnitOfWork:
        del actor
        return FakeUnitOfWork(self.records, self.events)


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwt_verifier(signing_key: rsa.RSAPrivateKey) -> JwtVerifier:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    return JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        key_resolver=JwksKeyResolver({"keys": [jwk]}),
    )


@pytest.fixture
def uow_factory() -> FakeUnitOfWorkFactory:
    return FakeUnitOfWorkFactory()


@pytest.fixture
def client(jwt_verifier: JwtVerifier, uow_factory: FakeUnitOfWorkFactory) -> TestClient:
    settings = Settings(
        app_env="test",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_url="https://identity.test.example/.well-known/jwks.json",
    )
    return TestClient(
        create_app(
            settings=settings,
            jwt_verifier=jwt_verifier,
            uow_factory=uow_factory,
        )
    )


def make_token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER_A,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_delta: timedelta = timedelta(minutes=5),
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": str(subject),
            "roles": roles or ["INTAKE_OFFICER"],
            "iat": now,
            "exp": now + expires_delta,
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-ID": "request-123"}


def test_missing_token_returns_flat_vietnamese_api_error(client: TestClient) -> None:
    response = client.get("/api/v1/cases")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "code": "AUTHENTICATION_REQUIRED",
        "messageVi": "Yêu cầu xác thực hợp lệ.",
        "correlationId": response.json()["correlationId"],
        "retryable": False,
    }


@pytest.mark.parametrize(
    ("issuer", "audience", "expires_delta"),
    [
        (ISSUER, "wrong-audience", timedelta(minutes=5)),
        ("https://wrong-issuer.test.example", AUDIENCE, timedelta(minutes=5)),
        (ISSUER, AUDIENCE, timedelta(seconds=-1)),
    ],
)
def test_invalid_signed_tokens_are_rejected_without_details(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    issuer: str,
    audience: str,
    expires_delta: timedelta,
) -> None:
    token = make_token(
        signing_key,
        issuer=issuer,
        audience=audience,
        expires_delta=expires_delta,
    )

    response = client.get("/api/v1/cases", headers=authorization(token))

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert set(response.json()) == {"code", "messageVi", "correlationId", "retryable"}


def test_token_without_subject_is_rejected(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "roles": ["INTAKE_OFFICER"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )

    response = client.get("/api/v1/cases", headers=authorization(token))

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_case_response_exposes_capabilities_not_hidden_rules(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    response = client.post(
        "/api/v1/cases",
        headers=authorization(make_token(signing_key)),
        json={
            "requestedAmount": "5000000000",
            "purpose": "Bổ sung vốn lưu động",
        },
    )

    assert response.status_code == 201
    assert response.headers["location"] == f"/api/v1/cases/{response.json()['id']}"
    assert response.json()["capabilities"] == {
        "canUpload": True,
        "canConfirm": True,
        "canCompleteIntake": True,
    }
    assert response.json()["assignedOfficerId"] == str(OFFICER_A)
    assert "canApprove" not in response.json()["capabilities"]
    assert [event.event_type for event in uow_factory.events] == ["CASE_CREATED"]


def test_openapi_capabilities_match_web_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    properties = schema["components"]["schemas"]["CaseCapabilities"]["properties"]
    assert set(properties) == {"canUpload", "canConfirm", "canCompleteIntake"}


def test_list_is_flat_cursor_ready_and_scoped_to_assigned_officer(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    token = make_token(signing_key)
    create_response = client.post(
        "/api/v1/cases",
        headers=authorization(token),
        json={"requestedAmount": "1", "purpose": "Vốn lưu động"},
    )

    response = client.get(
        "/api/v1/cases?limit=20",
        headers=authorization(token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [create_response.json()],
        "nextCursor": None,
    }


def test_other_officer_cannot_distinguish_case_from_missing_case(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    created = client.post(
        "/api/v1/cases",
        headers=authorization(make_token(signing_key)),
        json={"requestedAmount": "1", "purpose": "Vốn lưu động"},
    )
    other_headers = authorization(make_token(signing_key, subject=OFFICER_B))

    forbidden = client.get(
        f"/api/v1/cases/{created.json()['id']}",
        headers=other_headers,
    )
    missing = client.get(f"/api/v1/cases/{uuid4()}", headers=other_headers)

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json()["code"] == missing.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert forbidden.json()["messageVi"] == missing.json()["messageVi"]
    assert set(forbidden.json()) == {"code", "messageVi", "correlationId", "retryable"}


def test_non_intake_role_cannot_create_case(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    token = make_token(signing_key, roles=["RISK_REVIEWER"])

    response = client.post(
        "/api/v1/cases",
        headers=authorization(token),
        json={"requestedAmount": "1", "purpose": "Vốn lưu động"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_validation_error_uses_flat_vietnamese_contract(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    response = client.post(
        "/api/v1/cases",
        headers=authorization(make_token(signing_key)),
        json={"requestedAmount": "-1", "purpose": ""},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert set(response.json()) == {"code", "messageVi", "correlationId", "retryable"}
