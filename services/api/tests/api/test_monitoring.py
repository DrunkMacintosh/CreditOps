"""Role-gated API tests for the stage-12 post-credit monitoring surfaces.

Human-only authority: obligations / observations / covenants / covenant tests
require ``MONITORING_OFFICER``; alert disposition requires ``MONITORING_REVIEWER``
and a mandatory rationale.  Early-warning alerts are raised only by the two
deterministic rules (COVENANT_BREACH on a failed covenant test, OVERDUE_OBLIGATION
on a late observation) -- never by a model.  The routers are mounted onto the app
built by ``create_app`` here (``main.py`` wiring is a deferred lead decision), and
the repository is injected directly.

All customer data in this project is synthetic and created solely for
demonstration; the fixture case belongs to the invented SME "Cong ty TNHH Nong San
Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.monitoring import (
    MONITORING_OFFICER_ROLE,
    MONITORING_REVIEWER_ROLE,
)
from creditops.api.monitoring import router as monitoring_router
from creditops.application.ports.monitoring import (
    RecordedAlert,
    RecordedCovenant,
    RecordedCovenantTest,
    RecordedObligation,
    RecordedObservation,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.monitoring import (
    AlertRule,
    AlertStatus,
    Covenant,
    CovenantEvaluation,
    EarlyWarningAlert,
    GeneratedObligation,
    MonitoringObservation,
    ObligationSpec,
)
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REVIEWER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CASE_ID = UUID("10000000-0000-0000-0000-0000000000f1")
ASSIGNED = frozenset({OFFICER, REVIEWER})
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class FakeMonitoringRepository:
    """In-memory monitoring store that mirrors the adapter's rule + dedup contract.

    ``record_observation`` / ``record_covenant_test`` persist the row and, when the
    API passes the alert its deterministic rule raised, store it too -- deduped per
    source exactly as the Postgres adapter does with its partial unique indexes.
    """

    def __init__(self) -> None:
        self.obligations: dict[UUID, RecordedObligation] = {}
        self.observations: dict[UUID, RecordedObservation] = {}
        self.covenants: dict[UUID, RecordedCovenant] = {}
        self.covenant_tests: dict[UUID, RecordedCovenantTest] = {}
        self.alerts: dict[UUID, RecordedAlert] = {}

    async def create_obligations(
        self,
        *,
        case_id: UUID,
        case_version: int,
        spec: ObligationSpec,
        obligations: tuple[GeneratedObligation, ...],
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedObligation, ...]:
        created: list[RecordedObligation] = []
        for obligation in obligations:
            record = RecordedObligation(
                id=uuid4(),
                case_id=case_id,
                case_version=case_version,
                sequence=obligation.sequence,
                frequency=obligation.frequency,
                due_date=obligation.due_date,
                requirement_text_vi=obligation.requirement_text_vi,
                created_at=NOW,
            )
            self.obligations[record.id] = record
            created.append(record)
        return tuple(created)

    async def list_obligations(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedObligation, ...]:
        return tuple(
            o
            for o in self.obligations.values()
            if o.case_id == case_id and o.case_version == case_version
        )

    async def load_obligation(
        self, obligation_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedObligation | None:
        o = self.obligations.get(obligation_id)
        if o is None or o.case_id != case_id or o.case_version != case_version:
            return None
        return o

    async def record_observation(
        self,
        *,
        observation: MonitoringObservation,
        overdue_alert: EarlyWarningAlert | None,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedObservation, RecordedAlert | None]:
        record = RecordedObservation(
            id=observation.id,
            case_id=observation.case_id,
            case_version=observation.case_version,
            obligation_id=observation.obligation_id,
            observation_type_vi=observation.observation_type_vi,
            body_vi=observation.body_vi,
            effective_at=observation.effective_at,
            observed_at=observation.observed_at,
            recorded_at=NOW,
            evidence_refs=observation.evidence_refs,
        )
        self.observations[record.id] = record
        alert = self._raise(overdue_alert, dedup_key="source_obligation_id")
        return record, alert

    async def list_observations(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedObservation, ...]:
        return tuple(
            o
            for o in self.observations.values()
            if o.case_id == case_id and o.case_version == case_version
        )

    async def create_covenant(
        self, *, covenant: Covenant, actor_id: UUID, actor_role: str
    ) -> RecordedCovenant:
        record = RecordedCovenant(
            id=covenant.id,
            case_id=covenant.case_id,
            case_version=covenant.case_version,
            name_vi=covenant.name_vi,
            metric_key=covenant.threshold.metric_key,
            operator=covenant.threshold.operator,
            threshold_value=covenant.threshold.threshold_value,
            threshold_version=covenant.threshold.threshold_version,
            created_at=NOW,
        )
        self.covenants[record.id] = record
        return record

    async def list_covenants(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCovenant, ...]:
        return tuple(
            c
            for c in self.covenants.values()
            if c.case_id == case_id and c.case_version == case_version
        )

    async def load_covenant(
        self, covenant_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedCovenant | None:
        c = self.covenants.get(covenant_id)
        if c is None or c.case_id != case_id or c.case_version != case_version:
            return None
        return c

    async def record_covenant_test(
        self,
        *,
        test_id: UUID,
        covenant_id: UUID,
        case_id: UUID,
        case_version: int,
        evaluation: CovenantEvaluation,
        breach_alert: EarlyWarningAlert | None,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedCovenantTest, RecordedAlert | None]:
        record = RecordedCovenantTest(
            id=test_id,
            covenant_id=covenant_id,
            case_id=case_id,
            case_version=case_version,
            metric_key=evaluation.metric_key,
            operator=evaluation.operator,
            numerator=evaluation.numerator,
            denominator=evaluation.denominator,
            threshold_value=evaluation.threshold_value,
            threshold_version=evaluation.threshold_version,
            comparison_lhs=evaluation.comparison_lhs,
            comparison_rhs=evaluation.comparison_rhs,
            passed=evaluation.passed,
            recorded_at=NOW,
        )
        self.covenant_tests[record.id] = record
        alert = self._raise(breach_alert, dedup_key="source_covenant_test_id")
        return record, alert

    async def list_covenant_tests(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCovenantTest, ...]:
        return tuple(
            t
            for t in self.covenant_tests.values()
            if t.case_id == case_id and t.case_version == case_version
        )

    async def list_alerts(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedAlert, ...]:
        return tuple(
            a
            for a in self.alerts.values()
            if a.case_id == case_id and a.case_version == case_version
        )

    async def load_alert(
        self, alert_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedAlert | None:
        a = self.alerts.get(alert_id)
        if a is None or a.case_id != case_id or a.case_version != case_version:
            return None
        return a

    async def dispose_alert(
        self,
        *,
        alert_id: UUID,
        case_id: UUID,
        case_version: int,
        to_status: AlertStatus,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedAlert:
        current = self.alerts[alert_id]
        updated = RecordedAlert(
            id=current.id,
            case_id=current.case_id,
            case_version=current.case_version,
            rule=current.rule,
            status=to_status,
            detail_vi=current.detail_vi,
            source_covenant_test_id=current.source_covenant_test_id,
            source_obligation_id=current.source_obligation_id,
            source_observation_id=current.source_observation_id,
            created_at=current.created_at,
        )
        self.alerts[alert_id] = updated
        return updated

    def _raise(
        self, alert: EarlyWarningAlert | None, *, dedup_key: str
    ) -> RecordedAlert | None:
        if alert is None:
            return None
        source = getattr(alert, dedup_key)
        for existing in self.alerts.values():
            if getattr(existing, dedup_key) == source:
                return None  # per-source dedup, mirroring the adapter
        record = RecordedAlert(
            id=alert.id,
            case_id=alert.case_id,
            case_version=alert.case_version,
            rule=alert.rule,
            status=alert.status,
            detail_vi=alert.detail_vi,
            source_covenant_test_id=alert.source_covenant_test_id,
            source_obligation_id=alert.source_obligation_id,
            source_observation_id=alert.source_observation_id,
            created_at=NOW,
        )
        self.alerts[record.id] = record
        return record


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id not in ASSIGNED:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=actor_id,
            requested_amount="5000000000",
            purpose_vi="Vốn lưu động cho nông sản",
            created_at=NOW,
        )


class FakeUnitOfWork:
    cases = FakeCases()

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_client(
    signing_key: rsa.RSAPrivateKey, *, repository: FakeMonitoringRepository
) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=JwksKeyResolver({"keys": [jwk]})
    )
    application = create_app(
        settings=Settings(app_env="test"),
        jwt_verifier=verifier,
        uow_factory=lambda actor: FakeUnitOfWork(),
    )
    application.include_router(monitoring_router)
    application.state.monitoring_repository = repository
    return TestClient(application)


def token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(subject),
            "roles": roles or [MONITORING_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _url() -> str:
    return f"/api/v1/cases/{CASE_ID}/monitoring"


def _officer(signing_key: rsa.RSAPrivateKey) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, roles=[MONITORING_OFFICER_ROLE])}"}


def _reviewer(signing_key: rsa.RSAPrivateKey) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {token(signing_key, subject=REVIEWER, roles=[MONITORING_REVIEWER_ROLE])}"
        )
    }


def _create_obligation(client: TestClient, signing_key: rsa.RSAPrivateKey) -> str:
    response = client.post(
        f"{_url()}/obligations",
        json={
            "frequency": "MONTHLY",
            "requirementText": "Nộp báo cáo tài chính hàng tháng (mô phỏng).",
            "fromDate": "2026-01-31",
            "count": 1,
        },
        headers=_officer(signing_key),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["obligations"][0]["id"])


def _create_covenant(
    client: TestClient, signing_key: rsa.RSAPrivateKey, *, operator: str = "GTE"
) -> str:
    response = client.post(
        f"{_url()}/covenants",
        json={
            "name": "Hệ số bao phủ nợ (mô phỏng).",
            "metricKey": "DSCR",
            "operator": operator,
            "thresholdValue": "1.20",
            "thresholdVersion": 1,
        },
        headers=_officer(signing_key),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# -- obligations --------------------------------------------------------------


def test_generate_obligations_is_deterministic_schedule(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/obligations",
        json={
            "frequency": "MONTHLY",
            "requirementText": "Báo cáo tồn kho (mô phỏng).",
            "fromDate": "2026-01-31",
            "count": 3,
        },
        headers=_officer(signing_key),
    )
    assert response.status_code == 201
    due = [o["dueDate"] for o in response.json()["obligations"]]
    assert due == ["2026-02-28", "2026-03-31", "2026-04-30"]


def test_generate_obligations_requires_officer(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/obligations",
        json={
            "frequency": "MONTHLY",
            "requirementText": "x",
            "fromDate": "2026-01-31",
            "count": 1,
        },
        headers=_reviewer(signing_key),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_generate_obligations_unassigned_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/obligations",
        json={
            "frequency": "MONTHLY",
            "requirementText": "x",
            "fromDate": "2026-01-31",
            "count": 1,
        },
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, subject=uuid4(), roles=[MONITORING_OFFICER_ROLE])}"
            )
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


# -- observations + OVERDUE_OBLIGATION rule -----------------------------------


def test_on_time_observation_raises_no_alert(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    obligation_id = _create_obligation(client, signing_key)  # due 2026-02-28

    response = client.post(
        f"{_url()}/observations",
        json={
            "obligationId": obligation_id,
            "observationType": "Báo cáo tài chính",
            "body": "Đã nộp đúng hạn (mô phỏng).",
            "effectiveAt": "2026-02-20T00:00:00Z",
            "observedAt": "2026-02-25T09:00:00Z",
        },
        headers=_officer(signing_key),
    )

    assert response.status_code == 201
    assert response.json()["alert"] is None


def test_late_observation_raises_overdue_alert(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    obligation_id = _create_obligation(client, signing_key)  # due 2026-02-28

    response = client.post(
        f"{_url()}/observations",
        json={
            "obligationId": obligation_id,
            "observationType": "Báo cáo tài chính",
            "body": "Nộp muộn (mô phỏng).",
            "effectiveAt": "2026-03-01T00:00:00Z",
            "observedAt": "2026-03-05T09:00:00Z",
        },
        headers=_officer(signing_key),
    )

    assert response.status_code == 201
    alert = response.json()["alert"]
    assert alert is not None
    assert alert["rule"] == AlertRule.OVERDUE_OBLIGATION.value
    assert alert["status"] == AlertStatus.OPEN.value
    assert alert["sourceObligationId"] == obligation_id


def test_second_late_observation_is_deduped(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    obligation_id = _create_obligation(client, signing_key)
    late = {
        "obligationId": obligation_id,
        "observationType": "Báo cáo tài chính",
        "body": "Nộp muộn (mô phỏng).",
        "effectiveAt": "2026-03-01T00:00:00Z",
        "observedAt": "2026-03-05T09:00:00Z",
    }
    first = client.post(f"{_url()}/observations", json=late, headers=_officer(signing_key))
    second = client.post(f"{_url()}/observations", json=late, headers=_officer(signing_key))

    assert first.json()["alert"] is not None
    # The obligation already carries an OVERDUE alert; a second late observation
    # does not re-raise it.
    assert second.json()["alert"] is None


def test_observation_rejects_effective_after_observed(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/observations",
        json={
            "observationType": "Báo cáo tài chính",
            "body": "Thứ tự thời gian sai (mô phỏng).",
            "effectiveAt": "2026-03-10T00:00:00Z",
            "observedAt": "2026-03-05T09:00:00Z",
        },
        headers=_officer(signing_key),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_OBSERVATION"


def test_observation_on_missing_obligation_is_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/observations",
        json={
            "obligationId": str(uuid4()),
            "observationType": "Báo cáo tài chính",
            "body": "Nghĩa vụ không tồn tại (mô phỏng).",
            "effectiveAt": "2026-03-01T00:00:00Z",
            "observedAt": "2026-03-05T09:00:00Z",
        },
        headers=_officer(signing_key),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "OBLIGATION_NOT_FOUND"


# -- covenants + COVENANT_BREACH rule -----------------------------------------


def test_passing_covenant_test_raises_no_alert(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    covenant_id = _create_covenant(client, signing_key)

    response = client.post(
        f"{_url()}/covenants/{covenant_id}/test",
        json={"numerator": "1000", "denominator": "800"},  # 1.25 >= 1.20
        headers=_officer(signing_key),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["test"]["passed"] is True
    assert body["test"]["comparisonRhs"] == "960.00"
    assert body["alert"] is None


def test_failing_covenant_test_raises_breach_alert(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    covenant_id = _create_covenant(client, signing_key)

    response = client.post(
        f"{_url()}/covenants/{covenant_id}/test",
        json={"numerator": "1000", "denominator": "900"},  # ~1.11 < 1.20
        headers=_officer(signing_key),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["test"]["passed"] is False
    alert = body["alert"]
    assert alert is not None
    assert alert["rule"] == AlertRule.COVENANT_BREACH.value
    assert alert["sourceCovenantTestId"] == body["test"]["id"]


def test_covenant_test_on_missing_covenant_is_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/covenants/{uuid4()}/test",
        json={"numerator": "1", "denominator": "1"},
        headers=_officer(signing_key),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "COVENANT_NOT_FOUND"


def test_covenant_test_rejects_non_positive_denominator(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    covenant_id = _create_covenant(client, signing_key)

    response = client.post(
        f"{_url()}/covenants/{covenant_id}/test",
        json={"numerator": "1", "denominator": "0"},
        headers=_officer(signing_key),
    )
    # The request-model gt=0 constraint fires first.
    assert response.status_code == 422


# -- alert disposition (human-only, rationale required) -----------------------


def _raise_breach_alert(client: TestClient, signing_key: rsa.RSAPrivateKey) -> str:
    covenant_id = _create_covenant(client, signing_key)
    response = client.post(
        f"{_url()}/covenants/{covenant_id}/test",
        json={"numerator": "1000", "denominator": "900"},
        headers=_officer(signing_key),
    )
    return str(response.json()["alert"]["id"])


def test_dispose_alert_requires_reviewer(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    alert_id = _raise_breach_alert(client, signing_key)

    response = client.post(
        f"{_url()}/alerts/{alert_id}/disposition",
        json={"toStatus": "ACKNOWLEDGED", "rationale": "Đã xem xét (mô phỏng)."},
        headers=_officer(signing_key),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_dispose_alert_requires_rationale(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    alert_id = _raise_breach_alert(client, signing_key)

    response = client.post(
        f"{_url()}/alerts/{alert_id}/disposition",
        json={"toStatus": "ACKNOWLEDGED"},
        headers=_reviewer(signing_key),
    )
    assert response.status_code == 422  # rationale is a required field


def test_dispose_alert_succeeds(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    alert_id = _raise_breach_alert(client, signing_key)

    response = client.post(
        f"{_url()}/alerts/{alert_id}/disposition",
        json={"toStatus": "ESCALATED", "rationale": "Chuyển cấp cao hơn (mô phỏng)."},
        headers=_reviewer(signing_key),
    )
    assert response.status_code == 200
    assert response.json()["status"] == AlertStatus.ESCALATED.value


def test_dispose_alert_forbidden_transition_is_422(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    alert_id = _raise_breach_alert(client, signing_key)
    client.post(
        f"{_url()}/alerts/{alert_id}/disposition",
        json={"toStatus": "DISMISSED_BY_HUMAN", "rationale": "Đóng (mô phỏng)."},
        headers=_reviewer(signing_key),
    )

    # DISMISSED_BY_HUMAN is terminal: no further transition.
    response = client.post(
        f"{_url()}/alerts/{alert_id}/disposition",
        json={"toStatus": "ESCALATED", "rationale": "Cố mở lại (mô phỏng)."},
        headers=_reviewer(signing_key),
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "FORBIDDEN_ALERT_TRANSITION"
    assert body["details"]["fromStatus"] == "DISMISSED_BY_HUMAN"


def test_dispose_missing_alert_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/alerts/{uuid4()}/disposition",
        json={"toStatus": "ACKNOWLEDGED", "rationale": "x"},
        headers=_reviewer(signing_key),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "ALERT_NOT_FOUND"


def test_invalid_alert_status_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeMonitoringRepository())
    response = client.post(
        f"{_url()}/alerts/{uuid4()}/disposition",
        json={"toStatus": "NOT_A_STATUS", "rationale": "x"},
        headers=_reviewer(signing_key),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_ALERT_STATUS"


# -- list surfaces ------------------------------------------------------------


def test_list_surfaces_are_reader_gated(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeMonitoringRepository()
    client = _build_client(signing_key, repository=repository)
    _create_obligation(client, signing_key)
    _raise_breach_alert(client, signing_key)

    for path in ("obligations", "observations", "covenants", "covenant-tests", "alerts"):
        response = client.get(f"{_url()}/{path}", headers=_reviewer(signing_key))
        assert response.status_code == 200, path

    alerts = client.get(f"{_url()}/alerts", headers=_reviewer(signing_key)).json()
    assert len(alerts["alerts"]) == 1
    assert alerts["caseVersion"] == 1
