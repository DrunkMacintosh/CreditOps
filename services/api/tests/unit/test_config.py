import pytest

from creditops.config import Settings


def test_non_synthetic_data_class_is_rejected() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        Settings(app_env="development", data_class="customer")


def test_database_credentials_are_redacted_from_settings_repr() -> None:
    settings = Settings(database_url="postgresql://user:secret-password@database.test/db")

    assert "secret-password" not in repr(settings)
