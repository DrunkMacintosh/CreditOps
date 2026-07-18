"""API tests for GET /api/v1/configuration (the ``/cau-hinh`` surface).

Pins the participant gate, the truthful catalog state (a benchmark-absent
capability reports ``benchmarkPassed: false``), the closed synthetic role set
(drift-checked against ``api/cases.py``'s role usage), and -- load-bearing --
that NO secret material (endpoint URL / key) ever appears in the payload.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.config_view import SYNTHETIC_CASE_ROLE_SET
from creditops.api.config_view import router as config_router
from creditops.application.orchestration.gates import (
    ALL_GATES,
    G3_CONTINUE_DISPOSITION_TYPES,
)
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE
from creditops.config import Settings
from creditops.infrastructure.fpt.benchmark_records import FPT_BENCHMARK_RECORDS
from creditops.infrastructure.fpt.model_catalog import FPT_MODEL_CATALOG
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

# A conservative "looks like a credential" heuristic: a long unbroken run of
# base64/hex-ish characters.  Model ids like ``DeepSeek-V4-Flash`` are short and
# hyphen-broken, so they do not trip it.
_KEY_SHAPED_RE = re.compile(r"[A-Za-z0-9+/_-]{40,}")


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=JwksKeyResolver({"keys": [jwk]})
    )
    application = create_app(settings=Settings(app_env="test"), jwt_verifier=verifier)
    application.include_router(config_router)
    return TestClient(application)


@pytest.fixture
def client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    return _build_client(signing_key)


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
    return {"Authorization": f"Bearer {token_value}", "X-Request-ID": "request-config"}


def test_participant_reads_configuration_identity(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get("/api/v1/configuration", headers=auth(token(signing_key)))

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "SYNTHETIC"
    assert set(body) == {
        "label",
        "gateRegistry",
        "dispositionTaxonomies",
        "syntheticRoleSet",
        "fptCatalog",
        "allocationPolicyVersion",
        "noticeRefs",
    }


def test_gate_registry_mirrors_all_gates(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    body = client.get("/api/v1/configuration", headers=auth(token(signing_key))).json()
    assert body["gateRegistry"] == [gate.value for gate in ALL_GATES]


def test_disposition_taxonomies_expose_the_closed_sets(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    taxonomies = client.get(
        "/api/v1/configuration", headers=auth(token(signing_key))
    ).json()["dispositionTaxonomies"]

    assert taxonomies["g3ContinueDispositionTypes"] == sorted(G3_CONTINUE_DISPOSITION_TYPES)
    assert set(taxonomies["batchDispositionTypes"]) == {
        "APPROVED_ALL",
        "APPROVED_WITH_CHANGES",
        "REJECTED",
        "NO_OUTBOUND_REQUESTS",
    }
    assert "VERIFIED" in taxonomies["conditionStatuses"]
    assert "COMPLETED" in taxonomies["perfectionStatuses"]


def test_synthetic_role_set_matches_module_constant(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    body = client.get("/api/v1/configuration", headers=auth(token(signing_key))).json()
    assert body["syntheticRoleSet"] == list(SYNTHETIC_CASE_ROLE_SET)


def test_role_set_does_not_drift_from_cases_role_usage() -> None:
    # The roles ``api/cases.py`` actually uses must all be members of the closed
    # synthetic set the configuration surface publishes (no silent drift).
    assert INTAKE_OFFICER_ROLE in SYNTHETIC_CASE_ROLE_SET
    assert CASE_PARTICIPANT_ROLES <= set(SYNTHETIC_CASE_ROLE_SET)


def test_fpt_catalog_state_is_truthful_and_benchmark_absent_is_false(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    catalog = client.get(
        "/api/v1/configuration", headers=auth(token(signing_key))
    ).json()["fptCatalog"]

    by_capability = {entry["capability"]: entry for entry in catalog}
    # Every closed catalog capability is present.
    assert {"reasoning", "kie", "table", "vision", "embedding"} <= set(by_capability)

    # Pinned model ids mirror FPT_MODEL_CATALOG; unpinned capabilities are null.
    for capability, entry in by_capability.items():
        assert entry["pinnedModelId"] == FPT_MODEL_CATALOG.get(capability)
        # The benchmark registry is empty, so every capability must report false.
        assert entry["benchmarkPassed"] is False

    assert FPT_BENCHMARK_RECORDS == ()  # guards the premise of the assertion above


def test_payload_contains_no_secret_material(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get("/api/v1/configuration", headers=auth(token(signing_key)))

    raw = response.text
    # No endpoint URLs of any scheme, and no key-shaped strings.
    assert "http" not in raw.lower()
    assert _KEY_SHAPED_RE.search(raw) is None

    # And structurally: never an endpoint / key field anywhere in the payload.
    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered_key = key.lower()
                assert "endpoint" not in lowered_key
                assert "url" not in lowered_key
                assert "apikey" not in lowered_key
                assert "secret" not in lowered_key
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(response.json())


def test_notice_refs_point_at_the_single_source_of_truth(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    notice = client.get(
        "/api/v1/configuration", headers=auth(token(signing_key))
    ).json()["noticeRefs"]
    assert notice["source"] == "shared/synthetic-notice.json"
    assert notice["vi"] and notice["en"]


def test_allocation_policy_version_is_exposed(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    body = client.get("/api/v1/configuration", headers=auth(token(signing_key))).json()
    assert body["allocationPolicyVersion"] == "collections-allocation-v1"


def test_missing_bearer_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/configuration", headers={"X-Request-ID": "x"})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_non_participant_role_is_403(client: TestClient, signing_key: rsa.RSAPrivateKey) -> None:
    # A REPORTING_VIEWER / AUDITOR-only token holds no case-participant role.
    response = client.get(
        "/api/v1/configuration",
        headers=auth(token(signing_key, roles=["REPORTING_VIEWER"])),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
