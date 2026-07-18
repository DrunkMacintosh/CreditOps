"""Role-gated API tests for GET /api/v1/work-items (the ``/cong-viec`` surface).

Mirrors ``tests/api/test_audit.py``: house RS256 JWT fixtures plus a fake
repository.  ``WorkItemRepository`` is a structural Protocol, so the fake only
implements ``list_for_actor``.  These tests pin the API-layer JWT/role AND (the
repository is only ever asked for ``actor.roles & CASE_PARTICIPANT_ROLES``), the
response shape/route strings, the limit clamp, and the 401/403 negatives.  The
deterministic severity ordering and SQL scoping live in the adapter and are
proven in ``tests/contract/postgres/test_work_items_adapter.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.work_items import router as work_items_router
from creditops.application.ports.work_items import Severity, WorkItem
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _item(
    *,
    kind: str,
    severity: Severity,
    route: str,
    seq: int = 0,
) -> WorkItem:
    return WorkItem(
        case_id=CASE_ID,
        case_version=1,
        kind=kind,
        title_vi=f"tiêu đề {kind}",
        reason_vi=f"lý do {kind}",
        severity=severity,
        primary_route=route,
        created_at=NOW - timedelta(minutes=seq),
    )


class FakeWorkItemRepository:
    """In-memory queue keyed by case role.

    ``list_for_actor`` returns the items catalogued under exactly the roles it
    is handed -- so if the API only passes the JWT-intersected roles, a role
    catalogued but not passed can never surface.  It also records the actor,
    roles and limit it received for API-boundary assertions.
    """

    def __init__(self, catalog: dict[str, list[WorkItem]] | None = None) -> None:
        self._catalog = catalog or {}
        self.received_actor: UUID | None = None
        self.received_roles: frozenset[str] | None = None
        self.received_limit: int | None = None

    async def list_for_actor(
        self, actor_id: UUID, roles: frozenset[str], *, limit: int
    ) -> tuple[WorkItem, ...]:
        self.received_actor = actor_id
        self.received_roles = roles
        self.received_limit = limit
        items: list[WorkItem] = []
        for role in sorted(roles):
            items.extend(self._catalog.get(role, []))
        return tuple(items[:limit])


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_client(
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeWorkItemRepository | None = None,
    wire_repository: bool = True,
) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=JwksKeyResolver({"keys": [jwk]})
    )
    application = create_app(settings=Settings(app_env="test"), jwt_verifier=verifier)
    # NEW router (deliberately not wired into main.py -- the lead wires it):
    # mount it directly on this test's own app instance instead.
    application.include_router(work_items_router)
    if wire_repository:
        application.state.work_item_repository = repository or FakeWorkItemRepository()
    return TestClient(application)


def token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER,
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
    return {"Authorization": f"Bearer {token_value}", "X-Request-ID": "request-work-items"}


def test_repository_receives_jwt_roles_intersected_with_participant_vocab(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeWorkItemRepository()
    client = _build_client(signing_key, repository=repository)

    # JWT carries a participant role plus two roles outside the case-participant
    # vocabulary; only the participant role may reach the repository.
    response = client.get(
        "/api/v1/work-items",
        headers=auth(token(signing_key, roles=["RISK_REVIEWER", "AUDITOR", "SOMETHING_ELSE"])),
    )

    assert response.status_code == 200
    assert repository.received_actor == OFFICER
    assert repository.received_roles == frozenset({"RISK_REVIEWER"})


def test_case_role_without_matching_jwt_role_yields_no_items(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # The catalog holds an OPS item (as if the actor were assigned OPS_OFFICER on
    # the case), but the JWT only carries RISK_REVIEWER, so the API never asks
    # for OPS items and none surface.
    catalog = {
        "RISK_REVIEWER": [
            _item(
                kind="RISK_DISPOSITION_PENDING",
                severity="ATTENTION",
                route=f"/ho-so/{CASE_ID}/rui-ro",
            )
        ],
        "OPS_OFFICER": [
            _item(
                kind="OPS_AUTHORIZATION_PENDING",
                severity="ATTENTION",
                route=f"/ho-so/{CASE_ID}/tong-hop",
            )
        ],
    }
    client = _build_client(signing_key, repository=FakeWorkItemRepository(catalog))

    response = client.get(
        "/api/v1/work-items", headers=auth(token(signing_key, roles=["RISK_REVIEWER"]))
    )

    assert response.status_code == 200
    kinds = [item["kind"] for item in response.json()["items"]]
    assert kinds == ["RISK_DISPOSITION_PENDING"]


def test_response_shape_is_camel_case_with_correct_route_strings(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    catalog = {
        "INTAKE_OFFICER": [
            _item(
                kind="GAP_BATCH_PENDING",
                severity="ATTENTION",
                route=f"/ho-so/{CASE_ID}/khoang-trong",
            )
        ]
    }
    client = _build_client(signing_key, repository=FakeWorkItemRepository(catalog))

    response = client.get(
        "/api/v1/work-items", headers=auth(token(signing_key, roles=["INTAKE_OFFICER"]))
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items"}
    [entry] = body["items"]
    assert set(entry) == {
        "caseId",
        "caseVersion",
        "kind",
        "titleVi",
        "reasonVi",
        "severity",
        "primaryRoute",
        "createdAt",
    }
    assert entry["caseId"] == str(CASE_ID)
    assert entry["caseVersion"] == 1
    assert entry["kind"] == "GAP_BATCH_PENDING"
    assert entry["titleVi"] == "tiêu đề GAP_BATCH_PENDING"
    assert entry["reasonVi"] == "lý do GAP_BATCH_PENDING"
    assert entry["severity"] == "ATTENTION"
    assert entry["primaryRoute"] == f"/ho-so/{CASE_ID}/khoang-trong"


def test_route_strings_cover_each_kind(signing_key: rsa.RSAPrivateKey) -> None:
    expected = {
        "INTAKE_INCOMPLETE": f"/ho-so/{CASE_ID}/tiep-nhan",
        "GAP_BATCH_PENDING": f"/ho-so/{CASE_ID}/khoang-trong",
        "RISK_DISPOSITION_PENDING": f"/ho-so/{CASE_ID}/rui-ro",
        "OPS_AUTHORIZATION_PENDING": f"/ho-so/{CASE_ID}/tong-hop",
        "MANUAL_REVIEW": f"/ho-so/{CASE_ID}/quy-trinh",
        "RETRY_WAIT": f"/ho-so/{CASE_ID}/quy-trinh",
    }
    catalog = {
        "INTAKE_OFFICER": [
            _item(kind=k, severity="ATTENTION", route=r)
            for k, r in expected.items()
        ]
    }
    client = _build_client(signing_key, repository=FakeWorkItemRepository(catalog))

    response = client.get(
        "/api/v1/work-items",
        headers=auth(token(signing_key, roles=["INTAKE_OFFICER"])),
        params={"limit": 200},
    )

    assert response.status_code == 200
    routes = {item["kind"]: item["primaryRoute"] for item in response.json()["items"]}
    assert routes == expected


def test_items_are_returned_in_repository_order(signing_key: rsa.RSAPrivateKey) -> None:
    # The repository yields BLOCKING-first (its deterministic order); the API
    # must pass that order through unchanged.
    ordered = [
        _item(
            kind="MANUAL_REVIEW",
            severity="BLOCKING",
            route=f"/ho-so/{CASE_ID}/quy-trinh",
            seq=5,
        ),
        _item(
            kind="RETRY_WAIT",
            severity="INFO",
            route=f"/ho-so/{CASE_ID}/quy-trinh",
            seq=0,
        ),
    ]
    catalog = {"INTAKE_OFFICER": ordered}
    client = _build_client(signing_key, repository=FakeWorkItemRepository(catalog))

    response = client.get(
        "/api/v1/work-items", headers=auth(token(signing_key, roles=["INTAKE_OFFICER"]))
    )

    assert response.status_code == 200
    severities = [item["severity"] for item in response.json()["items"]]
    assert severities == ["BLOCKING", "INFO"]


def test_default_limit_is_fifty(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeWorkItemRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get("/api/v1/work-items", headers=auth(token(signing_key)))

    assert response.status_code == 200
    assert repository.received_limit == 50


def test_explicit_limit_is_forwarded(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeWorkItemRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        "/api/v1/work-items", headers=auth(token(signing_key)), params={"limit": 25}
    )

    assert response.status_code == 200
    assert repository.received_limit == 25


def test_limit_out_of_range_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)
    headers = auth(token(signing_key))

    too_low = client.get("/api/v1/work-items", params={"limit": 0}, headers=headers)
    too_high = client.get("/api/v1/work-items", params={"limit": 201}, headers=headers)

    assert too_low.status_code == too_high.status_code == 422


def test_missing_bearer_is_401(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)

    response = client.get("/api/v1/work-items", headers={"X-Request-ID": "no-auth"})

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_non_participant_role_is_403(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)

    response = client.get(
        "/api/v1/work-items", headers=auth(token(signing_key, roles=["AUDITOR"]))
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_non_participant_role_does_not_reach_repository(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeWorkItemRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        "/api/v1/work-items", headers=auth(token(signing_key, roles=["AUDITOR"]))
    )

    assert response.status_code == 403
    # Fail closed before any repository read.
    assert repository.received_roles is None
