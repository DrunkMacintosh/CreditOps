from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.config import Settings
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"


def _demo_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def test_health_stays_cheap_and_process_only() -> None:
    settings = Settings(app_env="test")
    client = TestClient(create_app(settings=settings))

    assert client.get("/api/v1/health").json() == {
        "service": "creditops-api",
        "status": "ok",
    }


def test_ready_reports_component_states_without_secrets() -> None:
    settings = Settings(
        app_env="test",
        demo_session_enabled=True,
        demo_jwt_private_key=SecretStr(_demo_private_key_pem()),
    )
    client = TestClient(create_app(settings=settings))

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "creditops-api"
    assert body["disclaimer"] == SYNTHETIC_NOTICE_VI
    components = body["components"]
    # No database/storage/queue configured in this minimal app: all disabled and
    # the overall gate is closed.
    assert components["database"]["status"] == "disabled"
    assert components["queue"]["status"] == "disabled"
    assert components["storage"]["status"] == "disabled"
    assert body["ready"] is False
    assert body["status"] == "not-ready"
    # Demo verifier is composed, so auth is reported ready in demo mode.
    assert components["auth"] == {"status": "ok", "mode": "demo"}
    # FPT capabilities are reported per-capability as a non-secret ACTIVE/DISABLED
    # state; no endpoint URL, id or key appears anywhere in the payload.
    capabilities = components["fpt"]["capabilities"]
    assert capabilities
    assert all(item["state"] in {"ACTIVE", "DISABLED"} for item in capabilities)
    assert "api_key" not in response.text.lower()
    assert "endpoint_url" not in response.text.lower()


def test_ready_reports_oidc_auth_mode() -> None:
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk: dict[str, Any] = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": "k", "alg": "RS256", "use": "sig"})
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
    client = TestClient(create_app(settings=settings, jwt_verifier=verifier))

    body = client.get("/api/v1/ready").json()

    assert body["components"]["auth"] == {"status": "ok", "mode": "oidc"}


def test_ready_reports_auth_disabled_when_no_verifier() -> None:
    settings = Settings(app_env="test")
    client = TestClient(create_app(settings=settings))

    body = client.get("/api/v1/ready").json()

    assert body["components"]["auth"] == {"status": "disabled", "mode": "none"}
    assert body["ready"] is False
