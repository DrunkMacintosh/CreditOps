"""Transactional-outbox dispatch (master design section 14.2).

A material command commits domain mutation + outbox event atomically; the
queue send happens ONLY afterwards, from the outbox, so a crash between
commit and send can never strand invisible work.  Delivery is at-least-once:
a failure after send leaves the row undispatched and the consumer's
idempotent claim absorbs the duplicate.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from test_advance import CASE_ID, FakeOrchestrationRepository, RecordingQueue

from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.ports.queue import QueueError
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.orchestration import TaskType


class FailingQueue(RecordingQueue):
    def __init__(self, *, fail_first: int) -> None:
        super().__init__()
        self._failures_left = fail_first

    async def send(self, envelope, *, delay_seconds: int = 0):  # type: ignore[no-untyped-def]
        if self._failures_left > 0:
            self._failures_left -= 1
            raise QueueError("queue unavailable")
        return await super().send(envelope, delay_seconds=delay_seconds)


@pytest.mark.asyncio
async def test_kickoff_commits_task_and_outbox_without_touching_the_queue() -> None:
    repository = FakeOrchestrationRepository()
    kickoff = KickoffOrchestration(repository)

    result = await kickoff.execute(CASE_ID)

    assert result.created is True
    assert len(repository.outbox) == 1
    event = repository.outbox[0]
    assert event.event_type == "TASK_READY"
    assert event.payload["task_id"] == str(result.task_id)
    assert event.dispatched_at is None


@pytest.mark.asyncio
async def test_dispatch_sends_each_undispatched_event_exactly_once() -> None:
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()
    await KickoffOrchestration(repository).execute(CASE_ID)

    first = await DispatchOutbox(repository, queue).run()
    second = await DispatchOutbox(repository, queue).run()

    assert first.dispatched == 1
    assert second.dispatched == 0
    assert len(queue.sent) == 1
    assert queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN
    assert repository.outbox[0].dispatched_at is not None


@pytest.mark.asyncio
async def test_queue_failure_leaves_the_event_undispatched_for_the_sweep() -> None:
    repository = FakeOrchestrationRepository()
    queue = FailingQueue(fail_first=1)
    await KickoffOrchestration(repository).execute(CASE_ID)

    failed = await DispatchOutbox(repository, queue).run()
    assert failed.dispatched == 0
    assert failed.failed == 1
    assert repository.outbox[0].dispatched_at is None
    assert repository.outbox[0].dispatch_attempts == 1

    recovered = await DispatchOutbox(repository, queue).run()
    assert recovered.dispatched == 1
    assert len(queue.sent) == 1
    assert repository.outbox[0].dispatched_at is not None


@pytest.mark.asyncio
async def test_duplicate_kickoff_creates_no_second_outbox_event() -> None:
    repository = FakeOrchestrationRepository()
    kickoff = KickoffOrchestration(repository)

    await kickoff.execute(CASE_ID)
    await kickoff.execute(CASE_ID)

    assert len(repository.outbox) == 1


@pytest.mark.asyncio
async def test_dispatch_ignores_foreign_payloads_fail_closed() -> None:
    # An outbox row whose payload does not validate as a task envelope is
    # counted as failed and left in place for manual attention -- never sent.
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()
    repository.append_outbox_for_test(
        event_id=uuid4(),
        event_type="TASK_READY",
        payload={"schema_version": "1", "nonsense": True},
    )

    result = await DispatchOutbox(repository, queue).run()

    assert result.dispatched == 0
    assert result.failed == 1
    assert queue.sent == []
