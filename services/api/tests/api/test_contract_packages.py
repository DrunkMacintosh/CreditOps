"""Role-gated API tests for the stage-8 contract package surfaces.

Covers deterministic rendering (canonical notice + mock label embedded, no
variation for equal inputs), versioned redlines, material-change blocking,
strict gate ordering, MOCK-only signing evidence, and the role/assignment fail
-closed negatives.  ``main.py`` is out of scope for stage 8, so the router is
included into the test app directly and its repositories are injected.

All data is synthetic and created solely for demonstration; the fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from dataclasses import replace
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
from creditops.api.contract_packages import (
    LEGAL_REVIEWER_ROLE,
    OPS_CHECKER_ROLE,
)
from creditops.api.contract_packages import (
    router as contract_packages_router,
)
from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.contract_packages import (
    AddedRedline,
    ContractPackageAlreadySignedError,
    ContractPackageView,
    CreatedContractPackage,
    MaterialChangeBlockedError,
    NoContractPackageError,
    PermittingDecisionSnapshot,
    RecordedContractPackage,
    RecordedContractRedline,
    RecordedSignatureEvidence,
    SignedContractPackage,
)
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.contract_packages import (
    MOCK_CONTRACT_LABEL_VI,
    compute_content_hash,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateType
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-0000000000f1")
DECISION_ID = UUID("d0000000-0000-0000-0000-0000000000f1")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)

_TERMS: dict[str, object] = {
    "amount": "5000000000",
    "currency": "VND",
    "term": "12 tháng",
    "rate": "9.5",
}


class FakeContractPackageRepository:
    """In-memory faithful-enough contract package store (append-only versions)."""

    def __init__(
        self, *, permitting_hash: str | None = "a" * 64, permitting_present: bool = True
    ) -> None:
        self.permitting_present = permitting_present
        self.permitting_hash = permitting_hash or "a" * 64
        self.packages: list[RecordedContractPackage] = []
        self.redlines: list[RecordedContractRedline] = []
        self.evidence: RecordedSignatureEvidence | None = None

    # -- helpers --
    def _current(self, case_id: UUID, case_version: int) -> RecordedContractPackage | None:
        matches = [
            p
            for p in self.packages
            if p.case_id == case_id and p.case_version == case_version
        ]
        return max(matches, key=lambda p: p.package_version) if matches else None

    def _append(
        self,
        base: RecordedContractPackage,
        *,
        state: str,
        content_vi: str,
        content_hash: str,
    ) -> RecordedContractPackage:
        package = RecordedContractPackage(
            id=uuid4(),
            case_id=base.case_id,
            case_version=base.case_version,
            decision_id=base.decision_id,
            term_snapshot_hash=base.term_snapshot_hash,
            content_vi=content_vi,
            content_hash=content_hash,
            package_version=base.package_version + 1,
            state=state,
            created_by=uuid4(),
            created_at=NOW,
        )
        self.packages.append(package)
        return package

    # -- port surface --
    async def load_permitting_decision(
        self, case_id: UUID, case_version: int
    ) -> PermittingDecisionSnapshot | None:
        if not self.permitting_present:
            return None
        return PermittingDecisionSnapshot(
            decision_id=DECISION_ID,
            case_id=case_id,
            case_version=case_version,
            decision_type="APPROVED_WITH_CONDITIONS",
            rationale_vi="Phê duyệt có điều kiện.",
            conditions=("Bổ sung hợp đồng bảo đảm.",),
            terms=dict(_TERMS),
            snapshot_hash=self.permitting_hash,
        )

    async def create_package(
        self,
        *,
        case_id: UUID,
        case_version: int,
        decision_id: UUID,
        term_snapshot_hash: str,
        content_vi: str,
        content_hash: str,
        actor_id: UUID,
    ) -> CreatedContractPackage:
        existing = self._current(case_id, case_version)
        if existing is not None:
            return CreatedContractPackage(package=existing, created=False)
        package = RecordedContractPackage(
            id=uuid4(),
            case_id=case_id,
            case_version=case_version,
            decision_id=decision_id,
            term_snapshot_hash=term_snapshot_hash,
            content_vi=content_vi,
            content_hash=content_hash,
            package_version=1,
            state="DRAFT",
            created_by=actor_id,
            created_at=NOW,
        )
        self.packages.append(package)
        return CreatedContractPackage(package=package, created=True)

    async def load_current_package(
        self, case_id: UUID, case_version: int
    ) -> RecordedContractPackage | None:
        return self._current(case_id, case_version)

    async def load_package_view(
        self, case_id: UUID, case_version: int
    ) -> ContractPackageView | None:
        current = self._current(case_id, case_version)
        if current is None:
            return None
        return ContractPackageView(
            package=current,
            redlines=tuple(self.redlines),
            signature_evidence=self.evidence,
        )

    async def add_redline(
        self,
        *,
        case_id: UUID,
        case_version: int,
        change_note_vi: str,
        changed_content_vi: str,
        changed_content_hash: str,
        actor_id: UUID,
    ) -> AddedRedline:
        base = self._current(case_id, case_version)
        if base is None:
            raise NoContractPackageError("no package to redline")
        redline = RecordedContractRedline(
            id=uuid4(),
            package_id=base.id,
            redline_version=len(self.redlines) + 1,
            change_note_vi=change_note_vi,
            changed_content_vi=changed_content_vi,
            changed_content_hash=changed_content_hash,
            created_by=actor_id,
            created_at=NOW,
        )
        self.redlines.append(redline)
        package = self._append(
            base,
            state="REDLINED",
            content_vi=changed_content_vi,
            content_hash=changed_content_hash,
        )
        return AddedRedline(redline=redline, package=package)

    async def mark_material_change(
        self, *, case_id: UUID, case_version: int, actor_id: UUID
    ) -> RecordedContractPackage:
        base = self._current(case_id, case_version)
        if base is None:
            raise NoContractPackageError("no package to fence")
        if base.state == "MATERIAL_CHANGE_DETECTED":
            return base
        return self._append(
            base,
            state="MATERIAL_CHANGE_DETECTED",
            content_vi=base.content_vi,
            content_hash=base.content_hash,
        )

    async def record_signature_evidence(
        self,
        *,
        case_id: UUID,
        case_version: int,
        signer_names: tuple[str, ...],
        evidence_note_vi: str | None,
        actor_id: UUID,
    ) -> SignedContractPackage:
        base = self._current(case_id, case_version)
        if base is None:
            raise NoContractPackageError("no package to sign")
        if base.state == "MATERIAL_CHANGE_DETECTED":
            raise MaterialChangeBlockedError("blocked")
        if base.state == "READY_FOR_SIGNATURE":
            raise ContractPackageAlreadySignedError("already signed")
        package = self._append(
            base,
            state="READY_FOR_SIGNATURE",
            content_vi=base.content_vi,
            content_hash=base.content_hash,
        )
        evidence = RecordedSignatureEvidence(
            id=uuid4(),
            package_id=package.id,
            kind="MOCK_SIGNATURE",
            signer_names=signer_names,
            evidence_note_vi=evidence_note_vi,
            recorded_by=actor_id,
            created_at=NOW,
        )
        self.evidence = evidence
        return SignedContractPackage(package=package, evidence=evidence)


class FakeOrchestrationRepository:
    def __init__(self) -> None:
        self.gates: list[GateRecord] = []
        self.ensure_gate_calls: list[dict[str, Any]] = []
        self.created_tasks: list[dict[str, Any]] = []
        self.outbox: list[OutboxEventRow] = []
        self.audit_events: list[Any] = []

    async def load_snapshot(self, case_id: UUID) -> Any:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=1,
            has_intake_handoff=True,
            gates=tuple(self.gates),
        )

    async def ensure_gate(self, **kwargs: Any) -> GateRecord:
        self.ensure_gate_calls.append(kwargs)
        record = GateRecord(
            gate_type=kwargs["gate_type"],
            case_version=kwargs["case_version"],
            status=kwargs["status"],
            satisfied_by_actor_id=kwargs.get("satisfied_by_actor_id"),
            disposition_ref=kwargs.get("disposition_ref"),
            satisfied_at=NOW,
        )
        self.gates = [g for g in self.gates if g.gate_type != record.gate_type]
        self.gates.append(record)
        return record

    async def create_task(self, **kwargs: Any) -> CreatedTask:
        for existing in self.created_tasks:
            if existing["idempotency_key"] == kwargs["idempotency_key"]:
                return CreatedTask(
                    row=OrchestrationTaskRow(
                        task_id=existing["task_id"],
                        task_type=existing["task_type"],
                        case_version=int(existing["case_version"]),
                        status=TaskStatus.PENDING,
                    ),
                    created=False,
                )
        self.created_tasks.append(dict(kwargs))
        envelope = TaskEnvelopeV1(
            task_id=kwargs["task_id"],
            case_id=kwargs["case_id"],
            case_version=int(kwargs["case_version"]),
            task_type=kwargs["task_type"],
            document_version_id=None,
        )
        self.outbox.append(
            OutboxEventRow(
                event_id=uuid4(),
                case_id=kwargs["case_id"],
                case_version=int(kwargs["case_version"]),
                event_type="TASK_READY",
                payload=envelope.model_dump(mode="json"),
            )
        )
        return CreatedTask(
            row=OrchestrationTaskRow(
                task_id=kwargs["task_id"],
                task_type=kwargs["task_type"],
                case_version=int(kwargs["case_version"]),
                status=TaskStatus.PENDING,
            ),
            created=True,
        )

    async def record_proposal(self, **kwargs: object) -> None:
        raise AssertionError("not used by the contract package API")

    async def append_audit(self, event: object) -> None:
        self.audit_events.append(event)

    async def load_undispatched_outbox(
        self, *, limit: int
    ) -> tuple[OutboxEventRow, ...]:
        return tuple(e for e in self.outbox if e.dispatched_at is None)[:limit]

    async def mark_outbox_dispatched(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = replace(event, dispatched_at=NOW)

    async def record_outbox_dispatch_failure(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = replace(
                    event, dispatch_attempts=event.dispatch_attempts + 1
                )


class RecordingAgentQueue:
    def __init__(self) -> None:
        self.sent: list[TaskEnvelopeV1] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del delay_seconds
        self.sent.append(envelope)
        return len(self.sent)

    async def read_one(self, *, visibility_timeout_seconds: int) -> None:
        del visibility_timeout_seconds
        return None

    async def extend_visibility(
        self, message_id: int, *, visibility_timeout_seconds: int
    ) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        del message_id


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=OFFICER_A,
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
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeContractPackageRepository,
    orchestration_repository: FakeOrchestrationRepository | None = None,
    agent_queue: RecordingAgentQueue | None = None,
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
    application.include_router(contract_packages_router)
    application.state.contract_package_repository = repository
    application.state.orchestration_repository = orchestration_repository
    application.state.agent_task_queue = agent_queue
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


def _url(suffix: str = "") -> str:
    return f"/api/v1/cases/{CASE_ID}/contract-packages{suffix}"


def _auth(
    signing_key: rsa.RSAPrivateKey, *, roles: list[str], subject: UUID = OFFICER_A
) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, subject=subject, roles=roles)}"}


def _create(client: TestClient, signing_key: rsa.RSAPrivateKey) -> Any:
    return client.post(_url(), headers=_auth(signing_key, roles=[OPS_OFFICER_ROLE]))


# -- deterministic rendering --------------------------------------------------


def test_create_renders_deterministic_content_with_notice_and_label(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)

    response = _create(client, signing_key)

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "DRAFT"
    assert body["packageVersion"] == 1
    content = body["content"]
    assert SYNTHETIC_NOTICE_VI in content
    assert MOCK_CONTRACT_LABEL_VI in content
    assert "5000000000" in content and "VND" in content
    assert body["contentHash"] == compute_content_hash(content)


def test_create_is_idempotent_returns_200_on_repeat(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)

    first = _create(client, signing_key)
    second = _create(client, signing_key)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    # A repeat renders identical content (deterministic) and adds no version.
    assert first.json()["content"] == second.json()["content"]
    assert len(repository.packages) == 1


def test_create_blocked_409_without_permitting_decision(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository(permitting_present=False)
    client = _build_client(signing_key, repository=repository)

    response = _create(client, signing_key)

    assert response.status_code == 409
    assert response.json()["code"] == "NO_PERMITTING_DECISION"
    assert repository.packages == []


# -- redlines -----------------------------------------------------------------


def test_redline_creates_a_new_version(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)
    _create(client, signing_key)

    response = client.post(
        _url("/redlines"),
        json={
            "changeNote": "Sửa điều khoản lãi suất.",
            "changedContent": "Hợp đồng mô phỏng đã redline.",
        },
        headers=_auth(signing_key, roles=[LEGAL_REVIEWER_ROLE]),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["redline"]["redlineVersion"] == 1
    assert body["package"]["state"] == "REDLINED"
    assert body["package"]["packageVersion"] == 2


def test_redline_requires_legal_reviewer_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)
    _create(client, signing_key)

    response = client.post(
        _url("/redlines"),
        json={"changeNote": "x", "changedContent": "y"},
        headers=_auth(signing_key, roles=[OPS_OFFICER_ROLE]),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


# -- approve + material change ------------------------------------------------


def test_approve_satisfies_gate_and_reticks(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    _create(client, signing_key)

    response = client.post(
        _url("/approve"),
        json={"rationale": "Đã rà soát hồ sơ hợp đồng."},
        headers=_auth(signing_key, roles=[OPS_CHECKER_ROLE]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gateType"] == "HG_CONTRACT_PACKAGE_APPROVED"
    assert body["status"] == "SATISFIED"
    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.HG_CONTRACT_PACKAGE_APPROVED
    assert call["satisfied_by_actor_id"] == OFFICER_A
    # The approval self-fires an idempotent orchestration tick.
    assert len(queue.sent) == 1


def test_approve_blocked_on_material_change_and_fences_package(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository(permitting_hash="a" * 64)
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    _create(client, signing_key)  # package rendered with term_snapshot_hash = a*64

    # A new decision landed: the current decision snapshot hash now differs.
    repository.permitting_hash = "b" * 64

    response = client.post(
        _url("/approve"),
        json={"rationale": "Thử phê duyệt sau khi đổi quyết định."},
        headers=_auth(signing_key, roles=[OPS_CHECKER_ROLE]),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "MATERIAL_CHANGE_DETECTED"
    assert body["details"]["state"] == "MATERIAL_CHANGE_DETECTED"
    # No gate is satisfied and the package is fenced in a new version.
    assert orchestration.ensure_gate_calls == []
    fenced = repository._current(CASE_ID, 1)
    assert fenced is not None and fenced.state == "MATERIAL_CHANGE_DETECTED"


def test_approve_blocked_409_when_decision_missing(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    _create(client, signing_key)
    repository.permitting_present = False  # decision disappeared

    response = client.post(
        _url("/approve"),
        json={"rationale": "Phê duyệt khi thiếu quyết định."},
        headers=_auth(signing_key, roles=[OPS_CHECKER_ROLE]),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "NO_PERMITTING_DECISION"
    assert orchestration.ensure_gate_calls == []


# -- gate ordering ------------------------------------------------------------


def test_signature_authority_requires_approval_gate_first(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    _create(client, signing_key)

    response = client.post(
        _url("/signature-authority"),
        json={"rationale": "Xác nhận thẩm quyền ký."},
        headers=_auth(signing_key, roles=[OPS_CHECKER_ROLE]),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "GATE_ORDER_VIOLATION"
    assert orchestration.ensure_gate_calls == []


def test_sign_requires_both_prior_gates(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    _create(client, signing_key)
    # Approve only; signature authority NOT yet confirmed.
    client.post(
        _url("/approve"),
        json={"rationale": "Đã phê duyệt."},
        headers=_auth(signing_key, roles=[OPS_CHECKER_ROLE]),
    )

    response = client.post(
        _url("/sign"),
        json={"signerNames": ["Nguyễn Văn A (mô phỏng)"]},
        headers=_auth(signing_key, roles=[OPS_CHECKER_ROLE]),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "GATE_ORDER_VIOLATION"
    assert repository.evidence is None


def test_full_happy_path_approve_authority_sign(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    checker = _auth(signing_key, roles=[OPS_CHECKER_ROLE])
    _create(client, signing_key)

    approved = client.post(_url("/approve"), json={"rationale": "duyệt"}, headers=checker)
    assert approved.status_code == 200
    authorized = client.post(
        _url("/signature-authority"), json={"rationale": "thẩm quyền"}, headers=checker
    )
    assert authorized.status_code == 200

    response = client.post(
        _url("/sign"),
        json={
            "signerNames": ["Nguyễn Văn A (mô phỏng)", "Trần Thị B (mô phỏng)"],
            "evidenceNote": "Bằng chứng ký mô phỏng.",
        },
        headers=checker,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gateType"] == "HG_CONTRACTS_SIGNED"
    assert body["status"] == "SATISFIED"
    # Signing evidence is MOCK only.
    assert body["signatureEvidence"]["kind"] == "MOCK_SIGNATURE"
    assert body["signatureEvidence"]["signerNames"] == [
        "Nguyễn Văn A (mô phỏng)",
        "Trần Thị B (mô phỏng)",
    ]
    assert body["package"]["state"] == "READY_FOR_SIGNATURE"
    # All three gates satisfied in order.
    satisfied = {c["gate_type"] for c in orchestration.ensure_gate_calls}
    assert satisfied == {
        GateType.HG_CONTRACT_PACKAGE_APPROVED,
        GateType.HG_SIGNATURE_AUTHORITY_CONFIRMED,
        GateType.HG_CONTRACTS_SIGNED,
    }


def test_second_sign_is_409_already_signed(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=RecordingAgentQueue(),
    )
    checker = _auth(signing_key, roles=[OPS_CHECKER_ROLE])
    _create(client, signing_key)
    client.post(_url("/approve"), json={"rationale": "duyệt"}, headers=checker)
    client.post(_url("/signature-authority"), json={"rationale": "tq"}, headers=checker)
    first = client.post(
        _url("/sign"), json={"signerNames": ["A (mô phỏng)"]}, headers=checker
    )
    assert first.status_code == 200

    second = client.post(
        _url("/sign"), json={"signerNames": ["A (mô phỏng)"]}, headers=checker
    )
    assert second.status_code == 409
    assert second.json()["code"] == "CONTRACT_ALREADY_SIGNED"


# -- negatives ----------------------------------------------------------------


def test_create_requires_ops_officer_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(), headers=_auth(signing_key, roles=[LEGAL_REVIEWER_ROLE])
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.packages == []


def test_unassigned_actor_gets_indistinguishable_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        headers=_auth(signing_key, roles=[OPS_OFFICER_ROLE], subject=uuid4()),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_get_returns_package_with_redlines(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)
    _create(client, signing_key)
    client.post(
        _url("/redlines"),
        json={"changeNote": "Sửa.", "changedContent": "Nội dung mới."},
        headers=_auth(signing_key, roles=[LEGAL_REVIEWER_ROLE]),
    )

    response = client.get(
        _url(), headers=_auth(signing_key, roles=[INTAKE_OFFICER_ROLE])
    )

    assert response.status_code == 200
    body = response.json()
    assert body["package"]["state"] == "REDLINED"
    assert len(body["redlines"]) == 1
    assert body["signatureEvidence"] is None


def test_get_is_404_when_no_package(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeContractPackageRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        _url(), headers=_auth(signing_key, roles=[INTAKE_OFFICER_ROLE])
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NO_CONTRACT_PACKAGE"
