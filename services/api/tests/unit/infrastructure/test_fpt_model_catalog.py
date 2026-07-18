from __future__ import annotations

import pytest

from creditops.infrastructure.fpt.benchmark_records import (
    FPT_BENCHMARK_RECORDS,
    FPTBenchmarkRecord,
)
from creditops.infrastructure.fpt.catalog import CapabilityName, FPTCatalog
from creditops.infrastructure.fpt.model_catalog import FPT_MODEL_CATALOG


def _endpoint_env(prefix: str) -> dict[str, str]:
    return {
        f"{prefix}_ENDPOINT_URL": "https://fpt.example.com/v1/reasoning",
        f"{prefix}_ENDPOINT_ID": "endpoint-123",
    }


def _pass_record(
    capability: CapabilityName = "reasoning",
    *,
    model_id: str = "qwen3-benchmark-selected",
    endpoint_id: str = "endpoint-123",
    route_version: str = "fpt-route-v1",
    prompt_version: str = "intake-prompt-v1",
    schema_version: str = "intake-schema-v1",
    passed: bool = True,
) -> FPTBenchmarkRecord:
    return FPTBenchmarkRecord(
        capability=capability,
        model_id=model_id,
        endpoint_id=endpoint_id,
        route_version=route_version,
        prompt_version=prompt_version,
        schema_version=schema_version,
        passed=passed,
        evidence_ref="docs/benchmarks/synthetic-holdout-run.md",
        recorded_on="2026-07-18",
    )


def test_shipped_catalog_pins_the_selected_stack() -> None:
    # reasoning/vision/embedding are pinned by project decision; kie/table stay
    # unpinned (fail closed) and reranking is not a catalog capability.
    assert dict(FPT_MODEL_CATALOG) == {
        "reasoning": "DeepSeek-V4-Flash",
        "vision": "Qwen2.5-VL-72B-Instruct",
        "embedding": "multilingual-e5-large",
    }
    assert "kie" not in FPT_MODEL_CATALOG
    assert "table" not in FPT_MODEL_CATALOG


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
        benchmark_records=(_pass_record(),),
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
        benchmark_records=(_pass_record(),),
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


def test_shipped_benchmark_registry_is_empty_until_a_run_is_recorded() -> None:
    # No representative Vietnamese banking benchmark has been executed yet, so
    # the committed registry must ship empty: every capability route stays
    # DISABLED until a real benchmark-pass record is added in a reviewed change.
    assert FPT_BENCHMARK_RECORDS == ()


def test_route_without_benchmark_pass_record_fails_closed() -> None:
    # Pinned model + endpoint + API key is NOT enough to activate a route: the
    # committed registry (empty by default) must contain a matching pass record.
    with pytest.raises(ValueError, match="benchmark"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
        )


def test_route_with_matching_benchmark_pass_record_activates() -> None:
    catalog = FPTCatalog.from_configuration(
        model_catalog={"reasoning": "qwen3-benchmark-selected"},
        environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
        benchmark_records=(_pass_record(),),
    )
    assert catalog.config_for("reasoning").model_id == "qwen3-benchmark-selected"


def test_benchmark_record_for_another_model_does_not_activate() -> None:
    with pytest.raises(ValueError, match="benchmark"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
            benchmark_records=(_pass_record(model_id="some-other-model"),),
        )


def test_benchmark_record_for_another_endpoint_does_not_activate() -> None:
    with pytest.raises(ValueError, match="benchmark"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
            benchmark_records=(_pass_record(endpoint_id="endpoint-999"),),
        )


def test_benchmark_record_for_other_prompt_or_schema_version_does_not_activate() -> None:
    with pytest.raises(ValueError, match="benchmark"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
            benchmark_records=(_pass_record(prompt_version="intake-prompt-v0"),),
        )
    with pytest.raises(ValueError, match="benchmark"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
            benchmark_records=(_pass_record(schema_version="intake-schema-v0"),),
        )


def test_failed_benchmark_record_does_not_activate() -> None:
    # A recorded FAILED run is history, never activation evidence.
    with pytest.raises(ValueError, match="benchmark"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
            benchmark_records=(_pass_record(passed=False),),
        )


def test_environment_cannot_supply_or_bypass_benchmark_records() -> None:
    # Benchmark evidence is committed code, never runtime configuration: no
    # environment variable may inject a record or bypass the gate.
    with pytest.raises(ValueError, match="benchmark"):
        FPTCatalog.from_configuration(
            model_catalog={"reasoning": "qwen3-benchmark-selected"},
            environ={
                "FPT_API_KEY": "secret-key",
                "FPT_REASONING_BENCHMARK_PASSED": "true",
                "FPT_BENCHMARK_OVERRIDE": "1",
                **_endpoint_env("FPT_REASONING"),
            },
        )


def test_benchmark_evaluation_catalog_is_explicit_and_separate() -> None:
    # The benchmark harness itself must be able to reach a configured endpoint
    # BEFORE any pass record exists -- through an explicitly named evaluation
    # constructor, never through the application path.
    catalog = FPTCatalog.for_benchmark_evaluation(
        model_catalog={"reasoning": "qwen3-benchmark-selected"},
        environ={"FPT_API_KEY": "secret-key", **_endpoint_env("FPT_REASONING")},
    )
    assert catalog.config_for("reasoning").model_id == "qwen3-benchmark-selected"
