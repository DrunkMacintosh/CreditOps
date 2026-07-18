"""Worker composition root (master design P0 #1, section 14.3).

``build_runtime`` constructs the REAL injected dependencies from
configuration: the Postgres task repository, the configured mode's queue,
and the full per-task-type processor registry with a fail-closed
manual-review fallback.  There is intentionally no synthetic processor and
no preloaded result:

- no ``DATABASE_URL`` or no explicit ``WORKER_MODE`` -> no runtime; the
  entry point refuses to run (exit 78) exactly as before;
- no benchmark-passed FPT route -> inference stays DISABLED and every
  specialist processor fails closed per task (no fabricated analysis, no
  hidden provider fallback);
- a task type with no registered processor -> ``ManualReviewProcessor``
  (FAILED_MANUAL_REVIEW with an audit event), never a crash and never a
  fake success.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from creditops.application.credit_ops.assembler import RunMemoInference
from creditops.application.credit_ops.processor import CreditOperationsProcessor
from creditops.application.legal.processor import LegalComplianceProcessor
from creditops.application.legal.reviewer import RunLegalInference
from creditops.application.orchestration.advance import AdvanceCase
from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.planner import OrchestrationPlanner
from creditops.application.orchestration.processors import (
    ManualReviewProcessor,
    OrchestratorPlanProcessor,
    ProcessorRegistry,
)
from creditops.application.ports.queue import QueuePort, TaskRepository
from creditops.application.risk_review.checker import RunCheckerInference
from creditops.application.risk_review.processor import IndependentRiskReviewProcessor
from creditops.application.stages.ingestion_processor import DocumentIngestionProcessor
from creditops.application.underwriting.maker import RunUnderwritingInference
from creditops.application.underwriting.processor import CreditUnderwritingProcessor
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.application.use_cases.run_worker_once import (
    RunWorkerOnce,
    TaskProcessor,
    TaskProcessorRegistry,
    WorkerOutcome,
    WorkerRunResult,
)
from creditops.config import Settings
from creditops.domain.orchestration import TaskType
from creditops.infrastructure.fpt.catalog import FPTCatalog
from creditops.infrastructure.fpt.client import FPTClient
from creditops.infrastructure.fpt.gateway import FPTInferenceGateway
from creditops.infrastructure.mock.legal_checks import MockControlledChecksGateway
from creditops.infrastructure.postgres.credit_ops import PostgresCreditOpsRepository
from creditops.infrastructure.postgres.document_ingestion import (
    PostgresDocumentIngestionRepository,
)
from creditops.infrastructure.postgres.legal import PostgresLegalRepository
from creditops.infrastructure.postgres.orchestration import (
    PostgresOrchestrationRepository,
)
from creditops.infrastructure.postgres.risk_review import PostgresRiskReviewRepository
from creditops.infrastructure.postgres.session import PsycopgConnectionFactory
from creditops.infrastructure.postgres.tasks import PostgresTaskRepository
from creditops.infrastructure.postgres.underwriting import (
    PostgresUnderwritingRepository,
)
from creditops.infrastructure.supabase.queue import (
    AGENT_TASK_QUEUE_NAME,
    SupabaseQueue,
)
from creditops.infrastructure.supabase.storage import SupabaseStorage
from creditops.observability import configure_structured_logging, log_event

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """The fully constructed dependencies for one worker execution."""

    tasks: PostgresTaskRepository
    queue: SupabaseQueue
    registry: ProcessorRegistry
    orchestration: PostgresOrchestrationRepository
    agent_queue: SupabaseQueue
    #: Whether a benchmark-passed FPT route was activated for this runtime.
    inference_enabled: bool


def _build_gateway(
    environ: Mapping[str, str] | None,
) -> FPTInferenceGateway | None:
    """Activate FPT inference ONLY through the benchmark-gated catalog.

    Any configuration problem — including a configured endpoint with no
    committed benchmark-pass record — leaves inference DISABLED (specialists
    then fail closed per task).  The reason is logged, never silent.
    """

    try:
        catalog = FPTCatalog.from_configuration(environ=environ)
    except ValueError as exc:
        log_event(
            _logger,
            logging.WARNING,
            "FPT inference disabled; specialist processors fail closed per task",
            {"event": "fpt_routes_disabled", "reason": str(exc)},
        )
        return None
    if not catalog.capabilities:
        return None
    return FPTInferenceGateway(catalog, FPTClient(catalog))


def build_runtime(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> WorkerRuntime | None:
    """Construct real dependencies from configuration, or ``None`` to refuse."""

    if settings.database_url is None or settings.worker_mode is None:
        return None

    connection_factory = PsycopgConnectionFactory(
        settings.database_url.get_secret_value()
    )
    tasks = PostgresTaskRepository(connection_factory)
    orchestration = PostgresOrchestrationRepository(connection_factory)
    agent_queue = SupabaseQueue(connection_factory, queue_name=AGENT_TASK_QUEUE_NAME)
    document_queue = SupabaseQueue(connection_factory)

    gateway = _build_gateway(os.environ if environ is None else environ)
    reasoning: FPTInferenceGateway | None = gateway

    advance = AdvanceCase(
        orchestration,
        OrchestrationPlanner(DependencyTemplate.canonical(), gateway=reasoning),
    )
    processors: dict[TaskType, TaskProcessor] = {
        TaskType.ORCHESTRATOR_PLAN: OrchestratorPlanProcessor(
            advance, dispatch=DispatchOutbox(orchestration, agent_queue)
        ),
        # DOCUMENT_INGESTION requires BOTH private storage and a
        # benchmark-passed inference route; anything less resolves to the
        # manual-review fallback (fail closed, never a partial pipeline).
        **(
            {
                TaskType.DOCUMENT_INGESTION: DocumentIngestionProcessor(
                    port=PostgresDocumentIngestionRepository(connection_factory),
                    storage=SupabaseStorage(settings),
                    gateway=reasoning,
                )
            }
            if reasoning is not None
            and settings.supabase_url
            and settings.supabase_service_role_key
            else {}
        ),
        TaskType.CREDIT_UNDERWRITING: CreditUnderwritingProcessor(
            PostgresUnderwritingRepository(connection_factory),
            RunUnderwritingInference(reasoning) if reasoning is not None else None,
        ),
        TaskType.LEGAL_COMPLIANCE_COLLATERAL: LegalComplianceProcessor(
            PostgresLegalRepository(connection_factory),
            RunLegalInference(reasoning) if reasoning is not None else None,
            # Labelled synthetic mock: no real KYC/AML/registry integration
            # exists or is authorized in the current scope.
            controlled_checks_gateway=MockControlledChecksGateway(),
        ),
        TaskType.INDEPENDENT_RISK_REVIEW: IndependentRiskReviewProcessor(
            PostgresRiskReviewRepository(connection_factory),
            RunCheckerInference(reasoning) if reasoning is not None else None,
        ),
        TaskType.CREDIT_OPERATIONS: CreditOperationsProcessor(
            PostgresCreditOpsRepository(connection_factory),
            RunMemoInference(reasoning) if reasoning is not None else None,
        ),
    }
    registry = ProcessorRegistry(
        processors,
        fallback=ManualReviewProcessor(orchestration),
    )
    queue = document_queue if settings.worker_mode == "document" else agent_queue
    return WorkerRuntime(
        tasks=tasks,
        queue=queue,
        registry=registry,
        orchestration=orchestration,
        agent_queue=agent_queue,
        inference_enabled=reasoning is not None,
    )


async def maybe_retick_after_success(
    result: WorkerRunResult,
    orchestration: object | None,
    agent_queue: QueuePort | None,
) -> None:
    """Self-fire an idempotent orchestration tick after a task success.

    A succeeding ORCHESTRATOR_PLAN task never re-ticks (it IS the tick —
    re-ticking would chain plan tasks forever) and non-success outcomes never
    re-tick.  The plan task + outbox event commit durably; the queue publish
    is best-effort and a failure is logged, never silent.
    """

    from typing import cast

    from creditops.application.orchestration.kickoff import KickoffOrchestration
    from creditops.application.ports.orchestration import OrchestrationRepository

    if (
        orchestration is None
        or result.outcome is not WorkerOutcome.SUCCEEDED
        or result.case_id is None
        or result.task_type is None
        or result.task_type is TaskType.ORCHESTRATOR_PLAN
    ):
        return
    repository = cast(OrchestrationRepository, orchestration)
    try:
        await KickoffOrchestration(repository).execute(
            result.case_id, trigger_ref=f"TASK:{result.task_id}"
        )
        if agent_queue is not None:
            await DispatchOutbox(repository, agent_queue).run()
    except Exception:
        log_event(
            _logger,
            logging.ERROR,
            "Post-success orchestration retick failed; the task result is "
            "durable and the case can be advanced manually",
            {"event": "orchestration_retick_failed", "taskId": str(result.task_id)},
        )


async def run_once(
    *,
    tasks: TaskRepository,
    queue: QueuePort,
    processor: TaskProcessor | TaskProcessorRegistry,
    orchestration: object | None = None,
    agent_queue: QueuePort | None = None,
) -> WorkerRunResult:
    """Run one real injected worker execution.

    There is intentionally no synthetic processor or preloaded result.
    ``processor`` may be a single processor (legacy) or a per-task-type
    registry.  When an orchestration repository is supplied, a task success
    re-ticks the case (master design section 9).
    """
    result = await RunWorkerOnce(tasks, queue, processor).run_once()
    await maybe_retick_after_success(result, orchestration, agent_queue)
    return result


def main(
    *,
    tasks: TaskRepository | None = None,
    queue: QueuePort | None = None,
    processor: TaskProcessor | TaskProcessorRegistry | None = None,
) -> None:
    settings = Settings()
    configure_structured_logging(
        service_name=settings.service_name,
        level=settings.log_level,
    )
    orchestration: object | None = None
    agent_queue: QueuePort | None = None
    if tasks is None and queue is None and processor is None:
        runtime = build_runtime(settings)
        if runtime is not None:
            log_event(
                _logger,
                logging.INFO,
                "Worker runtime composed from configuration",
                {
                    "event": "worker_runtime_composed",
                    "mode": settings.worker_mode,
                    "queue": runtime.queue.queue_name,
                    "inferenceEnabled": runtime.inference_enabled,
                },
            )
            tasks, queue, processor = runtime.tasks, runtime.queue, runtime.registry
            orchestration = runtime.orchestration
            agent_queue = runtime.agent_queue

    if tasks is not None and queue is not None and processor is not None:
        result = asyncio.run(
            run_once(
                tasks=tasks,
                queue=queue,
                processor=processor,
                orchestration=orchestration,
                agent_queue=agent_queue,
            )
        )
        if result.outcome in {
            WorkerOutcome.SUCCEEDED,
            WorkerOutcome.SUPERSEDED,
            WorkerOutcome.NO_MESSAGE,
            WorkerOutcome.NO_SLOT,
            WorkerOutcome.RETRY_WAIT,
            WorkerOutcome.FAILED_MANUAL_REVIEW,
        }:
            return
        raise SystemExit(1)

    log_event(
        _logger,
        logging.CRITICAL,
        "Worker execution refused because the processing runtime is not configured",
        {"event": "worker_runtime_not_ready"},
    )
    raise SystemExit(78)


if __name__ == "__main__":
    main()
