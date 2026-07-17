from __future__ import annotations

import re
from pathlib import Path

import pytest

from creditops.config import Settings

ROOT = Path(__file__).resolve().parents[4]
TERRAFORM_ROOT = ROOT / "deploy" / "terraform"


def _terraform() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(TERRAFORM_ROOT.rglob("*.tf"))
    )


def test_production_configuration_fails_closed_without_secrets() -> None:
    with pytest.raises(ValueError, match="Missing production configuration"):
        Settings(app_env="production")


def test_terraform_uses_pinned_secret_versions_without_secret_payloads() -> None:
    terraform = _terraform()

    assert "google_secret_manager_secret_version" not in terraform
    assert 'resource "google_secret_manager_secret"' not in terraform
    assert 'data "google_secret_manager_secret"' in terraform
    assert re.search(r"version\s+=\s+[a-z_]+\.value\.version", terraform)
    assert "secret_data" not in terraform
    assert not re.search(r'version\s*=\s*"latest"', terraform, re.IGNORECASE)


def test_terraform_defines_separate_runtime_identities_and_private_worker() -> None:
    terraform = _terraform()

    for identity in ("api", "worker", "scheduler", "web-invoker"):
        assert f'account_id   = "creditops-{identity}"' in terraform
    assert "roles/run.invoker" in terraform
    assert "allUsers" not in terraform
    assert "google_cloud_run_v2_service_iam_member" in terraform
    assert "google_cloud_run_v2_job_iam_member" in terraform


def test_terraform_provisions_workload_identity_for_web_invoker() -> None:
    terraform = _terraform()

    assert "google_iam_workload_identity_pool" in terraform
    assert "google_iam_workload_identity_pool_provider" in terraform
    assert "roles/iam.workloadIdentityUser" in terraform
    assert "attribute_condition" in terraform
    assert "allowed_audiences" in terraform
    assert "web_identity_provider_name" in terraform
    assert "principal://iam.googleapis.com/" in terraform
    assert "subject/%s" in terraform


def test_worker_and_scheduler_contracts_are_single_task_oauth_calls() -> None:
    terraform = _terraform()

    assert re.search(r"task_count\s*=\s*1", terraform)
    assert re.search(r"parallelism\s*=\s*1", terraform)
    assert re.search(r"max_retries\s*=\s*0", terraform)
    assert "durable worker slot" in terraform.lower()
    assert "oauth_token" in terraform
    assert "https://run.googleapis.com/" in terraform
    assert re.search(r"count\s*=\s*var\.worker_runtime_ready\s*\?\s*1\s*:\s*0", terraform)


def test_worker_secret_access_is_gated_until_runtime_is_ready() -> None:
    terraform = _terraform()

    assert re.search(
        r'for_each\s*=\s*var\.worker_runtime_ready\s*\?\s*var\.worker_secret_ids\s*:\s*toset\(\[\]\)',
        terraform,
    )


def test_unimplemented_operational_metrics_are_deployment_gated_and_scoped() -> None:
    terraform = _terraform()

    assert "operational_metrics_ready" in terraform
    assert re.search(
        r"count\s*=\s*var\.operational_metrics_ready\s*\?\s*1\s*:\s*0",
        terraform,
    )
    for service in ("creditops-api", "creditops-worker"):
        assert f'jsonPayload.service=\\"{service}\\"' in terraform


def test_runtime_secret_names_cannot_override_reserved_environment() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")

    for name in ("api_secret_refs", "worker_secret_refs"):
        block = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables, re.DOTALL)
        assert block is not None
        for reserved in (
            "APP_ENV",
            "DATA_CLASS",
            "SERVICE_NAME",
            "PORT",
            "K_SERVICE",
            "K_REVISION",
            "K_CONFIGURATION",
            "FUNCTION_TARGET",
            "FUNCTION_SIGNATURE_TYPE",
        ):
            assert reserved in block.group(1)
        assert "^[A-Za-z_][A-Za-z0-9_]*$" in block.group(1)


def test_runtime_capacity_and_region_variables_have_no_defaults() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")

    for name in (
        "region",
        "api_cpu",
        "api_memory",
        "api_timeout_seconds",
        "worker_cpu",
        "worker_memory",
        "worker_timeout_seconds",
    ):
        block = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables, re.DOTALL)
        assert block is not None
        assert "default" not in block.group(1)


def test_container_runs_as_non_root_and_has_explicit_modes() -> None:
    dockerfile = (ROOT / "services" / "api" / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"^USER [1-9][0-9]*$", dockerfile, re.MULTILINE)
    assert 'CMD ["api"]' in dockerfile
    assert "python -m creditops.worker.main" in dockerfile
    assert "chmod -R a-w" in dockerfile
