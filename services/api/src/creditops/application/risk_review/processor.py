"""Worker processor for INDEPENDENT_RISK_REVIEW tasks (two-pass checker).

Registered in the per-task-type ProcessorRegistry exactly like
CREDIT_UNDERWRITING and LEGAL_COMPLIANCE_COLLATERAL.  The independent risk
review runs in two passes (design stage 6):

- Pass A -- BLIND pre-analysis.  The checker reads only the blind evidence
  view (Confirmed Facts) and forms an INDEPENDENT, structured pre-analysis
  BEFORE seeing any maker conclusion.  Persisted to its own append-only store,
  deduplicated per (case, version, task).
- Pass B -- the checker assessment as before, now receiving BOTH the maker/
  legal artifacts AND the Pass A blind pre-analysis, marking per challenge
  whether the concern was also surfaced blind (``Challenge.raised_blind``).

Stages are checkpointed (evidence view -> maker outputs loaded -> blind
pre-analysis persisted -> deterministic pre-analysis computed -> inference
validated -> persisted).  A redelivery resumes from the latest checkpoint; the
blind-pass persist and the Pass B persist are each idempotent on (case, case
version, task), making duplicate delivery harmless and keeping exactly one
blind pre-analysis row per task even across a Pass A crash.

Fail-closed paths beyond the shared "no gateway" policy:

- No configured reasoning endpoint (either pass): both passes fail closed
  (FAILED_MANUAL_REVIEW) -- no fabricated analysis.
- Readiness already requires both maker handoffs at the task-graph level
  (application/orchestration/graph.py); this processor re-checks at execution
  time that BOTH maker outputs are actually loadable and fails closed if
  either is missing -- defense in depth.
- The same-execution guard: if this checker execution's id would equal either
  reviewed maker's execution id, the task fails closed BEFORE any model call
  (including the blind Pass A call) -- "the same role execution must not
  author and independently clear the same material conclusion."  Maker outputs
  are loaded first only for this control-plane guard; their conclusions never
  enter Pass A's reasoning context.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from pydantic import ValidationError

from creditops.application.governance import (
    RISK_REVIEW_PRE_ANALYSIS_GOVERNANCE,
    governance_for,
    manifest_from_governance,
)
from creditops.application.ports.governance import GovernanceRepository
from creditops.application.ports.model_gateway import (
    InferenceError,
    InferenceUnavailableError,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.ports.risk_review import (
    CheckerEvidenceView,
    MakerOutputsView,
    OpenGapRecord,
    PersistedCheckerOutput,
    PreAnalysisEvidenceView,
    PreAnalysisRecord,
    RiskReviewRepository,
)
from creditops.application.risk_review.analysis import compute_deterministic_pre_analysis
from creditops.application.risk_review.checker import (
    PRE_ANALYSIS_PROMPT_VERSION,
    PRE_ANALYSIS_SCHEMA_VERSION,
    CheckerOutputInvalid,
    CheckerRunContext,
    PreAnalysisOutputInvalid,
    RiskPreAnalysis,
    RunCheckerInference,
    RunPreAnalysisInference,
    SameExecutionGuardTriggered,
    persist_checker_output,
)
from creditops.application.risk_review.evidence import build_target_universe
from creditops.application.use_cases.run_worker_once import (
    CheckpointCallback,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.goal_contracts import UNIVERSAL_PROHIBITED_ACTIONS
from creditops.domain.orchestration import TaskType
from creditops.domain.risk_review import RISK_REVIEW_AGENT_ROLE, RiskReviewAssessment

#: The service identity under which the worker acts when it builds a context
#: manifest (see the underwriting processor for the rationale).
_SERVICE_IDENTITY = "service:agent-worker"

CHECKPOINT_EVIDENCE_VIEW = "EVIDENCE_VIEW_BUILT"
CHECKPOINT_MAKER_OUTPUTS = "MAKER_OUTPUTS_LOADED"
#: Pass A (blind pre-analysis) persisted: a resume from here skips the blind
#: model call and goes straight into Pass B.
CHECKPOINT_PRE_ANALYSIS_PERSISTED = "PRE_ANALYSIS_PERSISTED"
CHECKPOINT_PRE_ANALYSIS = "PRE_ANALYSIS_COMPUTED"
CHECKPOINT_INFERENCE = "INFERENCE_VALIDATED"
CHECKPOINT_PERSISTED = "ASSESSMENT_PERSISTED"


class IndependentRiskReviewProcessor:
    """Run one resumable, idempotent checker execution for a claimed task."""

    def __init__(
        self,
        repository: RiskReviewRepository,
        inference: RunCheckerInference | None,
        pre_analysis_inference: RunPreAnalysisInference | None = None,
        *,
        governance: GovernanceRepository | None = None,
        clock: Callable[[], datetime] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._inference = inference
        self._governance = governance
        # This task runs TWO bounded model calls, each with its OWN committed
        # goal contract: the blind Pass A pre-analysis and the Pass B checker
        # assessment.  Both are fetched unconditionally so the universal
        # human-only bans are enforced at construction (see the underwriting
        # processor); the Pass B contract is the task type's primary contract.
        self._governance_profile = governance_for(TaskType.INDEPENDENT_RISK_REVIEW)
        self._pre_analysis_governance = RISK_REVIEW_PRE_ANALYSIS_GOVERNANCE
        assert UNIVERSAL_PROHIBITED_ACTIONS.issubset(
            self._governance_profile.goal_contract.prohibited_actions
        ), "risk-review checker goal contract must restate every universal ban"
        assert UNIVERSAL_PROHIBITED_ACTIONS.issubset(
            self._pre_analysis_governance.goal_contract.prohibited_actions
        ), "risk-review blind pre-analysis goal contract must restate every universal ban"
        # Worker composition wires only the Pass B runner; derive the blind
        # Pass A runner over the very same reasoning endpoint when one is not
        # injected explicitly, so both passes share one endpoint and both are
        # disabled together when reasoning is unavailable.
        if pre_analysis_inference is None and inference is not None:
            pre_analysis_inference = RunPreAnalysisInference(inference.gateway)
        self._pre_analysis_inference = pre_analysis_inference
        self._clock = clock or (lambda: datetime.now(UTC))
        self._execution_id_factory = execution_id_factory or uuid4

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_PERSISTED:
            return StageResult()

        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_INFERENCE:
            return await self._persist_stage(
                task, self._assessment_from_checkpoint(checkpoint), save_checkpoint
            )

        existing = await self._repository.find_persisted(
            case_id=task.case_id, case_version=task.case_version, task_id=task.id
        )
        if existing is not None:
            await self._save_persisted_checkpoint(save_checkpoint, existing)
            return StageResult()

        if self._inference is None or self._pre_analysis_inference is None:
            await self._audit(
                task,
                "RISK_REVIEW_GATEWAY_UNAVAILABLE",
                {"reason": "no configured FPT reasoning endpoint"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "independent risk review requires a configured FPT reasoning endpoint",
            )

        view = await self._repository.load_evidence_view(task.case_id)
        if view is None:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW, f"case {task.case_id} has no evidence view"
            )
        if view.case_version != task.case_version:
            return StageResult(
                WorkerOutcome.SUPERSEDED, "case version advanced past this task's bound version"
            )
        await save_checkpoint(
            CHECKPOINT_EVIDENCE_VIEW,
            {
                "confirmedFactCount": len(view.confirmed_facts),
                "builtAt": view.built_at.isoformat(),
            },
        )

        outputs = await self._repository.load_maker_outputs(task.case_id, task.case_version)
        if not outputs.is_complete():
            # Defense-in-depth: readiness (graph.py) already requires both
            # maker handoffs before this task type may become READY.  A
            # partial pair here means that invariant was violated upstream;
            # never review one maker's output alone.
            await self._audit(
                task,
                "RISK_REVIEW_MAKER_OUTPUTS_INCOMPLETE",
                {
                    "hasUnderwriting": outputs.underwriting is not None,
                    "hasLegal": outputs.legal is not None,
                },
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "independent risk review requires both maker handoffs "
                "(READY_FOR_RISK_REVIEW from underwriting AND legal/compliance)",
            )
        underwriting = outputs.underwriting
        legal = outputs.legal
        assert underwriting is not None and legal is not None
        assert outputs.underwriting_execution_id is not None
        assert outputs.legal_execution_id is not None

        run = CheckerRunContext(
            task_id=task.id,
            execution_id=self._execution_id_factory(),
            correlation_id=f"risk-review:{task.id}",
        )
        if run.execution_id in (outputs.underwriting_execution_id, outputs.legal_execution_id):
            # Same-execution guard: fail closed BEFORE any model call.
            await self._audit(
                task,
                "RISK_REVIEW_SAME_EXECUTION_GUARD_TRIGGERED",
                {
                    "checkerExecutionId": str(run.execution_id),
                    "underwritingExecutionId": str(outputs.underwriting_execution_id),
                    "legalExecutionId": str(outputs.legal_execution_id),
                },
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "checker execution id must differ from every reviewed maker execution id "
                "(maker-checker separation)",
            )
        await save_checkpoint(
            CHECKPOINT_MAKER_OUTPUTS,
            {
                "underwritingAssessmentId": str(underwriting.id),
                "legalAssessmentId": str(legal.id),
            },
        )

        # ---- Pass A: blind pre-analysis (skipped on resume / when durable) ----
        blind = await self._resolve_pre_analysis(task, checkpoint, save_checkpoint)
        if isinstance(blind, StageResult):
            return blind

        # ---- Pass B: checker assessment, now fed the blind pre-analysis ----
        open_gaps = await self._repository.load_open_gaps(task.case_id, task.case_version)
        pre_analysis = compute_deterministic_pre_analysis(
            underwriting=underwriting, legal=legal, checker_view=view, open_gaps=open_gaps
        )
        universe = build_target_universe(underwriting, legal)
        await save_checkpoint(
            CHECKPOINT_PRE_ANALYSIS,
            {
                "deterministicChallengeCount": len(pre_analysis.all_challenges),
                "blockingGapVisibilityCount": len(pre_analysis.visibility_checks.blocking_gaps),
                "exceptionVisibilityCount": len(pre_analysis.visibility_checks.exceptions),
            },
        )

        # Governance: snapshot exactly what the Pass B checker call is
        # authorized to see (confirmed facts, BOTH maker handoffs and open gaps)
        # BEFORE inference; a distinct manifest from the blind Pass A one, and
        # reached only on the fresh pre-inference path.
        await self._persist_checker_manifest(task, view, outputs, open_gaps)

        try:
            assessment = await self._inference.infer(
                view=view,
                underwriting=underwriting,
                underwriting_execution_id=outputs.underwriting_execution_id,
                legal=legal,
                legal_execution_id=outputs.legal_execution_id,
                pre_analysis=pre_analysis,
                universe=universe,
                policy_hits=legal.policy_hits,
                controlled_check_results=legal.controlled_check_results,
                run=run,
                blind_pre_analysis=blind,
            )
        except InferenceUnavailableError:
            await self._audit(
                task,
                "RISK_REVIEW_GATEWAY_UNAVAILABLE",
                {"reason": "FPT reasoning endpoint unavailable"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW, "FPT reasoning endpoint unavailable"
            )
        except SameExecutionGuardTriggered as exc:
            # Second-layer guard (also enforced pre-inference above and by the
            # domain schema itself): never retryable, always manual review.
            await self._audit(
                task, "RISK_REVIEW_SAME_EXECUTION_GUARD_TRIGGERED", {"reason": str(exc)[:2000]}
            )
            return StageResult(WorkerOutcome.FAILED_MANUAL_REVIEW, str(exc))
        except (CheckerOutputInvalid, InferenceError, ValidationError) as exc:
            await self._audit(
                task, "RISK_REVIEW_OUTPUT_REJECTED", {"reason": str(exc)[:2000]}
            )
            return StageResult(WorkerOutcome.RETRY_WAIT, f"checker output rejected: {exc}")

        await save_checkpoint(
            CHECKPOINT_INFERENCE, {"assessment": assessment.model_dump(mode="json")}
        )
        return await self._persist_stage(task, assessment, save_checkpoint)

    async def _resolve_pre_analysis(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> RiskPreAnalysis | StageResult:
        """Return the blind Pass A pre-analysis, running it only if necessary.

        Returns a ``StageResult`` instead when Pass A must abort (no evidence
        view, superseded case version, endpoint unavailable, or an invalid
        blind output).  Resumes without a model call from either the
        ``PRE_ANALYSIS_PERSISTED`` checkpoint or, if that was lost, the durable
        blind-pre-analysis row -- either way exactly one blind row exists.
        """

        assert self._pre_analysis_inference is not None

        if (
            checkpoint is not None
            and checkpoint.checkpoint_type == CHECKPOINT_PRE_ANALYSIS_PERSISTED
        ):
            return self._pre_analysis_from_checkpoint(checkpoint)

        found = await self._repository.find_pre_analysis(
            case_id=task.case_id, case_version=task.case_version, task_id=task.id
        )
        if found is not None:
            return RiskPreAnalysis.model_validate(dict(found.analysis))

        blind_view = await self._repository.load_blind_evidence_view(task.case_id)
        if blind_view is None:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                f"case {task.case_id} has no evidence view for the blind pass",
            )
        if blind_view.case_version != task.case_version:
            return StageResult(
                WorkerOutcome.SUPERSEDED, "case version advanced past this task's bound version"
            )

        # Governance: the blind Pass A manifest records ONLY the blind evidence
        # view (Confirmed Facts) and NO maker output -- the manifest is
        # structurally incapable of naming a maker artifact, proving blind
        # separation at the audit layer.  Persisted BEFORE the blind call and
        # reached only when Pass A actually runs (a resume from
        # PRE_ANALYSIS_PERSISTED or a durable blind row returns above).
        await self._persist_pre_analysis_manifest(task, blind_view)

        pre_execution_id = self._execution_id_factory()
        try:
            blind = await self._pre_analysis_inference.infer(
                view=blind_view, correlation_id=f"risk-review-blind:{task.id}"
            )
        except InferenceUnavailableError:
            await self._audit(
                task,
                "RISK_REVIEW_GATEWAY_UNAVAILABLE",
                {"reason": "FPT reasoning endpoint unavailable", "pass": "A"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW, "FPT reasoning endpoint unavailable"
            )
        except (PreAnalysisOutputInvalid, InferenceError, ValidationError) as exc:
            await self._audit(
                task, "RISK_REVIEW_PRE_ANALYSIS_REJECTED", {"reason": str(exc)[:2000]}
            )
            return StageResult(
                WorkerOutcome.RETRY_WAIT, f"blind pre-analysis rejected: {exc}"
            )

        persisted = await self._repository.persist_pre_analysis(
            record=PreAnalysisRecord(
                id=uuid4(),
                case_id=task.case_id,
                case_version=task.case_version,
                task_id=task.id,
                execution_id=pre_execution_id,
                prompt_version=PRE_ANALYSIS_PROMPT_VERSION,
                schema_version=PRE_ANALYSIS_SCHEMA_VERSION,
                analysis=blind.model_dump(mode="json"),
            )
        )
        await save_checkpoint(
            CHECKPOINT_PRE_ANALYSIS_PERSISTED,
            {
                "preAnalysisId": str(persisted.pre_analysis_id),
                "executionId": str(pre_execution_id),
                "independentRiskCount": len(blind.independent_risks),
                "observationCount": len(blind.observations),
                "analysis": blind.model_dump(mode="json"),
            },
        )
        await self._audit(
            task,
            "RISK_REVIEW_PRE_ANALYSIS_PERSISTED",
            {
                "preAnalysisId": str(persisted.pre_analysis_id),
                "created": persisted.created,
                "independentRiskCount": len(blind.independent_risks),
                "observationCount": len(blind.observations),
            },
        )
        # The durable payload wins on an idempotent conflict, so a redelivery
        # that raced ahead cannot diverge Pass B's blind input from the row.
        return RiskPreAnalysis.model_validate(dict(persisted.analysis))

    @staticmethod
    def _pre_analysis_from_checkpoint(checkpoint: TaskCheckpoint) -> RiskPreAnalysis:
        raw = checkpoint.checkpoint_data.get("analysis")
        if not isinstance(raw, Mapping):
            raise PreAnalysisOutputInvalid("pre-analysis checkpoint has no analysis payload")
        return RiskPreAnalysis.model_validate(dict(raw))

    async def _persist_stage(
        self,
        task: TaskRecord,
        assessment: RiskReviewAssessment,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        persisted = await persist_checker_output(
            self._repository, assessment, handoff_id=self._handoff_id_for(assessment)
        )
        await self._save_persisted_checkpoint(save_checkpoint, persisted)
        await self._audit(
            task,
            "RISK_REVIEW_ASSESSMENT_PERSISTED",
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "challengeCount": len(persisted.challenge_ids),
                "executionId": str(assessment.provenance.execution_id),
            },
        )
        return StageResult()

    async def _persist_pre_analysis_manifest(
        self, task: TaskRecord, blind_view: PreAnalysisEvidenceView
    ) -> None:
        """Persist the blind Pass A context manifest (no-op without a wired
        governance repository)."""

        if self._governance is None:
            return
        manifest = manifest_from_governance(
            self._pre_analysis_governance,
            case_id=task.case_id,
            case_version=task.case_version,
            task_id=task.id,
            actor_or_service_identity=_SERVICE_IDENTITY,
            case_roles=(RISK_REVIEW_AGENT_ROLE,),
            authoritative_fact_refs=tuple(
                fact.confirmed_fact_id for fact in blind_view.confirmed_facts
            ),
        )
        persisted = await self._governance.persist_manifest(manifest)
        await self._audit(
            task,
            "RISK_REVIEW_PRE_ANALYSIS_CONTEXT_MANIFEST_PERSISTED",
            {
                "contextManifestId": str(persisted.manifest_id),
                "contextHash": persisted.context_hash,
                "goalContractKey": self._pre_analysis_governance.contract_key,
                "goalContractVersion": (
                    self._pre_analysis_governance.goal_contract.version
                ),
                "pass": "A",
            },
        )

    async def _persist_checker_manifest(
        self,
        task: TaskRecord,
        view: CheckerEvidenceView,
        outputs: MakerOutputsView,
        open_gaps: tuple[OpenGapRecord, ...],
    ) -> None:
        """Persist the Pass B checker context manifest (no-op without a wired
        governance repository)."""

        if self._governance is None:
            return
        upstream_refs = tuple(
            handoff_id
            for handoff_id in (
                outputs.underwriting_handoff_id,
                outputs.legal_handoff_id,
            )
            if handoff_id is not None
        )
        manifest = manifest_from_governance(
            self._governance_profile,
            case_id=task.case_id,
            case_version=task.case_version,
            task_id=task.id,
            actor_or_service_identity=_SERVICE_IDENTITY,
            case_roles=(RISK_REVIEW_AGENT_ROLE,),
            authoritative_fact_refs=tuple(
                fact.confirmed_fact_id for fact in view.confirmed_facts
            ),
            upstream_artifact_refs=upstream_refs,
            open_gap_refs=tuple(gap.gap_id for gap in open_gaps),
        )
        persisted = await self._governance.persist_manifest(manifest)
        await self._audit(
            task,
            "RISK_REVIEW_CONTEXT_MANIFEST_PERSISTED",
            {
                "contextManifestId": str(persisted.manifest_id),
                "contextHash": persisted.context_hash,
                "goalContractKey": self._governance_profile.contract_key,
                "goalContractVersion": self._governance_profile.goal_contract.version,
                "pass": "B",
            },
        )

    @staticmethod
    def _handoff_id_for(assessment: RiskReviewAssessment) -> UUID:
        return uuid5(NAMESPACE_OID, f"risk-review-handoff:{assessment.id}")

    @staticmethod
    def _assessment_from_checkpoint(checkpoint: TaskCheckpoint) -> RiskReviewAssessment:
        raw = checkpoint.checkpoint_data.get("assessment")
        if not isinstance(raw, Mapping):
            raise CheckerOutputInvalid("inference checkpoint has no assessment payload")
        return RiskReviewAssessment.model_validate(raw)

    async def _save_persisted_checkpoint(
        self, save_checkpoint: CheckpointCallback, persisted: PersistedCheckerOutput
    ) -> None:
        await save_checkpoint(
            CHECKPOINT_PERSISTED,
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "challengeIds": [str(cid) for cid in persisted.challenge_ids],
                "created": persisted.created,
            },
        )

    async def _audit(
        self, task: TaskRecord, event_type: str, event_data: Mapping[str, Any]
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
                    "role": RISK_REVIEW_AGENT_ROLE,
                    "recordedAt": self._clock().isoformat(),
                    **dict(event_data),
                },
            )
        )
