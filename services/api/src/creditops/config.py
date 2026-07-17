from typing import Literal

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

    def model_post_init(self, __context: object) -> None:
        if self.data_class != "synthetic":
            raise ValueError("Only synthetic data is authorized")
        if self.app_env == "production":
            required = {
                "DATABASE_URL": self.database_url,
                "OIDC_ISSUER": self.oidc_issuer,
                "OIDC_AUDIENCE": self.oidc_audience,
                "OIDC_JWKS_URL": self.oidc_jwks_url,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing production configuration: {', '.join(missing)}")
