"""Worker composition root (master design P0 #1).

``build_runtime`` must construct the REAL injected dependencies from
configuration: Postgres task repository, the mode's queue, and the full
processor registry with a fail-closed manual-review fallback.  Incomplete
configuration still refuses to run (no synthetic processors, no preloaded
results) and an unbenchmarked FPT route stays DISABLED — the specialist
processors then fail closed per task instead of fabricating analysis.
"""

from __future__ import annotations

from pydantic import SecretStr

from creditops.application.credit_ops.processor import CreditOperationsProcessor
from creditops.application.legal.processor import LegalComplianceProcessor
from creditops.application.orchestration.processors import OrchestratorPlanProcessor
from creditops.application.risk_review.processor import IndependentRiskReviewProcessor
from creditops.application.underwriting.processor import CreditUnderwritingProcessor
from creditops.config import Settings
from creditops.domain.orchestration import TaskType
from creditops.infrastructure.supabase.queue import (
    AGENT_TASK_QUEUE_NAME,
    DOCUMENT_TASK_QUEUE_NAME,
)
from creditops.worker.main import build_runtime

_DB_URL = "postgresql://creditops:secret@localhost:5432/creditops"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "database_url": SecretStr(_DB_URL),
        "worker_mode": "agent",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_without_a_database_url_there_is_no_runtime() -> None:
    assert build_runtime(Settings(app_env="test", worker_mode="agent")) is None


def test_without_an_explicit_worker_mode_there_is_no_runtime() -> None:
    assert build_runtime(Settings(app_env="test", database_url=SecretStr(_DB_URL))) is None


def test_agent_mode_builds_the_full_specialist_registry() -> None:
    runtime = build_runtime(_settings())

    assert runtime is not None
    assert runtime.queue.queue_name == AGENT_TASK_QUEUE_NAME
    registry = runtime.registry
    assert isinstance(
        registry.processor_for(TaskType.ORCHESTRATOR_PLAN), OrchestratorPlanProcessor
    )
    assert isinstance(
        registry.processor_for(TaskType.CREDIT_UNDERWRITING), CreditUnderwritingProcessor
    )
    assert isinstance(
        registry.processor_for(TaskType.LEGAL_COMPLIANCE_COLLATERAL),
        LegalComplianceProcessor,
    )
    assert isinstance(
        registry.processor_for(TaskType.INDEPENDENT_RISK_REVIEW),
        IndependentRiskReviewProcessor,
    )
    assert isinstance(
        registry.processor_for(TaskType.CREDIT_OPERATIONS), CreditOperationsProcessor
    )


def test_document_mode_reads_the_document_queue() -> None:
    runtime = build_runtime(_settings(worker_mode="document"))

    assert runtime is not None
    assert runtime.queue.queue_name == DOCUMENT_TASK_QUEUE_NAME


def test_unbenchmarked_fpt_route_leaves_inference_disabled_not_crashed() -> None:
    # A configured endpoint with no committed benchmark-pass record must not
    # crash the worker: the route stays DISABLED and specialists fail closed
    # per task (no fabricated analysis, no hidden fallback).
    runtime = build_runtime(
        _settings(),
        environ={
            "FPT_API_KEY": "secret-key",
            "FPT_REASONING_ENDPOINT_URL": "https://fpt.example.com/v1/reasoning",
            "FPT_REASONING_ENDPOINT_ID": "endpoint-123",
        },
    )

    assert runtime is not None
    assert runtime.inference_enabled is False
