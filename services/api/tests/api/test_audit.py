"""Role-gated API tests for GET /cases/{id}/audit-events.

Mirrors the fake style in ``tests/api/test_orchestration.py``: a
``FakeUnitOfWork``/``FakeCases`` pair backs the case-assignment check, and a
fake orchestration repository backs ``list_audit_events``. The Protocol on
``OrchestrationRepository`` is structural, so this fake only needs to
implement the one method this router calls.
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

from creditops.api.audit import router as audit_router
from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.application.ports.orchestration import AuditEventRow
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000003")
OTHER_CASE_ID = UUID("10000000-0000-0000-0000-000000000099")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _event(
    *,
    seq: int,
    case_id: UUID = CASE_ID,
    event_type: str = "CASE_CREATED",
    actor_id: UUID | None = None,
) -> AuditEventRow:
    return AuditEventRow(
        id=UUID(f"70000000-0000-0000-0000-{seq:012d}"),
        case_id=case_id,
        case_version=1,
        event_type=event_type,
        actor_type="HUMAN" if actor_id is not None else "AGENT:CASE_ORCHESTRATOR",
        actor_id=actor_id,
        artifact_type="CREDIT_CASE",
        artifact_id=case_id,
        event_data={"note": f"event-{seq}"},
        created_at=NOW - timedelta(minutes=seq),
    )


class FakeOrchestrationRepository:
    """Newest-first in-memory audit log matching the real repository's contract.

    ``events`` must already be supplied newest-first (matching how the
    Postgres adapter orders rows); the fake resolves a cursor by locating the
    given id and returning everything strictly after it, exactly mirroring
    the server-side keyset resolution the real adapter performs in SQL.
    """

    def __init__(self, events: list[AuditEventRow] | None = None) -> None:
        self._events = events or []

    async def list_audit_events(
        self, case_id: UUID, *, cursor: UUID | None, limit: int
    ) -> tuple[tuple[AuditEventRow, ...], UUID | None]:
        scoped = [event for event in self._events if event.case_id == case_id]
        if cursor is not None:
            index = next((i for i, event in enumerate(scoped) if event.id == cursor), None)
            if index is None:
                return (), None
            scoped = scoped[index + 1 :]
        page = tuple(scoped[:limit])
        next_cursor = page[-1].id if len(scoped) > limit else None
        return page, next_cursor


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=OFFICER_A,
            requested_amount="1",
            purpose_vi="Vốn lưu động",
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
    orchestration_repository: FakeOrchestrationRepository | None = None,
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
    # NEW router (deliberately not wired into main.py -- the lead wires it):
    # mount it directly on this test's own app instance instead.
    application.include_router(audit_router)
    application.state.orchestration_repository = (
        orchestration_repository
        if orchestration_repository is not None
        else FakeOrchestrationRepository()
    )
    return TestClient(application)


@pytest.fixture
def client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    return _build_client(signing_key)


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
            "roles": roles or ["INTAKE_OFFICER"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def auth(token_value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_value}", "X-Request-ID": "request-audit"}


def test_pagination_walks_newest_first_without_duplicates_across_pages(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    events = [_event(seq=index) for index in range(5)]  # already newest-first
    repository = FakeOrchestrationRepository(events)
    client = _build_client(signing_key, orchestration_repository=repository)
    headers = auth(token(signing_key))

    first = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events", params={"limit": 2}, headers=headers
    )
    assert first.status_code == 200
    first_body = first.json()
    first_ids = [event["id"] for event in first_body["events"]]
    assert first_ids == [str(events[0].id), str(events[1].id)]
    assert first_body["nextCursor"] == str(events[1].id)

    second = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events",
        params={"limit": 2, "cursor": first_body["nextCursor"]},
        headers=headers,
    )
    assert second.status_code == 200
    second_body = second.json()
    second_ids = [event["id"] for event in second_body["events"]]
    assert second_ids == [str(events[2].id), str(events[3].id)]
    assert second_body["nextCursor"] == str(events[3].id)

    third = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events",
        params={"limit": 2, "cursor": second_body["nextCursor"]},
        headers=headers,
    )
    assert third.status_code == 200
    third_body = third.json()
    assert [event["id"] for event in third_body["events"]] == [str(events[4].id)]
    assert third_body["nextCursor"] is None

    seen_ids = (
        [event["id"] for event in first_body["events"]]
        + [event["id"] for event in second_body["events"]]
        + [event["id"] for event in third_body["events"]]
    )
    assert seen_ids == [str(event.id) for event in events]
    assert len(set(seen_ids)) == len(seen_ids)


def test_event_shape_is_camel_case_and_passes_event_data_through(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    event = _event(seq=0, actor_id=OFFICER_A)
    client = _build_client(
        signing_key, orchestration_repository=FakeOrchestrationRepository([event])
    )

    response = client.get(f"/api/v1/cases/{CASE_ID}/audit-events", headers=auth(token(signing_key)))

    assert response.status_code == 200
    body = response.json()
    assert body["nextCursor"] is None
    [entry] = body["events"]
    assert entry["id"] == str(event.id)
    assert entry["caseVersion"] == 1
    assert entry["eventType"] == "CASE_CREATED"
    assert entry["actorType"] == "HUMAN"
    assert entry["actorId"] == str(OFFICER_A)
    assert entry["artifactType"] == "CREDIT_CASE"
    assert entry["artifactId"] == str(CASE_ID)
    assert entry["eventData"] == {"note": "event-0"}
    assert "createdAt" in entry


def test_agent_event_has_null_actor_id(signing_key: rsa.RSAPrivateKey) -> None:
    event = _event(seq=0)  # actor_id defaults to None (agent-authored)
    client = _build_client(
        signing_key, orchestration_repository=FakeOrchestrationRepository([event])
    )

    response = client.get(f"/api/v1/cases/{CASE_ID}/audit-events", headers=auth(token(signing_key)))

    assert response.status_code == 200
    assert response.json()["events"][0]["actorId"] is None


def test_limit_out_of_range_is_422(client: TestClient, signing_key: rsa.RSAPrivateKey) -> None:
    headers = auth(token(signing_key))

    too_low = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events", params={"limit": 0}, headers=headers
    )
    too_high = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events", params={"limit": 201}, headers=headers
    )

    assert too_low.status_code == too_high.status_code == 422


def test_default_limit_is_fifty(signing_key: rsa.RSAPrivateKey) -> None:
    events = [_event(seq=index) for index in range(60)]
    repository = FakeOrchestrationRepository(events)
    client = _build_client(signing_key, orchestration_repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events", headers=auth(token(signing_key))
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 50
    assert body["nextCursor"] == str(events[49].id)


def test_unassigned_actor_gets_indistinguishable_404(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events",
        headers=auth(token(signing_key, subject=uuid4())),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"

    missing_case = client.get(
        f"/api/v1/cases/{uuid4()}/audit-events",
        headers=auth(token(signing_key)),
    )
    assert missing_case.status_code == 404
    assert missing_case.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_non_participant_role_is_rejected(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events",
        headers=auth(token(signing_key, roles=["AUDITOR"])),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_empty_case_returns_empty_list_and_null_cursor(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events", headers=auth(token(signing_key))
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"events": [], "nextCursor": None}
