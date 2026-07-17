import pytest

from creditops.config import Settings


def test_non_synthetic_data_class_is_rejected() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        Settings(app_env="development", data_class="customer")
