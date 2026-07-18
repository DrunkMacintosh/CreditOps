"""Worker processor for CREDIT_UNDERWRITING tasks.

Registered in the per-task-type ProcessorRegistry exactly like
ORCHESTRATOR_PLAN.  Stages are checkpointed (evidence view -> calculators ->
inference validated -> persisted) so RunWorkerOnce resumes a redelivery from
the latest checkpoint; the persist itself is idempotent on
(case, case version, task), making duplicate delivery harmless.  Fail closed:
with no configured reasoning gateway the task follows the existing durable
failure policy toward FAILED_MANUAL_REVIEW — deterministic calculators alone
never fabricate an assessment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from pydantic import ValidationError

from creditops.application.ports.model_gateway import (
    InferenceError,
    InferenceUnavailableError,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.ports.underwriting import (
    PersistedMakerOutput,
    UnderwritingRepository,
)
from creditops.application.underwriting.evidence import build_calculator_suite
from creditops.application.underwriting.maker import (
    MakerOutputInvalid,
    MakerRunContext,
    RunUnderwritingInference,
    persist_maker_output,
)
from creditops.application.use_cases.run_worker_once import (
    CheckpointCallback,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.underwriting import (
    UNDERWRITING_AGENT_ROLE,
    UnderwritingAssessment,
)

CHECKPOINT_EVIDENCE_VIEW = "EVIDENCE_VIEW_BUILT"
CHECKPOINT_CALCULATORS = "CALCULATORS_COMPUTED"
CHECKPOINT_INFERENCE = "INFERENCE_VALIDATED"
CHECKPOINT_PERSISTED = "ASSESSMENT_PERSISTED"


class CreditUnderwritingProcessor:
    """Run one resumable, idempotent maker execution for a claimed task."""

    def __init__(
        self,
        repository: UnderwritingRepository,
        inference: RunUnderwritingInference | None,
        *,
        clock: Callable[[], datetime] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._inference = inference
        self._clock = clock or (lambda: datetime.now(UTC))
        self._execution_id_factory = execution_id_factory or uuid4

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_PERSISTED:
            # Terminal checkpoint: a prior execution already persisted the
            # assessment, gaps, and handoff.  Redelivery is a no-op.
            return StageResult()

        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_INFERENCE:
            # Resume after a crash between validation and persistence without
            # re-calling the model: the validated assessment is durable state.
            return await self._persist_stage(
                task,
                self._assessment_from_checkpoint(checkpoint),
                save_checkpoint,
            )

        existing = await self._repository.find_persisted(
            case_id=task.case_id,
            case_version=task.case_version,
            task_id=task.id,
        )
        if existing is not None:
            # Durable output exists but the terminal checkpoint was lost (or a
            # duplicate delivery raced): re-record the checkpoint and succeed.
            await self._save_persisted_checkpoint(save_checkpoint, existing)
            return StageResult()

        if self._inference is None:
            # Fail closed per the existing failure policy: no configured FPT
            # reasoning endpoint means NO assessment.  The calculators alone
            # must not fabricate analysis.
            await self._audit(
                task,
                "UNDERWRITING_GATEWAY_UNAVAILABLE",
                {"reason": "no configured FPT reasoning endpoint"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "credit underwriting requires a configured FPT reasoning endpoint",
            )

        view = await self._repository.load_evidence_view(task.case_id)
        if view is None:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                f"case {task.case_id} has no evidence view",
            )
        if view.case_version != task.case_version:
            return StageResult(
                WorkerOutcome.SUPERSEDED,
                "case version advanced past this task's bound version",
            )
        if not view.confirmed_facts:
            # No Confirmed Facts at all: there is nothing an evidence-grounded
            # maker may say.  Surface it; never guess.
            await self._audit(
                task,
                "UNDERWRITING_NO_CONFIRMED_FACTS",
                {"reason": "scoped evidence view is empty"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "evidence view contains no confirmed facts",
            )
        await save_checkpoint(
            CHECKPOINT_EVIDENCE_VIEW,
            {
                "confirmedFactCount": len(view.confirmed_facts),
                "builtAt": view.built_at.isoformat(),
            },
        )

        suite = build_calculator_suite(view)
        await save_checkpoint(
            CHECKPOINT_CALCULATORS,
            {
                "resultIds": list(suite.result_ids()),
                "missingEvidenceCount": len(suite.missing),
            },
        )

        run = MakerRunContext(
            task_id=task.id,
            execution_id=self._execution_id_factory(),
            correlation_id=f"underwriting:{task.id}",
        )
        try:
            assessment = await self._inference.infer(view=view, suite=suite, run=run)
        except InferenceUnavailableError:
            await self._audit(
                task,
                "UNDERWRITING_GATEWAY_UNAVAILABLE",
                {"reason": "FPT reasoning endpoint unavailable"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "FPT reasoning endpoint unavailable",
            )
        except (MakerOutputInvalid, InferenceError, ValidationError) as exc:
            # Invalid maker output is rejected in full and retried under the
            # bounded durable policy; nothing partial is ever persisted.
            await self._audit(
                task,
                "UNDERWRITING_OUTPUT_REJECTED",
                {"reason": str(exc)[:2000]},
            )
            return StageResult(
                WorkerOutcome.RETRY_WAIT, f"maker output rejected: {exc}"
            )

        await save_checkpoint(
            CHECKPOINT_INFERENCE,
            {"assessment": assessment.model_dump(mode="json")},
        )
        return await self._persist_stage(task, assessment, save_checkpoint)

    async def _persist_stage(
        self,
        task: TaskRecord,
        assessment: UnderwritingAssessment,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        persisted = await persist_maker_output(
            self._repository,
            assessment,
            handoff_id=self._handoff_id_for(assessment),
        )
        await self._save_persisted_checkpoint(save_checkpoint, persisted)
        await self._audit(
            task,
            "UNDERWRITING_ASSESSMENT_PERSISTED",
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "gapCount": len(persisted.gap_ids),
                "executionId": str(assessment.provenance.execution_id),
            },
        )
        return StageResult()

    @staticmethod
    def _handoff_id_for(assessment: UnderwritingAssessment) -> UUID:
        # Deterministic per assessment id so a resume after a partial persist
        # cannot mint a second handoff for the same assessment.
        return uuid5(NAMESPACE_OID, f"underwriting-handoff:{assessment.id}")

    @staticmethod
    def _assessment_from_checkpoint(
        checkpoint: TaskCheckpoint,
    ) -> UnderwritingAssessment:
        raw = checkpoint.checkpoint_data.get("assessment")
        if not isinstance(raw, Mapping):
            raise MakerOutputInvalid("inference checkpoint has no assessment payload")
        return UnderwritingAssessment.model_validate(raw)

    async def _save_persisted_checkpoint(
        self,
        save_checkpoint: CheckpointCallback,
        persisted: PersistedMakerOutput,
    ) -> None:
        await save_checkpoint(
            CHECKPOINT_PERSISTED,
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "gapIds": [str(gap_id) for gap_id in persisted.gap_ids],
                "created": persisted.created,
            },
        )

    async def _audit(
        self,
        task: TaskRecord,
        event_type: str,
        event_data: Mapping[str, Any],
    ) -> None:
        await self._repository.append_audit(
            OrchestrationAuditEvent(
                case_id=task.case_id,
                case_version=task.case_version,
                event_type=event_type,
                execution_id=self._execution_id_factory(),
                artifact_type="PROCESSING_TASK",
                artifact_id=task.id,
                event_data={
                    "role": UNDERWRITING_AGENT_ROLE,
                    "recordedAt": self._clock().isoformat(),
                    **dict(event_data),
                },
            )
        )
