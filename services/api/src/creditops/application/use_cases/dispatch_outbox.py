"""Publish committed outbox events to the durable queue.

The transactional outbox (master design section 14.2) closes the dual-write
window: a material command commits domain mutation + outbox event in ONE
transaction, and this dispatcher performs the queue send afterwards.  A
crash between commit and send leaves the event undispatched, where the next
dispatch run (API best-effort call or recovery sweep) picks it up.

Delivery is at-least-once by design: a failure after ``queue.send`` but
before ``mark_outbox_dispatched`` re-sends the same envelope later, and the
consumer's idempotent task claim absorbs the duplicate.  A payload that does
not validate as a task envelope is never sent -- it is counted as failed and
left in place for manual attention (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from creditops.application.ports.orchestration import OrchestrationRepository
from creditops.application.ports.queue import QueueError, QueuePort
from creditops.application.ports.worker_dispatcher import (
    WorkerDispatcher,
    WorkerDispatchError,
)
from creditops.domain.tasks import TaskEnvelopeV1

#: Outbox event types this dispatcher understands.  Anything else fails
#: closed (counted, left undispatched) rather than being guessed at.
TASK_READY_EVENT = "TASK_READY"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    dispatched: int
    failed: int
    #: Whether one Cloud Run worker execution was successfully requested
    #: after the sends.  A request failure never undoes the durable sends —
    #: the Cloud Scheduler recovery sweep covers delivery.
    worker_dispatch_requested: bool = False


class DispatchOutbox:
    def __init__(
        self,
        repository: OrchestrationRepository,
        queue: QueuePort,
        *,
        batch_limit: int = 32,
        worker_dispatcher: WorkerDispatcher | None = None,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._batch_limit = batch_limit
        self._worker_dispatcher = worker_dispatcher

    async def run(self) -> DispatchResult:
        events = await self._repository.load_undispatched_outbox(limit=self._batch_limit)
        dispatched = 0
        failed = 0
        for event in events:
            if event.event_type != TASK_READY_EVENT:
                failed += 1
                await self._repository.record_outbox_dispatch_failure(event.event_id)
                continue
            try:
                envelope = TaskEnvelopeV1.model_validate(dict(event.payload))
            except ValidationError:
                failed += 1
                await self._repository.record_outbox_dispatch_failure(event.event_id)
                continue
            try:
                await self._queue.send(envelope)
            except QueueError:
                failed += 1
                await self._repository.record_outbox_dispatch_failure(event.event_id)
                continue
            await self._repository.mark_outbox_dispatched(event.event_id)
            dispatched += 1
        worker_requested = False
        if dispatched > 0 and self._worker_dispatcher is not None:
            try:
                outcome = await self._worker_dispatcher.request_execution()
                worker_requested = outcome.accepted
            except WorkerDispatchError:
                # The sends are durable and the messages are queued; the
                # scheduled recovery sweep will run them.  Never retried
                # blindly here.
                worker_requested = False
        return DispatchResult(
            dispatched=dispatched,
            failed=failed,
            worker_dispatch_requested=worker_requested,
        )
