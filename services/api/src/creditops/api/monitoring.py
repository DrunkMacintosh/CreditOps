"""Post-credit monitoring API: obligations, observations, covenants, alerts.

Master design section 5 giai đoạn 12 ("Quản lý khoản vay và giám sát sau cấp tín
dụng").  A Post-Credit Monitoring Agent is a SUPPORT role only (it summarises and
explains -- this change makes NO model calls).  All state here is written by
authorised humans and every early-warning alert is raised by a DETERMINISTIC rule,
never by a model.  Surfaces, all case-scoped and fail-closed (an unassigned actor
gets the same indistinguishable 404 as a missing case):

- POST/GET ``/obligations`` -- the ``MONITORING_OFFICER`` generates a run of
  monitoring obligations from a declarative ``ObligationSpec`` (deterministic
  schedule engine ``generate_obligations`` -- pure date arithmetic, no clock);
  any participant reads them.
- POST/GET ``/observations`` -- the ``MONITORING_OFFICER`` records ONE append-only
  longitudinal observation with separated timestamps (``effectiveAt`` /
  ``observedAt`` are caller data, ``recordedAt`` is the DB clock).
- POST/GET ``/covenants`` + POST ``/covenants/{id}/test`` -- the
  ``MONITORING_OFFICER`` declares a covenant carrying its own versioned threshold,
  then tests supplied numeric inputs against that threshold; the pass/fail is
  EXACTLY the declared comparison (``evaluate_covenant``), with the arithmetic
  echoed.
- POST ``/alerts/{id}/disposition`` + GET ``/alerts`` -- a ``MONITORING_REVIEWER``
  disposes an early-warning alert along a validated lifecycle edge with a
  MANDATORY rationale (the human control of this stage; there is NO gate).

DETERMINISTIC ALERT RULES (each fires inside the endpoint's own transaction, so
the alert commits atomically with the row that triggered it; each is deduped so a
source can raise at most one alert):

- ``COVENANT_BREACH`` -- fired by ``POST /covenants/{id}/test`` when
  ``evaluate_covenant`` returns ``passed = False``.  The alert binds the failed
  covenant-test row.
- ``OVERDUE_OBLIGATION`` -- fired by ``POST /observations`` when an observation is
  recorded against an obligation and its ``observedAt`` calendar date is strictly
  after the obligation's ``dueDate``.  The alert binds the obligation + the late
  observation.  (A model never raises either alert.)

NO DEBT CLASSIFICATION anywhere: this stage deliberately has no column, enum, or
field that classifies a debt -- the spec forbids it.  This module is exported as
``router`` and is NOT registered in ``main.py`` here; production wiring is a
separate change (tests include the router directly).

All customer data in this project is synthetic and created solely for
demonstration.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.ports.monitoring import (
    AlertNotFound,
    ForbiddenAlertTransition,
    MonitoringRepository,
    RecordedAlert,
    RecordedCovenant,
    RecordedCovenantTest,
    RecordedObligation,
    RecordedObservation,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.domain.monitoring import (
    AlertRule,
    AlertStatus,
    ComparisonOperator,
    Covenant,
    CovenantThreshold,
    EarlyWarningAlert,
    MonitoringObservation,
    ObligationFrequency,
    ObligationSpec,
    build_breach_detail,
    build_overdue_detail,
    covenant_breach_detected,
    evaluate_covenant,
    generate_obligations,
    is_alert_transition_allowed,
    obligation_overdue,
)

router = APIRouter(prefix="/api/v1/cases/{case_id}/monitoring", tags=["monitoring"])

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic JWT authority role for the human who records post-credit
#: monitoring data (obligations, observations, covenants, covenant tests).  No
#: official SHB role exists; row access is still enforced by case assignment.
MONITORING_OFFICER_ROLE = "MONITORING_OFFICER"

#: PROPOSED synthetic JWT authority role for the human who DISPOSES early-warning
#: alerts.  The disposition is the human control of stage 12; separating it from
#: the officer who records data keeps who-signals distinct from who-adjudicates.
MONITORING_REVIEWER_ROLE = "MONITORING_REVIEWER"

#: Roles allowed to READ the monitoring surfaces: any case participant plus the
#: two monitoring roles.
_READ_ROLES = CASE_PARTICIPANT_ROLES | {
    MONITORING_OFFICER_ROLE,
    MONITORING_REVIEWER_ROLE,
}

#: Upper bound on obligations generated in one call (PROPOSED synthetic guard).
_MAX_OBLIGATION_COUNT = 120


# -- request models -----------------------------------------------------------


class CreateObligationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    frequency: ObligationFrequency
    requirement_text_vi: str = Field(
        alias="requirementText", min_length=1, max_length=4000
    )
    from_date: date = Field(alias="fromDate")
    count: int = Field(ge=1, le=_MAX_OBLIGATION_COUNT)


class CreateObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    obligation_id: UUID | None = Field(default=None, alias="obligationId")
    observation_type_vi: str = Field(
        alias="observationType", min_length=1, max_length=200
    )
    body_vi: str = Field(alias="body", min_length=1, max_length=8000)
    effective_at: datetime = Field(alias="effectiveAt")
    observed_at: datetime = Field(alias="observedAt")
    evidence_refs: tuple[str, ...] | None = Field(default=None, alias="evidenceRefs")


class CreateCovenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name_vi: str = Field(alias="name", min_length=1, max_length=400)
    metric_key: str = Field(alias="metricKey", min_length=1, max_length=200)
    operator: ComparisonOperator
    threshold_value: Decimal = Field(alias="thresholdValue")
    threshold_version: int = Field(alias="thresholdVersion", ge=1)


class RunCovenantTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    numerator: Decimal
    #: Defaults to 1 so an absolute-value covenant needs no denominator; must be
    #: strictly positive (the exact cross-multiplied comparison needs it).
    denominator: Decimal = Field(default=Decimal(1), gt=0)


class DisposeAlertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to_status: str = Field(alias="toStatus", min_length=1, max_length=64)
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


# -- response models ----------------------------------------------------------


class ObligationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    sequence: int
    frequency: str
    due_date: date = Field(serialization_alias="dueDate")
    requirement_text_vi: str = Field(serialization_alias="requirementText")
    created_at: datetime = Field(serialization_alias="createdAt")


class ObligationsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    obligations: list[ObligationResponse]
    case_version: int = Field(serialization_alias="caseVersion")


class ObservationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    obligation_id: UUID | None = Field(serialization_alias="obligationId")
    observation_type_vi: str = Field(serialization_alias="observationType")
    body_vi: str = Field(serialization_alias="body")
    effective_at: datetime = Field(serialization_alias="effectiveAt")
    observed_at: datetime = Field(serialization_alias="observedAt")
    recorded_at: datetime = Field(serialization_alias="recordedAt")
    evidence_refs: list[str] = Field(serialization_alias="evidenceRefs")


class AlertResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    rule: str
    status: str
    detail_vi: str = Field(serialization_alias="detail")
    source_covenant_test_id: UUID | None = Field(
        serialization_alias="sourceCovenantTestId"
    )
    source_obligation_id: UUID | None = Field(serialization_alias="sourceObligationId")
    source_observation_id: UUID | None = Field(
        serialization_alias="sourceObservationId"
    )
    created_at: datetime = Field(serialization_alias="createdAt")


class RecordObservationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    observation: ObservationResponse
    #: The OVERDUE_OBLIGATION alert this observation raised, or null if on time.
    alert: AlertResponse | None


class ObservationsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    observations: list[ObservationResponse]
    case_version: int = Field(serialization_alias="caseVersion")


class CovenantResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    name_vi: str = Field(serialization_alias="name")
    metric_key: str = Field(serialization_alias="metricKey")
    operator: str
    threshold_value: Decimal = Field(serialization_alias="thresholdValue")
    threshold_version: int = Field(serialization_alias="thresholdVersion")
    created_at: datetime = Field(serialization_alias="createdAt")


class CovenantsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    covenants: list[CovenantResponse]
    case_version: int = Field(serialization_alias="caseVersion")


class CovenantTestResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    covenant_id: UUID = Field(serialization_alias="covenantId")
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    metric_key: str = Field(serialization_alias="metricKey")
    operator: str
    numerator: Decimal
    denominator: Decimal
    threshold_value: Decimal = Field(serialization_alias="thresholdValue")
    threshold_version: int = Field(serialization_alias="thresholdVersion")
    comparison_lhs: Decimal = Field(serialization_alias="comparisonLhs")
    comparison_rhs: Decimal = Field(serialization_alias="comparisonRhs")
    passed: bool
    recorded_at: datetime = Field(serialization_alias="recordedAt")


class RecordCovenantTestResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    test: CovenantTestResponse
    #: The COVENANT_BREACH alert this test raised, or null if it passed.
    alert: AlertResponse | None


class CovenantTestsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    tests: list[CovenantTestResponse]
    case_version: int = Field(serialization_alias="caseVersion")


class AlertsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    alerts: list[AlertResponse]
    case_version: int = Field(serialization_alias="caseVersion")


Actor = Annotated[ActorContext, Depends(require_actor)]


# -- wiring helpers -----------------------------------------------------------


def _require_role(actor: ActorContext, role: str) -> None:
    if role not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò được yêu cầu cho thao tác này.",
        )


def _require_reader(actor: ActorContext) -> None:
    if not (_READ_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> MonitoringRepository:
    repository = getattr(request.app.state, "monitoring_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="MONITORING_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ giám sát sau cấp tín dụng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(MonitoringRepository, repository)


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> CaseRecord:
    """Return the assigned case record, or fail closed with an indistinguishable
    404 for an unassigned actor (assignment membership is never disclosed)."""

    uow_factory = getattr(request.app.state, "uow_factory", None)
    if uow_factory is None:
        raise ApiException(
            status_code=503,
            code="CASE_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ hồ sơ chưa sẵn sàng.",
            retryable=True,
        )
    async with uow_factory(actor) as uow:
        record = await uow.cases.get_assigned(case_id, actor.actor_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
        )
    return cast(CaseRecord, record)


# -- response mappers ---------------------------------------------------------


def _obligation_response(obligation: RecordedObligation) -> ObligationResponse:
    return ObligationResponse(
        id=obligation.id,
        case_id=obligation.case_id,
        case_version=obligation.case_version,
        sequence=obligation.sequence,
        frequency=obligation.frequency.value,
        due_date=obligation.due_date,
        requirement_text_vi=obligation.requirement_text_vi,
        created_at=obligation.created_at,
    )


def _observation_response(observation: RecordedObservation) -> ObservationResponse:
    return ObservationResponse(
        id=observation.id,
        case_id=observation.case_id,
        case_version=observation.case_version,
        obligation_id=observation.obligation_id,
        observation_type_vi=observation.observation_type_vi,
        body_vi=observation.body_vi,
        effective_at=observation.effective_at,
        observed_at=observation.observed_at,
        recorded_at=observation.recorded_at,
        evidence_refs=list(observation.evidence_refs),
    )


def _covenant_response(covenant: RecordedCovenant) -> CovenantResponse:
    return CovenantResponse(
        id=covenant.id,
        case_id=covenant.case_id,
        case_version=covenant.case_version,
        name_vi=covenant.name_vi,
        metric_key=covenant.metric_key,
        operator=covenant.operator.value,
        threshold_value=covenant.threshold_value,
        threshold_version=covenant.threshold_version,
        created_at=covenant.created_at,
    )


def _covenant_test_response(test: RecordedCovenantTest) -> CovenantTestResponse:
    return CovenantTestResponse(
        id=test.id,
        covenant_id=test.covenant_id,
        case_id=test.case_id,
        case_version=test.case_version,
        metric_key=test.metric_key,
        operator=test.operator.value,
        numerator=test.numerator,
        denominator=test.denominator,
        threshold_value=test.threshold_value,
        threshold_version=test.threshold_version,
        comparison_lhs=test.comparison_lhs,
        comparison_rhs=test.comparison_rhs,
        passed=test.passed,
        recorded_at=test.recorded_at,
    )


def _alert_response(alert: RecordedAlert) -> AlertResponse:
    return AlertResponse(
        id=alert.id,
        case_id=alert.case_id,
        case_version=alert.case_version,
        rule=alert.rule.value,
        status=alert.status.value,
        detail_vi=alert.detail_vi,
        source_covenant_test_id=alert.source_covenant_test_id,
        source_obligation_id=alert.source_obligation_id,
        source_observation_id=alert.source_observation_id,
        created_at=alert.created_at,
    )


# -- obligations --------------------------------------------------------------


@router.post("/obligations", response_model=ObligationsResponse, status_code=201)
async def create_obligations(
    case_id: UUID,
    body: CreateObligationsRequest,
    actor: Actor,
    request: Request,
) -> ObligationsResponse:
    """Deterministically generate + persist a run of monitoring obligations."""

    _require_role(actor, MONITORING_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)

    spec = ObligationSpec(
        frequency=body.frequency, requirement_text_vi=body.requirement_text_vi
    )
    obligations = generate_obligations(spec, body.from_date, body.count)
    created = await _repository(request).create_obligations(
        case_id=case_id,
        case_version=record.version,
        spec=spec,
        obligations=obligations,
        actor_id=actor.actor_id,
        actor_role=MONITORING_OFFICER_ROLE,
    )
    return ObligationsResponse(
        obligations=[_obligation_response(o) for o in created],
        case_version=record.version,
    )


@router.get("/obligations", response_model=ObligationsResponse)
async def list_obligations(
    case_id: UUID, actor: Actor, request: Request
) -> ObligationsResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    obligations = await _repository(request).list_obligations(case_id, record.version)
    return ObligationsResponse(
        obligations=[_obligation_response(o) for o in obligations],
        case_version=record.version,
    )


# -- observations -------------------------------------------------------------


@router.post("/observations", response_model=RecordObservationResponse, status_code=201)
async def record_observation(
    case_id: UUID,
    body: CreateObservationRequest,
    actor: Actor,
    request: Request,
) -> RecordObservationResponse:
    """Record ONE longitudinal observation; the OVERDUE_OBLIGATION rule may fire."""

    _require_role(actor, MONITORING_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    try:
        observation = MonitoringObservation(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            obligation_id=body.obligation_id,
            observation_type_vi=body.observation_type_vi,
            body_vi=body.body_vi,
            effective_at=body.effective_at,
            observed_at=body.observed_at,
            evidence_refs=body.evidence_refs or (),
        )
    except (ValidationError, ValueError) as exc:
        # The one deterministic temporal invariant: effectiveAt <= observedAt.
        raise ApiException(
            status_code=422,
            code="INVALID_OBSERVATION",
            message_vi="Quan sát không hợp lệ (effectiveAt phải <= observedAt).",
        ) from exc

    overdue_alert: EarlyWarningAlert | None = None
    if body.obligation_id is not None:
        obligation = await repository.load_obligation(
            body.obligation_id, case_id, record.version
        )
        if obligation is None:
            raise ApiException(
                status_code=404,
                code="OBLIGATION_NOT_FOUND",
                message_vi="Không tìm thấy nghĩa vụ giám sát trong hồ sơ này.",
            )
        # DETERMINISTIC RULE OVERDUE_OBLIGATION: a late observation raises an alert.
        if obligation_overdue(obligation.due_date, observation.observed_at):
            overdue_alert = EarlyWarningAlert(
                id=uuid4(),
                case_id=case_id,
                case_version=record.version,
                rule=AlertRule.OVERDUE_OBLIGATION,
                detail_vi=build_overdue_detail(
                    obligation.due_date, observation.observed_at
                ),
                source_obligation_id=obligation.id,
                source_observation_id=observation.id,
            )

    recorded, alert = await repository.record_observation(
        observation=observation,
        overdue_alert=overdue_alert,
        actor_id=actor.actor_id,
        actor_role=MONITORING_OFFICER_ROLE,
    )
    return RecordObservationResponse(
        observation=_observation_response(recorded),
        alert=_alert_response(alert) if alert is not None else None,
    )


@router.get("/observations", response_model=ObservationsResponse)
async def list_observations(
    case_id: UUID, actor: Actor, request: Request
) -> ObservationsResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    observations = await _repository(request).list_observations(case_id, record.version)
    return ObservationsResponse(
        observations=[_observation_response(o) for o in observations],
        case_version=record.version,
    )


# -- covenants ----------------------------------------------------------------


@router.post("/covenants", response_model=CovenantResponse, status_code=201)
async def create_covenant(
    case_id: UUID,
    body: CreateCovenantRequest,
    actor: Actor,
    request: Request,
) -> CovenantResponse:
    """Declare a covenant carrying its own versioned, human-supplied threshold."""

    _require_role(actor, MONITORING_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)

    try:
        covenant = Covenant(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            name_vi=body.name_vi,
            threshold=CovenantThreshold(
                metric_key=body.metric_key,
                operator=body.operator,
                threshold_value=body.threshold_value,
                threshold_version=body.threshold_version,
            ),
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_COVENANT",
            message_vi="Cam kết không hợp lệ.",
        ) from exc

    created = await _repository(request).create_covenant(
        covenant=covenant, actor_id=actor.actor_id, actor_role=MONITORING_OFFICER_ROLE
    )
    return _covenant_response(created)


@router.get("/covenants", response_model=CovenantsResponse)
async def list_covenants(
    case_id: UUID, actor: Actor, request: Request
) -> CovenantsResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    covenants = await _repository(request).list_covenants(case_id, record.version)
    return CovenantsResponse(
        covenants=[_covenant_response(c) for c in covenants],
        case_version=record.version,
    )


@router.post(
    "/covenants/{covenant_id}/test",
    response_model=RecordCovenantTestResponse,
    status_code=201,
)
async def run_covenant_test(
    case_id: UUID,
    covenant_id: UUID,
    body: RunCovenantTestRequest,
    actor: Actor,
    request: Request,
) -> RecordCovenantTestResponse:
    """Test supplied inputs against the covenant threshold; COVENANT_BREACH may fire."""

    _require_role(actor, MONITORING_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    covenant = await repository.load_covenant(covenant_id, case_id, record.version)
    if covenant is None:
        raise ApiException(
            status_code=404,
            code="COVENANT_NOT_FOUND",
            message_vi="Không tìm thấy cam kết trong hồ sơ này.",
        )

    threshold = CovenantThreshold(
        metric_key=covenant.metric_key,
        operator=covenant.operator,
        threshold_value=covenant.threshold_value,
        threshold_version=covenant.threshold_version,
    )
    try:
        evaluation = evaluate_covenant(body.numerator, body.denominator, threshold)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_COVENANT_TEST",
            message_vi="Đầu vào kiểm tra cam kết không hợp lệ.",
        ) from exc

    test_id = uuid4()
    breach_alert: EarlyWarningAlert | None = None
    # DETERMINISTIC RULE COVENANT_BREACH: a failed declared comparison raises an alert.
    if covenant_breach_detected(evaluation):
        breach_alert = EarlyWarningAlert(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            rule=AlertRule.COVENANT_BREACH,
            detail_vi=build_breach_detail(evaluation),
            source_covenant_test_id=test_id,
        )

    test, alert = await repository.record_covenant_test(
        test_id=test_id,
        covenant_id=covenant.id,
        case_id=case_id,
        case_version=record.version,
        evaluation=evaluation,
        breach_alert=breach_alert,
        actor_id=actor.actor_id,
        actor_role=MONITORING_OFFICER_ROLE,
    )
    return RecordCovenantTestResponse(
        test=_covenant_test_response(test),
        alert=_alert_response(alert) if alert is not None else None,
    )


@router.get("/covenant-tests", response_model=CovenantTestsResponse)
async def list_covenant_tests(
    case_id: UUID, actor: Actor, request: Request
) -> CovenantTestsResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    tests = await _repository(request).list_covenant_tests(case_id, record.version)
    return CovenantTestsResponse(
        tests=[_covenant_test_response(t) for t in tests],
        case_version=record.version,
    )


# -- alerts -------------------------------------------------------------------


@router.get("/alerts", response_model=AlertsResponse)
async def list_alerts(case_id: UUID, actor: Actor, request: Request) -> AlertsResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    alerts = await _repository(request).list_alerts(case_id, record.version)
    return AlertsResponse(
        alerts=[_alert_response(a) for a in alerts],
        case_version=record.version,
    )


@router.post("/alerts/{alert_id}/disposition", response_model=AlertResponse)
async def dispose_alert(
    case_id: UUID,
    alert_id: UUID,
    body: DisposeAlertRequest,
    actor: Actor,
    request: Request,
) -> AlertResponse:
    """Human-only alert disposition along a validated lifecycle edge (rationale required)."""

    _require_role(actor, MONITORING_REVIEWER_ROLE)

    try:
        to_status = AlertStatus(body.to_status)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_ALERT_STATUS",
            message_vi="Trạng thái cảnh báo không hợp lệ.",
        ) from exc

    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    current = await repository.load_alert(alert_id, case_id, record.version)
    if current is None:
        raise ApiException(
            status_code=404,
            code="ALERT_NOT_FOUND",
            message_vi="Không tìm thấy cảnh báo trong hồ sơ này.",
        )
    if not is_alert_transition_allowed(current.status, to_status):
        raise ApiException(
            status_code=422,
            code="FORBIDDEN_ALERT_TRANSITION",
            message_vi="Chuyển trạng thái cảnh báo không được phép.",
            details={
                "fromStatus": current.status.value,
                "toStatus": to_status.value,
            },
        )

    try:
        updated = await repository.dispose_alert(
            alert_id=alert_id,
            case_id=case_id,
            case_version=record.version,
            to_status=to_status,
            rationale_vi=body.rationale_vi,
            actor_id=actor.actor_id,
            actor_role=MONITORING_REVIEWER_ROLE,
        )
    except AlertNotFound as exc:
        raise ApiException(
            status_code=404,
            code="ALERT_NOT_FOUND",
            message_vi="Không tìm thấy cảnh báo trong hồ sơ này.",
        ) from exc
    except ForbiddenAlertTransition as exc:
        # Lost race: the alert moved between the pre-check and the write.
        raise ApiException(
            status_code=422,
            code="FORBIDDEN_ALERT_TRANSITION",
            message_vi="Chuyển trạng thái cảnh báo không được phép.",
        ) from exc
    return _alert_response(updated)
