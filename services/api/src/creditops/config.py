from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["test", "development", "production"] = "development"
    data_class: str = "synthetic"
    service_name: str = "creditops-api"
    log_level: str = "INFO"
    #: Which queue a worker execution serves.  Deliberately unset by default:
    #: a worker without an explicit mode refuses to run (fail closed).
    worker_mode: Literal["document", "agent"] | None = None
    database_url: SecretStr | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_storage_tus_url: str | None = None
    #: Cloud Run worker Job coordinates.  All three must be present before a
    #: dispatcher is composed; otherwise dispatch stays disabled and the
    #: scheduled recovery sweep is the only trigger (fail closed).
    gcp_project_id: str | None = None
    gcp_location: str | None = None
    gcp_worker_job_name: str | None = None
    supabase_storage_max_upload_bytes: int = 100 * 1024 * 1024
    supabase_storage_intent_ttl_seconds: int = 900
    #: Anonymous synthetic demo session (see creditops.api.demo_sessions).  When
    #: enabled with a demo private key, the API mints its own short-TTL RS256
    #: session tokens and validates them locally, so an external OIDC provider is
    #: NOT required.  Disabled by default (fail closed): the endpoint is not even
    #: mounted unless both the flag and the key are present.
    demo_session_enabled: bool = False
    #: PEM-encoded RSA private key (from env DEMO_JWT_PRIVATE_KEY / Secret
    #: Manager).  Never emitted to a client, token, JWKS document, or log.
    demo_jwt_private_key: SecretStr | None = None
    demo_jwt_issuer: str = "https://demo.creditops.local/"
    demo_jwt_audience: str = "creditops-api"
    demo_jwt_kid: str = "creditops-demo-v1"
    demo_session_ttl_seconds: int = 3600
    #: Bounded in-memory token bucket protecting the mint endpoint.  ``burst`` is
    #: the bucket capacity; ``refill_per_second`` the steady-state grant rate.
    demo_session_rate_limit_burst: int = 5
    demo_session_rate_limit_refill_per_second: float = 0.5

    def model_post_init(self, __context: object) -> None:
        if self.data_class != "synthetic":
            raise ValueError("Only synthetic data is authorized")
        # Demo mode replaces the external OIDC provider with a locally-signed,
        # short-TTL issuer; when it is enabled the demo private key is mandatory
        # (fail closed) and the external OIDC_* variables are no longer required.
        demo_mode = self.demo_session_enabled
        if demo_mode and self.demo_jwt_private_key is None:
            raise ValueError("DEMO_JWT_PRIVATE_KEY is required when demo sessions are enabled")
        if self.demo_session_ttl_seconds <= 0:
            raise ValueError("DEMO_SESSION_TTL_SECONDS must be positive")
        if self.demo_session_rate_limit_burst <= 0:
            raise ValueError("DEMO_SESSION_RATE_LIMIT_BURST must be positive")
        if self.demo_session_rate_limit_refill_per_second < 0:
            raise ValueError("DEMO_SESSION_RATE_LIMIT_REFILL_PER_SECOND must not be negative")
        if self.app_env == "production":
            jwks_url = urlsplit(self.oidc_jwks_url or "")
            if self.oidc_jwks_url and (jwks_url.scheme.lower() != "https" or not jwks_url.hostname):
                raise ValueError("OIDC_JWKS_URL must use HTTPS in production")
            required = {
                "DATABASE_URL": self.database_url,
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
            }
            if not demo_mode:
                required.update(
                    {
                        "OIDC_ISSUER": self.oidc_issuer,
                        "OIDC_AUDIENCE": self.oidc_audience,
                        "OIDC_JWKS_URL": self.oidc_jwks_url,
                    }
                )
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing production configuration: {', '.join(missing)}")
            if self.oidc_jwks_url and (jwks_url.scheme.lower() != "https" or not jwks_url.hostname):
                raise ValueError("OIDC_JWKS_URL must use HTTPS in production")
            storage_url = urlsplit(self.supabase_url or "")
            if storage_url.scheme.lower() != "https" or not storage_url.hostname:
                raise ValueError("SUPABASE_URL must use HTTPS in production")
        if self.supabase_storage_max_upload_bytes <= 0:
            raise ValueError("SUPABASE_STORAGE_MAX_UPLOAD_BYTES must be positive")
        if self.supabase_storage_intent_ttl_seconds <= 0:
            raise ValueError("SUPABASE_STORAGE_INTENT_TTL_SECONDS must be positive")
