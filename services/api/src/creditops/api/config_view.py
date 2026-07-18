"""Read-only configuration API: GET /api/v1/configuration.

The ``/cau-hinh`` surface (master design section 17.1): it exposes the RUNNING
configuration's NON-SECRET identity so a case participant can see which closed,
synthetic vocabularies and pinned models the system is operating under.  It is
strictly read-only -- there is NO mutation route, by design: a banking rule is
never edited here without a supplied source (design section 24, fail closed).

What is EXPOSED: the human-gate registry names, the PROPOSED synthetic
disposition taxonomies, the closed synthetic case-role set, the FPT catalog
STATE (per capability: the pinned non-secret model id and whether a benchmark
record exists), the allocation-policy version, and the synthetic-notice refs.

What is NEVER exposed: FPT endpoint URLs, endpoint ids, or API keys, or any
other secret material.  Only ``model_catalog`` and ``benchmark_records`` are
imported (both are committed, non-secret code); the secret-bearing runtime
``FPTCatalog`` config is not touched here.

This router is deliberately NOT wired into ``main.py`` here; it exports
``router`` for the lead to mount.
"""

from __future__ import annotations

from typing import Annotated, cast, get_args

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.gates import (
    ALL_GATES,
    G3_CONTINUE_DISPOSITION_TYPES,
)
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.unit_of_work import ActorContext
from creditops.domain.conditions import ConditionStatus
from creditops.domain.gap_request_batches import BatchDispositionType
from creditops.domain.repayments import ALLOCATION_POLICY_VERSION
from creditops.domain.security_interests import PerfectionStatus
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_EN, SYNTHETIC_NOTICE_VI
from creditops.infrastructure.fpt.benchmark_records import FPT_BENCHMARK_RECORDS
from creditops.infrastructure.fpt.catalog import CapabilityName
from creditops.infrastructure.fpt.model_catalog import FPT_MODEL_CATALOG

router = APIRouter(prefix="/api/v1/configuration", tags=["configuration"])

_SYNTHETIC_LABEL = "SYNTHETIC"

# Mirror of the CLOSED synthetic case-role set from migration
# 202607180008_case_assignment_roles.sql.  Kept as a module constant so the
# configuration surface can expose it; a drift test pins it against the roles
# ``api/cases.py`` actually uses (``INTAKE_OFFICER_ROLE`` and
# ``CASE_PARTICIPANT_ROLES``).  The official SHB role mapping is an OPEN QUESTION
# (design section 24); every name here is PROPOSED / synthetic.
SYNTHETIC_CASE_ROLE_SET: tuple[str, ...] = (
    "INTAKE_OFFICER",
    "UNDERWRITER",
    "LEGAL_REVIEWER",
    "RISK_REVIEWER",
    "OPS_OFFICER",
    "OPS_CHECKER",
    "ACTION_AUTHORIZER",
    "MONITORING_OFFICER",
    "COLLECTIONS_OFFICER",
    "AUDITOR",
)

# The closed FPT capability vocabulary (from the ``CapabilityName`` literal).
_CAPABILITY_NAMES: tuple[str, ...] = tuple(str(name) for name in get_args(CapabilityName))


class DispositionTaxonomies(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    g3_continue_disposition_types: list[str] = Field(
        serialization_alias="g3ContinueDispositionTypes"
    )
    batch_disposition_types: list[str] = Field(serialization_alias="batchDispositionTypes")
    condition_statuses: list[str] = Field(serialization_alias="conditionStatuses")
    perfection_statuses: list[str] = Field(serialization_alias="perfectionStatuses")


class FptCapabilityState(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    capability: str
    #: The pinned, NON-SECRET model identifier, or null when the capability is
    #: intentionally unpinned (fails closed).  Never an endpoint URL or key.
    pinned_model_id: str | None = Field(serialization_alias="pinnedModelId")
    #: Whether a committed PASSED benchmark record exists for this capability.
    benchmark_passed: bool = Field(serialization_alias="benchmarkPassed")


class NoticeRefs(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    #: Repository-relative pointer to the single source of truth.
    source: str
    vi: str
    en: str


class ConfigurationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    #: Synthetic-prototype marker (design section 2): every field below is a
    #: PROPOSED synthetic identity, not an official SHB configuration.
    label: str = _SYNTHETIC_LABEL
    gate_registry: list[str] = Field(serialization_alias="gateRegistry")
    disposition_taxonomies: DispositionTaxonomies = Field(
        serialization_alias="dispositionTaxonomies"
    )
    synthetic_role_set: list[str] = Field(serialization_alias="syntheticRoleSet")
    fpt_catalog: list[FptCapabilityState] = Field(serialization_alias="fptCatalog")
    allocation_policy_version: str = Field(serialization_alias="allocationPolicyVersion")
    notice_refs: NoticeRefs = Field(serialization_alias="noticeRefs")


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_participant(actor: ActorContext) -> None:
    """Fail closed unless the actor holds at least one case-participant role."""
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _benchmark_passed(capability: str) -> bool:
    return any(
        record.capability == capability and record.passed
        for record in FPT_BENCHMARK_RECORDS
    )


def _fpt_catalog_state() -> list[FptCapabilityState]:
    return [
        FptCapabilityState(
            capability=capability,
            pinned_model_id=FPT_MODEL_CATALOG.get(cast(CapabilityName, capability)),
            benchmark_passed=_benchmark_passed(capability),
        )
        for capability in _CAPABILITY_NAMES
    ]


@router.get("", response_model=ConfigurationResponse)
async def get_configuration(actor: Actor) -> ConfigurationResponse:
    _require_participant(actor)
    return ConfigurationResponse(
        gate_registry=[gate.value for gate in ALL_GATES],
        disposition_taxonomies=DispositionTaxonomies(
            g3_continue_disposition_types=sorted(G3_CONTINUE_DISPOSITION_TYPES),
            batch_disposition_types=[member.value for member in BatchDispositionType],
            condition_statuses=[member.value for member in ConditionStatus],
            perfection_statuses=[member.value for member in PerfectionStatus],
        ),
        synthetic_role_set=list(SYNTHETIC_CASE_ROLE_SET),
        fpt_catalog=_fpt_catalog_state(),
        allocation_policy_version=ALLOCATION_POLICY_VERSION,
        notice_refs=NoticeRefs(
            source="shared/synthetic-notice.json",
            vi=SYNTHETIC_NOTICE_VI,
            en=SYNTHETIC_NOTICE_EN,
        ),
    )
