"""Assembler: deterministic skeleton -> bounded, evidence-grounded memo draft.

Mirrors ``application/risk_review/checker.py``: the deterministic pass
(application/credit_ops/analysis.py) runs FIRST and produces the completeness
checklist, provenance index, drafted document requests, and drafted proposed
actions -- ALL of which are final before any model call.  The LLM then
receives that skeleton plus the three upstream assessments and may only
reference evidence through a closed JSON response schema: every citation
target is enum/const-pinned to the exact in-scope upstream finding/section
paths (application/credit_ops/evidence.py), so a well-formed response cannot
invent a finding or cite anything outside the reviewed assessments.  The LLM
drafts ONLY the memo narrative; it never touches the checklist, the
provenance index, the document requests, or the proposed actions.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from creditops.application.credit_ops.analysis import DeterministicCreditOpsPackage
from creditops.application.credit_ops.evidence import (
    MemoTargetUniverse,
    build_memo_target_universe,
    legal_target_paths,
    risk_review_target_paths,
    underwriting_target_paths,
)
from creditops.application.ports.credit_ops import (
    CreditOpsRepository,
    CreditOpsUpstreamView,
    PersistedCreditOpsOutput,
)
from creditops.application.ports.model_gateway import InferenceGateway, ReasonRequest
from creditops.domain.credit_ops import (
    ChallengeStatusSection,
    CreditOpsPackage,
    CreditOpsProvenance,
    DraftCreditMemo,
    MemoFindingCitation,
    MemoFindingRef,
    MemoSection,
    MemoSource,
    MemoStatement,
)

CREDIT_OPS_PROMPT_VERSION = "credit-ops-prompt-v1"
CREDIT_OPS_SCHEMA_VERSION = "credit-ops-package-v1"
HUMAN_DECISION_HANDOFF_STATE = "READY_FOR_HUMAN_DECISION"

_MEMO_SECTION_KEYS = (
    "tom_tat_nhu_cau",
    "phan_tich_maker",
    "ra_soat_phap_ly_tsbd",
    "dieu_kien_de_xuat",
    "phu_luc_bang_chung",
)


class CreditOpsOutputInvalid(ValueError):
    """The LLM response failed deterministic validation; bounded retry applies."""


class CreditOpsPrompt:
    """Load the versioned Vietnamese trusted-instruction credit-ops prompt."""

    version = CREDIT_OPS_PROMPT_VERSION

    def __init__(self, text: str | None = None) -> None:
        self._text = text if text is not None else self._load()

    @staticmethod
    def _load() -> str:
        return (
            resources.files("creditops.prompts.credit_ops")
            .joinpath("v1.md")
            .read_text(encoding="utf-8")
        )

    @property
    def text(self) -> str:
        return self._text


def _target_schema(universe: MemoTargetUniverse) -> Mapping[str, Any]:
    branches: list[Mapping[str, Any]] = []
    if universe.underwriting_paths:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "source_assessment_id", "section_path"],
                "properties": {
                    "source": {"const": "CREDIT_UNDERWRITING"},
                    "source_assessment_id": {"const": str(universe.underwriting_assessment_id)},
                    "section_path": {"enum": list(universe.underwriting_paths)},
                },
            }
        )
    if universe.legal_paths:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "source_assessment_id", "section_path"],
                "properties": {
                    "source": {"const": "LEGAL_COMPLIANCE_COLLATERAL"},
                    "source_assessment_id": {"const": str(universe.legal_assessment_id)},
                    "section_path": {"enum": list(universe.legal_paths)},
                },
            }
        )
    if universe.risk_review_paths:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "source_assessment_id", "section_path"],
                "properties": {
                    "source": {"const": "INDEPENDENT_RISK_REVIEW"},
                    "source_assessment_id": {"const": str(universe.risk_review_assessment_id)},
                    "section_path": {"enum": list(universe.risk_review_paths)},
                },
            }
        )
    return {"oneOf": branches}


def _statement_schema(universe: MemoTargetUniverse) -> Mapping[str, Any]:
    citation = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "ref"],
        "properties": {"kind": {"const": "MEMO_FINDING"}, "ref": _target_schema(universe)},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["statement_vi", "citations"],
        "properties": {
            "statement_vi": {"type": "string", "minLength": 1, "maxLength": 4000},
            "citations": {"type": "array", "minItems": 1, "maxItems": 10, "items": citation},
        },
    }


def _section_schema(universe: MemoTargetUniverse) -> Mapping[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["statements"],
        "properties": {
            "statements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": _statement_schema(universe),
            }
        },
    }


def build_response_schema(universe: MemoTargetUniverse) -> Mapping[str, Any]:
    """Closed response schema for the memo narrative ONLY.

    ``package_completeness``, ``evidence_consolidation``, ``document_requests``
    and ``proposed_actions`` are deliberately absent -- they are
    deterministic-only and never populated by the LLM.  ``disposition_status_vi``
    inside ``thach_thuc_checker`` is likewise absent from this schema; the
    assembler fills it from the deterministic pass, never from the model.
    """

    section = _section_schema(universe)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_MEMO_SECTION_KEYS) + ["thach_thuc_checker"],
        "properties": {
            **{key: section for key in _MEMO_SECTION_KEYS},
            "thach_thuc_checker": section,
        },
    }


def target_universe_for(view: CreditOpsUpstreamView) -> MemoTargetUniverse:
    assert view.underwriting is not None
    assert view.legal is not None
    assert view.risk_review is not None
    return build_memo_target_universe(
        underwriting_assessment_id=view.underwriting.id,
        underwriting_paths=underwriting_target_paths(view.underwriting),
        legal_assessment_id=view.legal.id,
        legal_paths=legal_target_paths(view.legal),
        risk_review_assessment_id=view.risk_review.id,
        risk_review_paths=risk_review_target_paths(view.risk_review),
    )


def build_untrusted_context(
    *,
    view: CreditOpsUpstreamView,
    deterministic: DeterministicCreditOpsPackage,
    universe: MemoTargetUniverse,
) -> str:
    """Serialize the scoped, deterministic-first context for the prompt's
    untrusted-data region."""

    assert view.underwriting is not None
    assert view.legal is not None
    assert view.risk_review is not None
    payload: dict[str, Any] = {
        "caseId": str(view.case_id),
        "caseVersion": view.case_version,
        "packageCompleteness": deterministic.package_completeness.model_dump(mode="json"),
        "documentRequestCount": len(deterministic.document_requests),
        "underwritingAssessment": view.underwriting.model_dump(mode="json"),
        "underwritingTargetPaths": list(universe.underwriting_paths),
        "legalAssessment": view.legal.model_dump(mode="json"),
        "legalTargetPaths": list(universe.legal_paths),
        "riskReviewAssessment": view.risk_review.model_dump(mode="json"),
        "riskReviewTargetPaths": list(universe.risk_review_paths),
        "note": (
            "Danh sach kiem tra day du ho so, chi so tong hop bang chung, cac "
            "yeu cau bo sung tai lieu, va cac hanh dong de xuat da duoc tinh "
            "san tat dinh; ban CHI soan phan tuong thuat cua bien ban tin dung "
            "(draft memo), khong duoc thay doi cac phan tat dinh do."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _ref_from(raw: Mapping[str, Any], universe: MemoTargetUniverse) -> MemoFindingRef:
    try:
        ref = MemoFindingRef(
            source=MemoSource(str(raw.get("source", ""))),
            source_assessment_id=UUID(str(raw.get("source_assessment_id", ""))),
            section_path=str(raw.get("section_path", "")),
        )
    except (ValueError, ValidationError) as exc:
        raise CreditOpsOutputInvalid(f"invalid memo finding reference: {raw}") from exc
    if not universe.contains(ref):
        raise CreditOpsOutputInvalid(f"reference does not resolve in reviewed assessments: {ref}")
    return ref


def _citations_from(
    raw: Sequence[Mapping[str, Any]], universe: MemoTargetUniverse
) -> tuple[MemoFindingCitation, ...]:
    citations: list[MemoFindingCitation] = []
    for item in raw:
        if item.get("kind") != "MEMO_FINDING":
            raise CreditOpsOutputInvalid(f"unsupported citation kind: {item.get('kind')!r}")
        ref_raw = item.get("ref")
        if not isinstance(ref_raw, Mapping):
            raise CreditOpsOutputInvalid("MEMO_FINDING citation missing ref")
        citations.append(MemoFindingCitation(ref=_ref_from(ref_raw, universe)))
    return tuple(citations)


def _section_from(
    payload: Mapping[str, Any], key: str, universe: MemoTargetUniverse
) -> MemoSection:
    raw = payload.get(key)
    if not isinstance(raw, Mapping):
        raise CreditOpsOutputInvalid(f"missing memo section: {key}")
    raw_statements = raw.get("statements", [])
    if not isinstance(raw_statements, list) or not raw_statements:
        raise CreditOpsOutputInvalid(f"memo section {key} has no statements")
    statements = tuple(
        MemoStatement(
            statement_vi=str(item.get("statement_vi", "")),
            citations=_citations_from(list(item.get("citations", [])), universe),
        )
        for item in raw_statements
        if isinstance(item, Mapping)
    )
    if not statements:
        raise CreditOpsOutputInvalid(f"memo section {key} produced no valid statements")
    return MemoSection(statements=statements)


class BuildMemo:
    """Deterministically validate an LLM payload into a ``DraftCreditMemo``."""

    def build(
        self,
        *,
        payload: Mapping[str, Any],
        universe: MemoTargetUniverse,
        disposition_status_vi: str,
    ) -> DraftCreditMemo:
        try:
            sections = {key: _section_from(payload, key, universe) for key in _MEMO_SECTION_KEYS}
            challenge_section = _section_from(payload, "thach_thuc_checker", universe)
            return DraftCreditMemo(
                tom_tat_nhu_cau=sections["tom_tat_nhu_cau"],
                phan_tich_maker=sections["phan_tich_maker"],
                ra_soat_phap_ly_tsbd=sections["ra_soat_phap_ly_tsbd"],
                thach_thuc_checker=ChallengeStatusSection(
                    statements=challenge_section.statements,
                    disposition_status_vi=disposition_status_vi,
                ),
                dieu_kien_de_xuat=sections["dieu_kien_de_xuat"],
                phu_luc_bang_chung=sections["phu_luc_bang_chung"],
            )
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, CreditOpsOutputInvalid):
                raise
            raise CreditOpsOutputInvalid(f"credit-ops memo output rejected: {exc}") from exc


@dataclass(frozen=True, slots=True)
class AssemblerRunContext:
    """Identity of one credit-ops execution, recorded in provenance."""

    task_id: UUID
    execution_id: UUID
    correlation_id: str


class RunMemoInference:
    """Call the reasoning endpoint with the closed memo schema and validate."""

    def __init__(
        self,
        gateway: InferenceGateway,
        *,
        prompt: CreditOpsPrompt | None = None,
        builder: BuildMemo | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompt = prompt or CreditOpsPrompt()
        self._builder = builder or BuildMemo()

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def infer(
        self,
        *,
        view: CreditOpsUpstreamView,
        deterministic: DeterministicCreditOpsPackage,
        universe: MemoTargetUniverse,
        run: AssemblerRunContext,
    ) -> tuple[DraftCreditMemo, str, str]:
        schema = build_response_schema(universe)
        result = await self._gateway.reason(
            ReasonRequest(
                correlation_id=run.correlation_id,
                case_id=view.case_id,
                content=build_untrusted_context(
                    view=view, deterministic=deterministic, universe=universe
                ),
                response_schema=schema,
                system_context=self._prompt.text,
            )
        )
        if not isinstance(result.payload, Mapping):
            raise CreditOpsOutputInvalid("credit-ops memo output is not a JSON object")
        memo = self._builder.build(
            payload=result.payload,
            universe=universe,
            disposition_status_vi=deterministic.package_completeness.dispositions_state_vi,
        )
        return memo, result.model_id, result.endpoint_id


def assemble_package(
    *,
    view: CreditOpsUpstreamView,
    deterministic: DeterministicCreditOpsPackage,
    memo: DraftCreditMemo,
    run: AssemblerRunContext,
    model_id: str,
    endpoint_id: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    id_factory: Callable[[], UUID] = uuid4,
) -> CreditOpsPackage:
    """Assemble the final, persisted-shape ``CreditOpsPackage`` from the
    deterministic skeleton plus the validated memo draft.  Never mutates
    ``deterministic``; the memo is the ONLY part the model contributed."""

    now = clock()
    provenance = CreditOpsProvenance(
        case_id=view.case_id,
        case_version=view.case_version,
        execution_id=run.execution_id,
        task_id=run.task_id,
        prompt_version=CREDIT_OPS_PROMPT_VERSION,
        model_id=model_id,
        endpoint_id=endpoint_id,
        evidence_view_built_at=view.built_at,
        created_at=now,
        intake_handoff_id=view.intake_handoff_id,
        underwriting_assessment_id=view.underwriting.id if view.underwriting else None,
        underwriting_execution_id=view.underwriting_execution_id,
        legal_assessment_id=view.legal.id if view.legal else None,
        legal_execution_id=view.legal_execution_id,
        risk_review_assessment_id=view.risk_review.id if view.risk_review else None,
        risk_review_execution_id=view.risk_review_execution_id,
    )
    return CreditOpsPackage(
        id=id_factory(),
        provenance=provenance,
        package_completeness=deterministic.package_completeness,
        evidence_consolidation=deterministic.evidence_consolidation,
        document_requests=deterministic.document_requests,
        draft_memo=memo,
        proposed_actions=deterministic.proposed_actions,
    )


async def persist_credit_ops_package(
    repository: CreditOpsRepository,
    package: CreditOpsPackage,
    *,
    handoff_id: UUID,
) -> PersistedCreditOpsOutput:
    """Persist the package + operations->human-decision handoff atomically."""

    return await repository.persist_package(
        package=package, handoff_id=handoff_id, handoff_state=HUMAN_DECISION_HANDOFF_STATE
    )


__all__ = [
    "CREDIT_OPS_PROMPT_VERSION",
    "CREDIT_OPS_SCHEMA_VERSION",
    "HUMAN_DECISION_HANDOFF_STATE",
    "AssemblerRunContext",
    "BuildMemo",
    "CreditOpsOutputInvalid",
    "CreditOpsPrompt",
    "RunMemoInference",
    "assemble_package",
    "build_response_schema",
    "build_untrusted_context",
    "persist_credit_ops_package",
    "target_universe_for",
]
