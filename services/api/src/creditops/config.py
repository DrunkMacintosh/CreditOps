from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["test", "development", "production"] = "development"
    data_class: str = "synthetic"
    service_name: str = "creditops-api"
    log_level: str = "INFO"

    def model_post_init(self, __context: object) -> None:
        if self.data_class != "synthetic":
            raise ValueError("Only synthetic data is authorized")
