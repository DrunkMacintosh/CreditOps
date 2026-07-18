"""Anonymous synthetic demo session: POST /api/v1/demo-sessions.

This endpoint mints identity, so it is deliberately NOT behind
``require_actor``.  In production it is platform-protected: only the BFF,
holding a Google Cloud Run OIDC id token, can reach it (Cloud Run
``--no-allow-unauthenticated``).  A bounded in-memory token bucket is the
second line of defence against abuse.

Behaviour: create a fresh synthetic actor (random UUID), then reuse the
existing create-case use case within one RLS-scoped unit of work to create a
synthetic credit case, a ``case_assignment`` binding the actor to that case
with the intake role, and a minimal synthetic financing request.  A short-TTL
RS256 JWT for that actor is minted by the local demo signer and returned to
the BFF.  Every artifact created here is SYNTHETIC and labelled as such; no
secret is ever placed in the response.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.errors import ApiException
from creditops.application.unit_of_work import ActorContext, UnitOfWorkFactory
from creditops.application.use_cases.create_case import (
    INTAKE_OFFICER_ROLE,
    CreateCase,
    CreateCaseCommand,
)
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI
from creditops.infrastructure.demo.signer import DemoJwtSigner

router = APIRouter(prefix="/api/v1/demo-sessions", tags=["demo"])

#: Roles the synthetic demo actor holds.  This mirrors exactly the case role
#: the create-case flow self-assigns (INTAKE_OFFICER), so the fail-closed
#: capability intersection (server-side case role AND JWT role) grants the demo
#: actor real capabilities ONLY on its own freshly-created case.
DEMO_ROLES: tuple[str, ...] = (INTAKE_OFFICER_ROLE,)

#: Synthetic seed values for the demo case; both are clearly labelled synthetic.
_DEMO_REQUESTED_AMOUNT = "5000000000"
_DEMO_PURPOSE_VI = "SYNTHETIC — Bổ sung vốn lưu động (dữ liệu tổng hợp trình diễn)"


class TokenBucket:
    """A bounded, thread-safe in-memory token bucket.

    Capacity (``burst``) and ``refill_per_second`` are fixed at construction.
    The bucket keeps no per-caller state and no unbounded structures, so it
    cannot leak memory under load.
    """

    def __init__(self, *, burst: int, refill_per_second: float) -> None:
        self._capacity = float(burst)
        self._refill_per_second = float(refill_per_second)
        self._tokens = float(burst)
        self._updated_at = time.monotonic()
        self._lock = Lock()

    def try_consume(self, amount: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self._updated_at)
            self._updated_at = now
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill_per_second,
            )
            if self._tokens >= amount:
                self._tokens -= amount
                return True
            return False


class DemoSessionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    session_token: str = Field(serialization_alias="sessionToken")
    token_type: str = Field(default="Bearer", serialization_alias="tokenType")
    expires_in_seconds: int = Field(serialization_alias="expiresInSeconds")
    actor_id: UUID = Field(serialization_alias="actorId")
    case_id: UUID = Field(serialization_alias="caseId")
    roles: list[str]
    #: Mandatory synthetic-data disclaimer surfaced to every demo user.
    disclaimer: str


@router.post("", response_model=DemoSessionResponse, status_code=201)
async def create_demo_session(request: Request) -> DemoSessionResponse:
    signer = getattr(request.app.state, "demo_signer", None)
    if not isinstance(signer, DemoJwtSigner):
        # Demo mode is disabled: the endpoint behaves as if it does not exist.
        raise ApiException(
            status_code=404,
            code="DEMO_SESSION_DISABLED",
            message_vi="Phiên trình diễn không khả dụng.",
        )

    limiter = getattr(request.app.state, "demo_rate_limiter", None)
    if isinstance(limiter, TokenBucket) and not limiter.try_consume():
        raise ApiException(
            status_code=429,
            code="RATE_LIMITED",
            message_vi="Bạn đã tạo quá nhiều phiên trình diễn. Vui lòng thử lại sau.",
            retryable=True,
            headers={"Retry-After": "1"},
        )

    uow_factory = getattr(request.app.state, "uow_factory", None)
    if uow_factory is None:
        raise ApiException(
            status_code=503,
            code="DEMO_SESSION_UNAVAILABLE",
            message_vi="Dịch vụ phiên trình diễn chưa sẵn sàng.",
            retryable=True,
        )

    actor_id = uuid4()
    actor = ActorContext(
        actor_id=actor_id,
        roles=frozenset(DEMO_ROLES),
        request_id=request.state.correlation_id,
    )
    # One RLS-scoped transaction (SET LOCAL ROLE + set_config actor id) creates
    # the synthetic case, the self-assignment, and the financing request.  RLS
    # confines this actor to its own case, isolating concurrent demo sessions.
    record = await CreateCase(cast(UnitOfWorkFactory, uow_factory)).execute(
        actor,
        CreateCaseCommand(
            requested_amount=_DEMO_REQUESTED_AMOUNT,
            purpose_vi=_DEMO_PURPOSE_VI,
        ),
    )

    session_token, expires_in_seconds = signer.sign(subject=actor_id, roles=DEMO_ROLES)
    return DemoSessionResponse(
        session_token=session_token,
        expires_in_seconds=expires_in_seconds,
        actor_id=actor_id,
        case_id=record.id,
        roles=list(DEMO_ROLES),
        disclaimer=SYNTHETIC_NOTICE_VI,
    )
