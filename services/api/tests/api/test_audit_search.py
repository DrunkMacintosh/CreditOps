"""Role-gated API tests for GET /api/v1/audit-events (cross-case auditor view).

Mirrors ``tests/api/test_audit.py`` but for the estate-wide surface: the gate is
the synthetic AUDITOR role alone (no case-assignment check), events span several
cases, and an optional ``eventType`` filter is validated at the boundary.
``OrchestrationRepository`` is a structural Protocol, so the fake only implements
``list_audit_events_all``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.audit_search import router as audit_search_router
from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.application.ports.orchestration import AuditEventRow
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
AUDITOR = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_A = UUID("10000000-0000-0000-0000-00000000000a")
CASE_B = UUID("10000000-0000-0000-0000-00000000000b")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _event(
    *,
    seq: int,
    case_id: UUID = CASE_A,
    event_type: str = "CASE_CREATED",
) -> AuditEventRow:
    return AuditEventRow(
        id=UUID(f"70000000-0000-0000-0000-{seq:012d}"),
        case_id=case_id,
        case_version=1,
        event_type=event_type,
        actor_type="AGENT:CASE_ORCHESTRATOR",
        actor_id=None,
        artifact_type="CREDIT_CASE",
        artifact_id=case_id,
        event_data={"note": f"event-{seq}"},
        created_at=NOW - timedelta(minutes=seq),
    )


class FakeOrchestrationRepository:
    """Cross-case newest-first in-memory log matching the adapter's contract.

    ``events`` are supplied newest-first; the fake resolves a cursor by
    locating the id and returning everything strictly after it, and applies the
    optional ``event_type`` exact-match filter -- mirroring the SQL adapter.
    """

    def __init__(self, events: list[AuditEventRow] | None = None) -> None:
        self._events = events or []
        self.received_event_type: str | None = None

    async def list_audit_events_all(
        self,
        *,
        cursor: UUID | None,
        limit: int,
        event_type: str | None = None,
    ) -> tuple[tuple[AuditEventRow, ...], UUID | None]:
        self.received_event_type = event_type
        scoped = [
            event
            for event in self._events
            if event_type is None or event.event_type == event_type
        ]
        if cursor is not None:
            index = next((i for i, e in enumerate(scoped) if e.id == cursor), None)
            if index is None:
                return (), None
            scoped = scoped[index + 1 :]
        page = tuple(scoped[:limit])
        next_cursor = page[-1].id if len(scoped) > limit else None
        return page, next_cursor


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
    application = create_app(settings=Settings(app_env="test"), jwt_verifier=verifier)
    application.include_router(audit_search_router)
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
    subject: UUID = AUDITOR,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(subject),
            "roles": roles or ["AUDITOR"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def auth(token_value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_value}", "X-Request-ID": "request-audit-search"}


def test_pagination_walks_newest_first_across_cases_without_duplicates(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # Interleave two cases; the timeline is estate-wide, newest-first.
    events = [
        _event(seq=index, case_id=CASE_A if index % 2 == 0 else CASE_B)
        for index in range(5)
    ]
    repository = FakeOrchestrationRepository(events)
    client = _build_client(signing_key, orchestration_repository=repository)
    headers = auth(token(signing_key))

    first = client.get("/api/v1/audit-events", params={"limit": 2}, headers=headers)
    assert first.status_code == 200
    first_body = first.json()
    assert [e["id"] for e in first_body["events"]] == [str(events[0].id), str(events[1].id)]
    assert first_body["nextCursor"] == str(events[1].id)
    # Cross-case: distinct case ids surface side by side.
    assert {e["caseId"] for e in first_body["events"]} == {str(CASE_A), str(CASE_B)}

    second = client.get(
        "/api/v1/audit-events",
        params={"limit": 2, "cursor": first_body["nextCursor"]},
        headers=headers,
    )
    second_body = second.json()
    assert [e["id"] for e in second_body["events"]] == [str(events[2].id), str(events[3].id)]

    third = client.get(
        "/api/v1/audit-events",
        params={"limit": 2, "cursor": second_body["nextCursor"]},
        headers=headers,
    )
    third_body = third.json()
    assert [e["id"] for e in third_body["events"]] == [str(events[4].id)]
    assert third_body["nextCursor"] is None

    seen = (
        [e["id"] for e in first_body["events"]]
        + [e["id"] for e in second_body["events"]]
        + [e["id"] for e in third_body["events"]]
    )
    assert seen == [str(e.id) for e in events]
    assert len(set(seen)) == len(seen)


def test_event_shape_is_camel_case_and_exposes_case_id(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    event = _event(seq=0, case_id=CASE_B)
    client = _build_client(
        signing_key, orchestration_repository=FakeOrchestrationRepository([event])
    )

    response = client.get("/api/v1/audit-events", headers=auth(token(signing_key)))

    assert response.status_code == 200
    [entry] = response.json()["events"]
    assert set(entry) == {
        "id",
        "caseId",
        "caseVersion",
        "eventType",
        "actorType",
        "actorId",
        "artifactType",
        "artifactId",
        "eventData",
        "createdAt",
    }
    assert entry["caseId"] == str(CASE_B)
    assert entry["eventType"] == "CASE_CREATED"
    assert entry["actorId"] is None


def test_event_type_filter_is_forwarded_to_repository(signing_key: rsa.RSAPrivateKey) -> None:
    events = [
        _event(seq=0, event_type="CASE_CREATED"),
        _event(seq=1, event_type="CASE_VERSION_BUMPED"),
    ]
    repository = FakeOrchestrationRepository(events)
    client = _build_client(signing_key, orchestration_repository=repository)

    response = client.get(
        "/api/v1/audit-events",
        params={"eventType": "CASE_VERSION_BUMPED"},
        headers=auth(token(signing_key)),
    )

    assert response.status_code == 200
    assert repository.received_event_type == "CASE_VERSION_BUMPED"
    assert [e["eventType"] for e in response.json()["events"]] == ["CASE_VERSION_BUMPED"]


@pytest.mark.parametrize(
    "bad_filter",
    ["lowercase", "has space", "semi;colon", "drop--table", "a" * 65, ""],
)
def test_malformed_event_type_filter_is_422(
    client: TestClient, signing_key: rsa.RSAPrivateKey, bad_filter: str
) -> None:
    response = client.get(
        "/api/v1/audit-events",
        params={"eventType": bad_filter},
        headers=auth(token(signing_key)),
    )
    assert response.status_code == 422


def test_limit_out_of_range_is_422(client: TestClient, signing_key: rsa.RSAPrivateKey) -> None:
    headers = auth(token(signing_key))
    too_low = client.get("/api/v1/audit-events", params={"limit": 0}, headers=headers)
    too_high = client.get("/api/v1/audit-events", params={"limit": 201}, headers=headers)
    assert too_low.status_code == too_high.status_code == 422


def test_default_limit_is_fifty(signing_key: rsa.RSAPrivateKey) -> None:
    events = [_event(seq=index) for index in range(60)]
    client = _build_client(
        signing_key, orchestration_repository=FakeOrchestrationRepository(events)
    )

    body = client.get("/api/v1/audit-events", headers=auth(token(signing_key))).json()

    assert len(body["events"]) == 50
    assert body["nextCursor"] == str(events[49].id)


def test_missing_bearer_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/audit-events", headers={"X-Request-ID": "x"})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_non_auditor_role_is_403(client: TestClient, signing_key: rsa.RSAPrivateKey) -> None:
    # A case-participant role is NOT sufficient for the auditor surface.
    response = client.get(
        "/api/v1/audit-events",
        headers=auth(token(signing_key, roles=["INTAKE_OFFICER", "RISK_REVIEWER"])),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_unknown_cursor_yields_empty_page(signing_key: rsa.RSAPrivateKey) -> None:
    events = [_event(seq=0)]
    client = _build_client(
        signing_key, orchestration_repository=FakeOrchestrationRepository(events)
    )

    response = client.get(
        "/api/v1/audit-events",
        params={"cursor": str(uuid4())},
        headers=auth(token(signing_key)),
    )

    assert response.status_code == 200
    assert response.json() == {"events": [], "nextCursor": None}
