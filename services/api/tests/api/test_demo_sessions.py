from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from creditops.application.ports.repositories import AuditEvent, CaseRecord, ForbiddenError
from creditops.config import Settings
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI
from creditops.main import create_app

AUDIENCE = "creditops-api"
ISSUER = "https://demo.creditops.local/"


class FakeCaseRepository:
    def __init__(
        self,
        records: dict[UUID, CaseRecord],
        roles: dict[tuple[UUID, UUID], set[str]],
    ) -> None:
        self.records = records
        self.roles = roles

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
        self.roles.setdefault((record.id, assigned_officer_id), set()).add("INTAKE_OFFICER")
        return record

    async def require_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord:
        record = self.records.get(case_id)
        if record is None or (case_id, actor_id) not in self.roles:
            raise ForbiddenError
        return record


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
        roles: dict[tuple[UUID, UUID], set[str]],
    ) -> None:
        self.cases = FakeCaseRepository(records, roles)
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
        self.roles: dict[tuple[UUID, UUID], set[str]] = {}
        self.actors: list[UUID] = []

    def __call__(self, actor: Any) -> FakeUnitOfWork:
        self.actors.append(actor.actor_id)
        return FakeUnitOfWork(self.records, self.events, self.roles)


def _demo_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _demo_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "demo_session_enabled": True,
        "demo_jwt_private_key": SecretStr(_demo_private_key_pem()),
        "demo_jwt_issuer": ISSUER,
        "demo_jwt_audience": AUDIENCE,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def uow_factory() -> FakeUnitOfWorkFactory:
    return FakeUnitOfWorkFactory()


def _client(settings: Settings, uow_factory: FakeUnitOfWorkFactory) -> TestClient:
    return TestClient(create_app(settings=settings, uow_factory=uow_factory))


def test_mint_returns_synthetic_session_contract(uow_factory: FakeUnitOfWorkFactory) -> None:
    client = _client(_demo_settings(), uow_factory)

    response = client.post("/api/v1/demo-sessions", json={})

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "sessionToken",
        "tokenType",
        "expiresInSeconds",
        "actorId",
        "caseId",
        "roles",
        "disclaimer",
    }
    assert body["tokenType"] == "Bearer"
    assert body["expiresInSeconds"] == 3600
    assert body["roles"] == ["INTAKE_OFFICER"]
    assert body["disclaimer"] == SYNTHETIC_NOTICE_VI
    # The synthetic actor and its case were created through the real create-case
    # flow in a single unit of work, self-assigning the intake role.
    assert uow_factory.roles[(UUID(body["caseId"]), UUID(body["actorId"]))] == {"INTAKE_OFFICER"}
    assert [event.event_type for event in uow_factory.events] == ["CASE_CREATED"]


def test_no_secret_material_in_response(uow_factory: FakeUnitOfWorkFactory) -> None:
    settings = _demo_settings()
    client = _client(settings, uow_factory)

    response = client.post("/api/v1/demo-sessions", json={})

    assert response.status_code == 201
    raw = response.text
    assert "PRIVATE KEY" not in raw
    assert settings.demo_jwt_private_key is not None
    assert settings.demo_jwt_private_key.get_secret_value() not in raw


def test_minted_token_is_accepted_by_require_actor_round_trip(
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    client = _client(_demo_settings(), uow_factory)

    minted = client.post("/api/v1/demo-sessions", json={})
    token = minted.json()["sessionToken"]

    # Standard Authorization header round-trips through the same verifier.
    via_standard = client.get(
        "/api/v1/configuration",
        headers={"Authorization": f"Bearer {token}"},
    )
    # The app-session header round-trips too (Cloud Run private + app auth).
    via_app_header = client.get(
        "/api/v1/configuration",
        headers={"X-CreditOps-Authorization": f"Bearer {token}"},
    )

    assert via_standard.status_code == 200
    assert via_app_header.status_code == 200
    assert via_standard.json()["label"] == "SYNTHETIC"


def test_two_sessions_are_isolated(uow_factory: FakeUnitOfWorkFactory) -> None:
    client = _client(_demo_settings(), uow_factory)

    first = client.post("/api/v1/demo-sessions", json={}).json()
    second = client.post("/api/v1/demo-sessions", json={}).json()

    assert first["actorId"] != second["actorId"]
    assert first["caseId"] != second["caseId"]


def test_rate_limit_returns_429_error_contract(uow_factory: FakeUnitOfWorkFactory) -> None:
    settings = _demo_settings(
        demo_session_rate_limit_burst=1,
        demo_session_rate_limit_refill_per_second=0.0,
    )
    client = _client(settings, uow_factory)

    first = client.post("/api/v1/demo-sessions", json={})
    second = client.post("/api/v1/demo-sessions", json={})

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"
    assert second.json()["retryable"] is True
    assert set(second.json()) == {"code", "messageVi", "correlationId", "retryable", "details"}


def test_endpoint_absent_when_demo_disabled(uow_factory: FakeUnitOfWorkFactory) -> None:
    settings = Settings(app_env="test")
    client = _client(settings, uow_factory)

    response = client.post("/api/v1/demo-sessions", json={})

    assert response.status_code == 404


def test_demo_disabled_requires_no_private_key() -> None:
    # Disabled by default: no key required and no validation error.
    settings = Settings(app_env="test")
    assert settings.demo_session_enabled is False


def _production_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "app_env": "production",
        "database_url": SecretStr("postgresql://user:pass@db.example.co:5432/postgres"),
        "supabase_url": "https://project.supabase.co",
        "supabase_service_role_key": SecretStr("synthetic-service-role"),
    }
    base.update(overrides)
    return base


def test_production_with_demo_enabled_starts_without_external_oidc() -> None:
    # The demo issuer replaces OIDC_*; production must construct without them.
    settings = Settings(
        **_production_kwargs(
            demo_session_enabled=True,
            demo_jwt_private_key=SecretStr(_demo_private_key_pem()),
        )
    )

    assert settings.demo_session_enabled is True
    assert settings.oidc_issuer is None


def test_production_without_demo_still_requires_oidc() -> None:
    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        Settings(**_production_kwargs())


def test_production_demo_enabled_without_key_fails_closed() -> None:
    with pytest.raises(ValueError, match="DEMO_JWT_PRIVATE_KEY"):
        Settings(**_production_kwargs(demo_session_enabled=True))
