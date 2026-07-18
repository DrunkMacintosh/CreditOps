from __future__ import annotations

import asyncio
import logging

from creditops.application.ports.queue import QueuePort, TaskRepository
from creditops.application.use_cases.run_worker_once import (
    RunWorkerOnce,
    TaskProcessor,
    TaskProcessorRegistry,
    WorkerOutcome,
    WorkerRunResult,
)
from creditops.config import Settings
from creditops.observability import configure_structured_logging, log_event


async def run_once(
    *,
    tasks: TaskRepository,
    queue: QueuePort,
    processor: TaskProcessor | TaskProcessorRegistry,
) -> WorkerRunResult:
    """Run one real injected worker execution.

    There is intentionally no synthetic processor or preloaded result.  Cloud
    Run wiring must supply the Storage/FPT-backed processor registry before this
    entry point can be enabled outside tests.  ``processor`` may be a single
    processor (legacy) or a per-task-type registry.
    """
    return await RunWorkerOnce(tasks, queue, processor).run_once()


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
    if tasks is not None and queue is not None and processor is not None:
        result = asyncio.run(run_once(tasks=tasks, queue=queue, processor=processor))
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
        logging.getLogger(__name__),
        logging.CRITICAL,
        "Worker execution refused because the processing runtime is not implemented",
        {"event": "worker_runtime_not_ready"},
    )
    raise SystemExit(78)


if __name__ == "__main__":
    main()
