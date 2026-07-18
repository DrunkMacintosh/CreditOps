"""Role-gated API tests for GET/POST /cases/{id}/credit-ops.

Requirements exercised: (c) missing approval blocks the action -- with no
authorization record the action stays DRAFT and G4 stays OPEN; the authorize
endpoint is restricted to OPS_OFFICER; a nonexistent/foreign action 404s
without a capability leak; (e) every authorization/approval write emits an
audit event; G2 derives from document-request approvals.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.application.orchestration.roles import OPS_OFFICER_ROLE
from creditops.application.ports.credit_ops import (
    ActionAuthorizationRecord,
    DocumentRequestApprovalRecord,
    LatestCreditOpsPackageRecord,
)
from creditops.application.ports.orchestration import (
    GateRecord,
    OrchestrationAuditEvent,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.orchestration import GateStatus, GateType
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000003")
PACKAGE_ID = UUID("50000000-0000-0000-0000-000000000003")
HANDOFF_ID = UUID("60000000-0000-0000-0000-000000000003")
ACTION_A = UUID("70000000-0000-0000-0000-000000000003")
ACTION_B = UUID("70000000-0000-0000-0000-000000000004")
REQUEST_A = UUID("71000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 7, 18, 12, 30, tzinfo=UTC)


def _package_payload(*, with_request: bool = True) -> dict[str, Any]:
    return {
        "package_completeness": {"all_required_present": True},
        "evidence_consolidation": {"entries": [], "distinct_citation_count": 0},
        "draft_memo": {"synthetic_disclaimer_vi": "Du lieu tong hop (mo phong)."},
        "document_requests": (
            [
                {
                    "id": str(REQUEST_A),
                    "originating_gap_id": str(uuid4()),
                    "request_text_vi": "De nghi bo sung tai lieu (mo phong).",
                    "blocking_level": "CONDITIONAL",
                    "approval_status": "PENDING_APPROVAL",
                }
            ]
            if with_request
            else []
        ),
        "proposed_actions": [
            {
                "id": str(ACTION_A),
                "action_type": "PREPARE_DOCUMENT_REQUEST",
                "description_vi": "Soan thao yeu cau bo sung (mo phong).",
                "execution_status": "DRAFT",
                "required_authorization": {"gate": "G4_OPS_AUTHORIZATION", "role": "OPS_OFFICER"},
            },
            {
                "id": str(ACTION_B),
                "action_type": "PREPARE_HANDOFF_PACKAGE",
                "description_vi": "Chuan bi goi ban giao (mo phong).",
                "execution_status": "DRAFT",
                "required_authorization": {"gate": "G4_OPS_AUTHORIZATION", "role": "OPS_OFFICER"},
            },
        ],
    }


class FakeCreditOpsRepository:
    def __init__(self, *, has_package: bool = True, with_request: bool = True) -> None:
        self.has_package = has_package
        self.with_request = with_request
        self.authorizations: list[ActionAuthorizationRecord] = []
        self.approvals: list[DocumentRequestApprovalRecord] = []
        self.audit_events: list[OrchestrationAuditEvent] = []

    async def load_upstream_view(self, case_id: UUID) -> Any:
        raise AssertionError("not used by the API")

    async def load_open_gaps(self, case_id: UUID, case_version: int) -> Any:
        raise AssertionError("not used by the API")

    async def load_dispositions(self, case_id: UUID, case_version: int) -> Any:
        raise AssertionError("not used by the API")

    async def load_latest_package(
        self, case_id: UUID
    ) -> LatestCreditOpsPackageRecord | None:
        if case_id != CASE_ID or not self.has_package:
            return None
        return LatestCreditOpsPackageRecord(
            package_id=PACKAGE_ID,
            case_id=CASE_ID,
            case_version=1,
            execution_id=uuid4(),
            agent_role="CREDIT_OPERATIONS",
            prompt_version="credit-ops-prompt-v1",
            created_at=NOW,
            package=_package_payload(with_request=self.with_request),
            handoff_id=HANDOFF_ID,
            handoff_state="READY_FOR_HUMAN_DECISION",
            handoff_created_at=NOW,
        )

    async def load_action_authorizations(
        self, package_id: UUID
    ) -> tuple[ActionAuthorizationRecord, ...]:
        return tuple(a for a in self.authorizations if a.package_id == package_id)

    async def load_document_request_approvals(
        self, package_id: UUID
    ) -> tuple[DocumentRequestApprovalRecord, ...]:
        return tuple(a for a in self.approvals if a.package_id == package_id)

    async def find_persisted(self, **kwargs: object) -> Any:
        raise AssertionError("not used by the API")

    async def persist_package(self, **kwargs: object) -> Any:
        raise AssertionError("the API must never write a package")

    async def record_action_authorization(
        self,
        *,
        authorization_id: UUID,
        package_id: UUID,
        action_id: UUID,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> ActionAuthorizationRecord:
        record = ActionAuthorizationRecord(
            id=authorization_id,
            package_id=package_id,
            action_id=action_id,
            actor_id=actor_id,
            actor_role=actor_role,
            rationale_vi=rationale_vi,
            created_at=NOW,
        )
        self.authorizations.append(record)
        return record

    async def record_document_request_approval(
        self,
        *,
        approval_id: UUID,
        package_id: UUID,
        request_id: UUID,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> DocumentRequestApprovalRecord:
        record = DocumentRequestApprovalRecord(
            id=approval_id,
            package_id=package_id,
            request_id=request_id,
            actor_id=actor_id,
            actor_role=actor_role,
            rationale_vi=rationale_vi,
            created_at=NOW,
        )
        self.approvals.append(record)
        return record

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        self.audit_events.append(event)


class FakeOrchestrationRepository:
    def __init__(self) -> None:
        self.ensure_gate_calls: list[dict[str, Any]] = []

    async def load_snapshot(self, case_id: UUID) -> Any:
        raise AssertionError("not used by the credit-ops API")

    async def ensure_gate(self, **kwargs: Any) -> GateRecord:
        self.ensure_gate_calls.append(kwargs)
        return GateRecord(
            gate_type=kwargs["gate_type"],
            case_version=kwargs["case_version"],
            status=kwargs["status"],
        )

    async def create_task(self, **kwargs: object) -> Any:
        raise AssertionError("not used by the credit-ops API")

    async def record_proposal(self, **kwargs: object) -> None:
        raise AssertionError("not used by the credit-ops API")

    async def append_audit(self, event: object) -> None:
        raise AssertionError("not used by the credit-ops API")


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=OFFICER_A,
            requested_amount="1",
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
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeCreditOpsRepository,
    orchestration_repository: FakeOrchestrationRepository | None = None,
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
    application.state.credit_ops_repository = repository
    application.state.orchestration_repository = orchestration_repository
    return TestClient(application)


def token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER_A,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(subject),
            "roles": roles or [OPS_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


# -- GET ----------------------------------------------------------------------


def test_participant_reads_latest_package_with_draft_actions(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(signing_key, repository=FakeCreditOpsRepository())
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/credit-ops",
        headers={"Authorization": f"Bearer {token(signing_key, roles=['INTAKE_OFFICER'])}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["packageId"] == str(PACKAGE_ID)
    assert body["agentRole"] == "CREDIT_OPERATIONS"
    assert body["handoff"]["state"] == "READY_FOR_HUMAN_DECISION"
    # (c) with no authorization record: every action stays DRAFT, un-authorized,
    # and G4 stays OPEN.
    assert len(body["proposedActions"]) == 2
    for action in body["proposedActions"]:
        assert action["executionStatus"] == "DRAFT"
        assert action["authorized"] is False
        assert action["authorizations"] == []
    assert body["g4GateStatus"] == "OPEN"
    # G2: one drafted request, unapproved.
    assert body["documentRequests"][0]["approvalStatus"] == "PENDING_APPROVAL"
    assert body["g2GateStatus"] == "OPEN"
    # No decision-capable field leaks through the read model.
    lowered = {key.lower() for key in body}
    assert not lowered & {"decision", "approved", "score", "waiver", "executed"}


def test_non_participant_role_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeCreditOpsRepository())
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/credit-ops",
        headers={"Authorization": f"Bearer {token(signing_key, roles=['AUDITOR'])}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_unassigned_actor_gets_indistinguishable_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(signing_key, repository=FakeCreditOpsRepository())
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/credit-ops",
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_no_package_yet_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(
        signing_key, repository=FakeCreditOpsRepository(has_package=False)
    )
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/credit-ops",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CREDIT_OPS_NOT_AVAILABLE"


# -- POST authorize -----------------------------------------------------------


def test_ops_officer_authorization_records_authority_and_audit_only(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditOpsRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/credit-ops/actions/{ACTION_A}/authorize",
        json={"rationale": "Da kiem tra; cho phep chuan bi (mo phong)."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["actionId"] == str(ACTION_A)
    assert body["actorRole"] == OPS_OFFICER_ROLE
    assert body["actorId"] == str(OFFICER_A)
    assert len(repository.authorizations) == 1
    # (e) the authorization write emitted an audit event.
    assert any(
        e.event_type == "CREDIT_OPS_ACTION_AUTHORIZED" for e in repository.audit_events
    )
    # Only ONE of TWO actions is authorized: G4 must stay OPEN -- no gate write.
    assert orchestration.ensure_gate_calls == []
    # The read model still shows the OTHER action unauthorized and DRAFT.
    status = client.get(
        f"/api/v1/cases/{CASE_ID}/credit-ops",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    ).json()
    by_id = {a["id"]: a for a in status["proposedActions"]}
    assert by_id[str(ACTION_A)]["authorized"] is True
    assert by_id[str(ACTION_A)]["executionStatus"] == "DRAFT"  # authorized != executed
    assert by_id[str(ACTION_B)]["authorized"] is False
    assert status["g4GateStatus"] == "OPEN"


def test_g4_satisfied_only_when_every_action_is_authorized(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditOpsRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    for action_id in (ACTION_A, ACTION_B):
        response = client.post(
            f"/api/v1/cases/{CASE_ID}/credit-ops/actions/{action_id}/authorize",
            json={"rationale": "Da kiem tra day du (mo phong)."},
            headers={"Authorization": f"Bearer {token(signing_key)}"},
        )
        assert response.status_code == 201

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.G4_OPS_AUTHORIZATION
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == OFFICER_A


def test_authorize_endpoint_rejects_non_ops_officer_actors(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditOpsRepository()
    client = _build_client(signing_key, repository=repository)

    for roles in (["INTAKE_OFFICER"], ["RISK_REVIEWER"]):
        response = client.post(
            f"/api/v1/cases/{CASE_ID}/credit-ops/actions/{ACTION_A}/authorize",
            json={"rationale": "khong duoc phep"},
            headers={"Authorization": f"Bearer {token(signing_key, roles=roles)}"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.authorizations == []


def test_authorizing_a_nonexistent_action_is_404_without_capability_leak(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditOpsRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/credit-ops/actions/{uuid4()}/authorize",
        json={"rationale": "khong ton tai"},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "ACTION_NOT_FOUND"
    assert repository.authorizations == []


def test_unassigned_ops_officer_cannot_probe_actions(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # A real action id probed by an actor without case access must return the
    # same indistinguishable 404 as a missing case -- no capability leak.
    repository = FakeCreditOpsRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/credit-ops/actions/{ACTION_A}/authorize",
        json={"rationale": "tham do"},
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert repository.authorizations == []


# -- POST approve document request --------------------------------------------


def test_ops_officer_approval_flips_only_the_derived_view_and_satisfies_g2(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditOpsRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/credit-ops/document-requests/{REQUEST_A}/approve",
        json={"rationale": "Yeu cau hop ly; phe duyet gui di sau (mo phong)."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    assert response.json()["requestId"] == str(REQUEST_A)
    assert len(repository.approvals) == 1
    # (e) the approval write emitted an audit event.
    assert any(
        e.event_type == "CREDIT_OPS_DOCUMENT_REQUEST_APPROVED"
        for e in repository.audit_events
    )
    # All drafted requests now approved: G2 derives SATISFIED via the
    # human-triggered path.
    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.G2_GAP_REQUEST_APPROVAL
    assert call["status"] == GateStatus.SATISFIED

    # The derived view flips to APPROVED; the stored package row (the fake's
    # payload) still says PENDING_APPROVAL -- approval never mutates it.
    status = client.get(
        f"/api/v1/cases/{CASE_ID}/credit-ops",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    ).json()
    assert status["documentRequests"][0]["approvalStatus"] == "APPROVED"
    assert (
        _package_payload()["document_requests"][0]["approval_status"] == "PENDING_APPROVAL"
    )
    assert status["g2GateStatus"] == "SATISFIED"


def test_approve_endpoint_rejects_non_ops_officer_actors(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditOpsRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/credit-ops/document-requests/{REQUEST_A}/approve",
        json={"rationale": "khong duoc phep"},
        headers={"Authorization": f"Bearer {token(signing_key, roles=['RISK_REVIEWER'])}"},
    )

    assert response.status_code == 403
    assert repository.approvals == []


def test_approving_a_nonexistent_request_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditOpsRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/credit-ops/document-requests/{uuid4()}/approve",
        json={"rationale": "khong ton tai"},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "DOCUMENT_REQUEST_NOT_FOUND"
    assert repository.approvals == []


def test_authorization_requires_authentication(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeCreditOpsRepository())
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/credit-ops/actions/{ACTION_A}/authorize",
        json={"rationale": "khong co token"},
    )
    assert response.status_code == 401
