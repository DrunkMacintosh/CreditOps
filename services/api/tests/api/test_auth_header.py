from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
ACTOR = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        key_resolver=JwksKeyResolver({"keys": [jwk]}),
    )
    settings = Settings(
        app_env="test",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_url="https://identity.test.example/.well-known/jwks.json",
    )
    return TestClient(create_app(settings=settings, jwt_verifier=verifier))


def make_token(signing_key: rsa.RSAPrivateKey, *, roles: list[str] | None = None) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(ACTOR),
            "roles": roles or ["INTAKE_OFFICER"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


# ``/api/v1/configuration`` is a require_actor-gated read that needs only a
# participant role (INTAKE_OFFICER) and no unit of work — an ideal probe for the
# auth-header extraction logic.
_PROBE = "/api/v1/configuration"


def test_app_session_header_is_accepted(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        _PROBE,
        headers={"X-CreditOps-Authorization": f"Bearer {make_token(signing_key)}"},
    )

    assert response.status_code == 200


def test_standard_authorization_header_still_works(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        _PROBE,
        headers={"Authorization": f"Bearer {make_token(signing_key)}"},
    )

    assert response.status_code == 200


def test_app_session_header_takes_precedence_over_authorization(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    # In production the standard Authorization header carries the Cloud Run OIDC
    # id token (opaque to the app); the app session JWT arrives in the dedicated
    # header and must win.
    response = client.get(
        _PROBE,
        headers={
            "Authorization": "Bearer not-the-app-session-token",
            "X-CreditOps-Authorization": f"Bearer {make_token(signing_key)}",
        },
    )

    assert response.status_code == 200


def test_malformed_app_session_header_fails_closed(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    # When the app header is present but malformed the request fails closed; it
    # does NOT silently fall back to the Authorization header (which here holds a
    # valid token) because the app header is authoritative when present.
    response = client.get(
        _PROBE,
        headers={
            "Authorization": f"Bearer {make_token(signing_key)}",
            "X-CreditOps-Authorization": "Basic not-a-bearer",
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_no_credentials_is_unauthenticated(client: TestClient) -> None:
    response = client.get(_PROBE)

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
