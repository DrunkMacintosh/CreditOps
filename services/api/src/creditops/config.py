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
    database_url: SecretStr | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_storage_tus_url: str | None = None
    supabase_storage_max_upload_bytes: int = 100 * 1024 * 1024
    supabase_storage_intent_ttl_seconds: int = 900

    def model_post_init(self, __context: object) -> None:
        if self.data_class != "synthetic":
            raise ValueError("Only synthetic data is authorized")
        if self.app_env == "production":
            jwks_url = urlsplit(self.oidc_jwks_url or "")
            if self.oidc_jwks_url and (jwks_url.scheme.lower() != "https" or not jwks_url.hostname):
                raise ValueError("OIDC_JWKS_URL must use HTTPS in production")
            required = {
                "DATABASE_URL": self.database_url,
                "OIDC_ISSUER": self.oidc_issuer,
                "OIDC_AUDIENCE": self.oidc_audience,
                "OIDC_JWKS_URL": self.oidc_jwks_url,
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing production configuration: {', '.join(missing)}")
            if jwks_url.scheme.lower() != "https" or not jwks_url.hostname:
                raise ValueError("OIDC_JWKS_URL must use HTTPS in production")
            storage_url = urlsplit(self.supabase_url or "")
            if storage_url.scheme.lower() != "https" or not storage_url.hostname:
                raise ValueError("SUPABASE_URL must use HTTPS in production")
        if self.supabase_storage_max_upload_bytes <= 0:
            raise ValueError("SUPABASE_STORAGE_MAX_UPLOAD_BYTES must be positive")
        if self.supabase_storage_intent_ttl_seconds <= 0:
            raise ValueError("SUPABASE_STORAGE_INTENT_TTL_SECONDS must be positive")
