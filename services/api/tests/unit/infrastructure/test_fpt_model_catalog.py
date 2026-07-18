from __future__ import annotations

import pytest

from creditops.infrastructure.fpt.catalog import FPTCatalog
from creditops.infrastructure.fpt.model_catalog import FPT_MODEL_CATALOG


def _endpoint_env(prefix: str) -> dict[str, str]:
    return {
        f"{prefix}_ENDPOINT_URL": "https://fpt.example.com/v1/reasoning",
        f"{prefix}_ENDPOINT_ID": "endpoint-123",
    }


def test_shipped_catalog_pins_no_model_yet() -> None:
    # Model IDs are benchmark-gated OPEN QUESTIONS; the shipped catalog stays
    # empty so every capability fails closed until a model is chosen via PR.
    assert dict(FPT_MODEL_CATALOG) == {}


def test_only_api_key_configures_no_capability() -> None:
    catalog = FPTCatalog.from_configuration(
        model_catalog={},
        environ={"FPT_API_KEY": "secret-key"},
    )
    assert dict(catalog.capabilities) == {}
    with pytest.raises(ValueError):
        catalog.config_for("reasoning")


def test_model_comes_from_code_endpoint_from_env() -> None:
    catalog = FPTCatalog.from_configuration(
        model_catalog={"reasoning": "qwen3-benchmark-selected"},
        environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
    )
    config = catalog.config_for("reasoning")
    assert config.model_id == "qwen3-benchmark-selected"
    assert config.endpoint_id == "endpoint-123"
    assert config.endpoint_url == "https://fpt.example.com/v1/reasoning"
    assert config.api_key.get_secret_value() == "secret-key"


def test_environment_cannot_override_the_code_model() -> None:
    with pytest.raises(ValueError, match="pinned in code"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={
                "FPT_API_KEY": "secret-key",
                "FPT_REASONING_MODEL_ID": "some-other-model",
                **_endpoint_env("FPT_REASONING"),
            },
        )


def test_environment_may_restate_the_same_code_model() -> None:
    catalog = FPTCatalog.from_configuration(
        model_catalog={"reasoning": "qwen3-benchmark-selected"},
        environ={
            "FPT_API_KEY": "secret-key",
            "FPT_REASONING_MODEL_ID": "qwen3-benchmark-selected",
            **_endpoint_env("FPT_REASONING"),
        },
    )
    assert catalog.config_for("reasoning").model_id == "qwen3-benchmark-selected"


def test_pinned_model_without_endpoint_fails_closed() -> None:
    with pytest.raises(ValueError, match="incomplete FPT reasoning"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={"FPT_API_KEY": "secret-key"},
        )


def test_pinned_model_without_api_key_fails_closed() -> None:
    with pytest.raises(ValueError, match="incomplete FPT reasoning"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ=_endpoint_env("FPT_REASONING"),
        )


def test_endpoint_without_a_code_pinned_model_fails_closed() -> None:
    # An endpoint configured in the environment for a capability that has no
    # model pinned in code must never silently activate; the model is the
    # committed authority.
    with pytest.raises(ValueError, match="no model is pinned in code"):
        FPTCatalog.from_configuration(
            model_catalog={},
            environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_KIE")},
        )
