"""Worker processor for LEGAL_COMPLIANCE_COLLATERAL tasks.

Registered in the per-task-type ProcessorRegistry exactly like
CREDIT_UNDERWRITING.  Stages are checkpointed (evidence view -> policy corpus
-> controlled checks -> inference validated -> persisted) so a redelivery
resumes from the latest checkpoint; the persist itself is idempotent on
(case, case version, task), making duplicate delivery harmless.

Fail closed, per the two distinct policies this agent must satisfy:

- No configured reasoning gateway -> FAILED_MANUAL_REVIEW.  Deterministic
  pre-analysis alone must never fabricate a legal/compliance assessment.
- No configured policy corpus -> NOT a task failure.  The task still
  produces a valid assessment; ``policy_review`` is structurally forced
  empty (see ``reviewer.build_response_schema``) and a CONDITIONAL evidence
  gap records the abstention, per ADR-0002.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from pydantic import ValidationError

from creditops.application.governance import governance_for, manifest_from_governance
from creditops.application.legal.controlled_checks import (
    ControlledCheckSuite,
    run_controlled_checks,
)
from creditops.application.legal.corpus import PolicyCorpus, try_load_default_corpus
from creditops.application.legal.evidence import (
    check_ownership_consistency,
    evaluate_collateral_checklist,
)
from creditops.application.legal.reviewer import (
    ReviewerOutputInvalid,
    ReviewerRunContext,
    RunLegalInference,
    controlled_check_results_from_assessment,
    persist_reviewer_output,
)
from creditops.application.ports.governance import GovernanceRepository
from creditops.application.ports.legal import (
    ControlledChecksGateway,
    LegalEvidenceView,
    LegalRepository,
    PersistedLegalOutput,
)
from creditops.application.ports.model_gateway import (
    InferenceError,
    InferenceUnavailableError,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.use_cases.run_worker_once import (
    CheckpointCallback,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.goal_contracts import UNIVERSAL_PROHIBITED_ACTIONS
from creditops.domain.legal import LEGAL_AGENT_ROLE, LegalComplianceAssessment
from creditops.domain.orchestration import TaskType
from creditops.infrastructure.mock.legal_checks import MockControlledChecksGateway

CHECKPOINT_EVIDENCE_VIEW = "EVIDENCE_VIEW_BUILT"
CHECKPOINT_CORPUS = "CORPUS_LOADED"
CHECKPOINT_CONTROLLED_CHECKS = "CONTROLLED_CHECKS_RUN"
CHECKPOINT_INFERENCE = "INFERENCE_VALIDATED"
CHECKPOINT_PERSISTED = "ASSESSMENT_PERSISTED"

#: The service identity under which the worker acts when it builds a context
#: manifest (see the underwriting processor for the rationale).
_SERVICE_IDENTITY = "service:agent-worker"

#: Fixed deterministic query covering every synthetic policy topic — retrieval
#: stays deterministic and independent of free-text case content.
_RETRIEVAL_QUERY_VI = (
    "thẩm quyền ký kết người đại diện ủy quyền tài sản bảo đảm giấy chứng "
    "nhận quyền sử dụng đất thế chấp định giá kyc hồ sơ pháp lý doanh nghiệp "
    "giao dịch bên liên quan tình trạng pháp lý"
)


class LegalComplianceProcessor:
    """Run one resumable, idempotent reviewer execution for a claimed task."""

    def __init__(
        self,
        repository: LegalRepository,
        inference: RunLegalInference | None,
        *,
        governance: GovernanceRepository | None = None,
        controlled_checks_gateway: ControlledChecksGateway | None = None,
        clock: Callable[[], datetime] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
        corpus_loader: Callable[[], PolicyCorpus | None] = try_load_default_corpus,
    ) -> None:
        self._repository = repository
        self._inference = inference
        self._governance = governance
        # The committed goal contract bounding this task type (fetched
        # unconditionally so the universal human-only bans are enforced at
        # construction; see the underwriting processor).
        self._governance_profile = governance_for(
            TaskType.LEGAL_COMPLIANCE_COLLATERAL
        )
        assert UNIVERSAL_PROHIBITED_ACTIONS.issubset(
            self._governance_profile.goal_contract.prohibited_actions
        ), "legal goal contract must restate every universal prohibited action"
        # The only permitted controlled-check adapter is the clearly-labelled
        # mock — this project never wires a real compliance provider.
        self._controlled_checks_gateway = (
            controlled_checks_gateway or MockControlledChecksGateway()
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._execution_id_factory = execution_id_factory or uuid4
        self._corpus_loader = corpus_loader

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_PERSISTED:
            # Terminal checkpoint: a prior execution already persisted the
            # assessment, gaps, controlled-check records, and handoff.
            return StageResult()

        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_INFERENCE:
            # Resume after a crash between validation and persistence without
            # re-calling the model or re-running controlled checks: the
            # validated assessment is durable state.
            assessment = self._assessment_from_checkpoint(checkpoint)
            return await self._persist_stage(
                task,
                assessment,
                controlled_check_results_from_assessment(assessment),
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
            # Fail closed: no configured FPT reasoning endpoint means NO
            # assessment.  Deterministic pre-analysis alone must not
            # fabricate a legal/compliance conclusion.
            await self._audit(
                task,
                "LEGAL_GATEWAY_UNAVAILABLE",
                {"reason": "no configured FPT reasoning endpoint"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "legal/compliance/collateral review requires a configured "
                "FPT reasoning endpoint",
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
            await self._audit(
                task,
                "LEGAL_NO_CONFIRMED_FACTS",
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
                "documentCount": len(view.documents),
                "builtAt": view.built_at.isoformat(),
            },
        )

        corpus = self._corpus_loader()
        policy_hits = (
            corpus.retrieve_relevant(_RETRIEVAL_QUERY_VI, top_k_per_document=2)
            if corpus is not None
            else ()
        )
        if corpus is None:
            # Abstention, per ADR-0002 — NOT a task failure.
            await self._audit(
                task,
                "LEGAL_POLICY_CORPUS_UNAVAILABLE",
                {"reason": "no configured/checksum-valid policy corpus (ADR-0002)"},
            )
        await save_checkpoint(
            CHECKPOINT_CORPUS,
            {
                "corpusLoaded": corpus is not None,
                "corpusId": corpus.corpus_id if corpus is not None else None,
                "corpusVersion": corpus.version if corpus is not None else None,
                "hitCount": len(policy_hits),
            },
        )

        as_of = self._clock().date()
        collateral_pre_analysis = evaluate_collateral_checklist(view, as_of=as_of)
        ownership_inconsistencies = check_ownership_consistency(view)

        run = ReviewerRunContext(
            task_id=task.id,
            execution_id=self._execution_id_factory(),
            correlation_id=f"legal:{task.id}",
        )
        controlled_checks = await run_controlled_checks(
            self._controlled_checks_gateway, view, correlation_id=run.correlation_id
        )
        await save_checkpoint(
            CHECKPOINT_CONTROLLED_CHECKS,
            {
                "invocationIds": list(controlled_checks.invocation_ids()),
                "missingCount": len(controlled_checks.missing),
            },
        )

        # Governance: snapshot exactly what this reviewer call is authorized to
        # see (confirmed facts, in-scope documents, controlled-check results and
        # the policy-corpus version) BEFORE inference; idempotent per
        # (task, contextHash) and reached only on the fresh pre-inference path.
        await self._persist_context_manifest(task, view, controlled_checks, corpus)

        try:
            assessment = await self._inference.infer(
                view=view,
                policy_hits=policy_hits,
                controlled_checks=controlled_checks,
                collateral_pre_analysis=collateral_pre_analysis,
                ownership_inconsistencies=ownership_inconsistencies,
                corpus=corpus,
                run=run,
            )
        except InferenceUnavailableError:
            await self._audit(
                task,
                "LEGAL_GATEWAY_UNAVAILABLE",
                {"reason": "FPT reasoning endpoint unavailable"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "FPT reasoning endpoint unavailable",
            )
        except (ReviewerOutputInvalid, InferenceError, ValidationError) as exc:
            # Invalid reviewer output is rejected in full and retried under
            # the bounded durable policy; nothing partial is ever persisted.
            await self._audit(
                task,
                "LEGAL_OUTPUT_REJECTED",
                {"reason": str(exc)[:2000]},
            )
            return StageResult(
                WorkerOutcome.RETRY_WAIT, f"reviewer output rejected: {exc}"
            )

        await save_checkpoint(
            CHECKPOINT_INFERENCE,
            {"assessment": assessment.model_dump(mode="json")},
        )
        return await self._persist_stage(
            task, assessment, controlled_checks.results, save_checkpoint
        )

    async def _persist_stage(
        self,
        task: TaskRecord,
        assessment: LegalComplianceAssessment,
        controlled_check_results: tuple[Any, ...],
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        persisted = await persist_reviewer_output(
            self._repository,
            assessment,
            controlled_check_results,
            handoff_id=self._handoff_id_for(assessment),
        )
        await self._save_persisted_checkpoint(save_checkpoint, persisted)
        await self._audit(
            task,
            "LEGAL_ASSESSMENT_PERSISTED",
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "gapCount": len(persisted.gap_ids),
                "controlledCheckCount": len(persisted.controlled_check_record_ids),
                "executionId": str(assessment.provenance.execution_id),
            },
        )
        return StageResult()

    async def _persist_context_manifest(
        self,
        task: TaskRecord,
        view: LegalEvidenceView,
        controlled_checks: ControlledCheckSuite,
        corpus: PolicyCorpus | None,
    ) -> None:
        """Build and persist this reviewer call's context manifest (no-op
        without a wired governance repository)."""

        if self._governance is None:
            return
        tool_versions = {
            result.tool_name: result.tool_version
            for result in controlled_checks.results
        }
        if corpus is not None:
            tool_versions["policy_corpus"] = corpus.version
        manifest = manifest_from_governance(
            self._governance_profile,
            case_id=task.case_id,
            case_version=task.case_version,
            task_id=task.id,
            actor_or_service_identity=_SERVICE_IDENTITY,
            case_roles=(LEGAL_AGENT_ROLE,),
            tool_versions=tool_versions,
            authoritative_fact_refs=tuple(
                fact.confirmed_fact_id for fact in view.confirmed_facts
            ),
            upstream_artifact_refs=tuple(
                document.document_version_id for document in view.documents
            ),
            tool_result_refs=tuple(
                result.invocation_id for result in controlled_checks.results
            ),
        )
        persisted = await self._governance.persist_manifest(manifest)
        await self._audit(
            task,
            "LEGAL_CONTEXT_MANIFEST_PERSISTED",
            {
                "contextManifestId": str(persisted.manifest_id),
                "contextHash": persisted.context_hash,
                "goalContractKey": self._governance_profile.contract_key,
                "goalContractVersion": self._governance_profile.goal_contract.version,
            },
        )

    @staticmethod
    def _handoff_id_for(assessment: LegalComplianceAssessment) -> UUID:
        # Deterministic per assessment id so a resume after a partial persist
        # cannot mint a second handoff for the same assessment.
        return uuid5(NAMESPACE_OID, f"legal-handoff:{assessment.id}")

    @staticmethod
    def _assessment_from_checkpoint(
        checkpoint: TaskCheckpoint,
    ) -> LegalComplianceAssessment:
        raw = checkpoint.checkpoint_data.get("assessment")
        if not isinstance(raw, Mapping):
            raise ReviewerOutputInvalid(
                "inference checkpoint has no assessment payload"
            )
        return LegalComplianceAssessment.model_validate(raw)

    async def _save_persisted_checkpoint(
        self,
        save_checkpoint: CheckpointCallback,
        persisted: PersistedLegalOutput,
    ) -> None:
        await save_checkpoint(
            CHECKPOINT_PERSISTED,
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "gapIds": [str(gap_id) for gap_id in persisted.gap_ids],
                "controlledCheckRecordIds": [
                    str(record_id) for record_id in persisted.controlled_check_record_ids
                ],
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
                    "role": LEGAL_AGENT_ROLE,
                    "recordedAt": self._clock().isoformat(),
                    **dict(event_data),
                },
            )
        )
