from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response
from starlette.types import ExceptionHandler

from creditops.api.auth import JwtVerifier, RemoteJwksKeyResolver
from creditops.api.cases import router as cases_router
from creditops.api.credit_ops import router as credit_ops_router
from creditops.api.errors import (
    ApiException,
    api_exception_handler,
    unexpected_exception_handler,
    validation_exception_handler,
)
from creditops.api.legal import router as legal_router
from creditops.api.orchestration import router as orchestration_router
from creditops.api.risk_review import router as risk_review_router
from creditops.api.tasks import router as tasks_router
from creditops.api.underwriting import router as underwriting_router
from creditops.api.uploads import router as uploads_router
from creditops.application.ports.storage import StoragePort
from creditops.application.unit_of_work import UnitOfWorkFactory
from creditops.config import Settings
from creditops.infrastructure.postgres.credit_ops import PostgresCreditOpsRepository
from creditops.infrastructure.postgres.legal import PostgresLegalRepository
from creditops.infrastructure.postgres.orchestration import (
    PostgresOrchestrationRepository,
)
from creditops.infrastructure.postgres.repositories import PostgresUnitOfWorkFactory
from creditops.infrastructure.postgres.risk_review import PostgresRiskReviewRepository
from creditops.infrastructure.postgres.session import PsycopgConnectionFactory
from creditops.infrastructure.postgres.tasks import PostgresTaskRepository
from creditops.infrastructure.postgres.underwriting import (
    PostgresUnderwritingRepository,
)
from creditops.infrastructure.supabase.queue import AGENT_TASK_QUEUE_NAME, SupabaseQueue
from creditops.infrastructure.supabase.storage import SupabaseStorage
from creditops.observability import configure_structured_logging
from creditops.security_headers import SecurityHeadersMiddleware

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def create_app(
    *,
    settings: Settings | None = None,
    jwt_verifier: JwtVerifier | None = None,
    uow_factory: UnitOfWorkFactory | None = None,
    storage_port: StoragePort | None = None,
) -> FastAPI:
    configured = settings or Settings()
    if configured.app_env != "test" and (
        jwt_verifier is not None or uow_factory is not None or storage_port is not None
    ):
        raise ValueError("Dependency injection overrides are available only in APP_ENV=test")

    if configured.app_env != "test":
        configure_structured_logging(
            service_name=configured.service_name,
            level=configured.log_level,
        )

    application = FastAPI(title="SHB CreditOps EvidenceGraph", version="0.1.0")
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_exception_handler(
        ApiException,
        cast(ExceptionHandler, api_exception_handler),
    )
    application.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_exception_handler),
    )
    application.add_exception_handler(
        Exception,
        cast(ExceptionHandler, unexpected_exception_handler),
    )

    if jwt_verifier is None and all(
        (configured.oidc_issuer, configured.oidc_audience, configured.oidc_jwks_url)
    ):
        jwt_verifier = JwtVerifier(
            issuer=cast(str, configured.oidc_issuer),
            audience=cast(str, configured.oidc_audience),
            key_resolver=RemoteJwksKeyResolver(cast(str, configured.oidc_jwks_url)),
        )
    database_connection_factory = None
    if configured.database_url:
        database_connection_factory = PsycopgConnectionFactory(
            configured.database_url.get_secret_value()
        )
    if uow_factory is None and database_connection_factory is not None:
        uow_factory = PostgresUnitOfWorkFactory(database_connection_factory)
    if storage_port is None and configured.supabase_url and configured.supabase_service_role_key:
        storage_port = SupabaseStorage(configured)

    application.state.jwt_verifier = jwt_verifier
    application.state.uow_factory = uow_factory
    application.state.storage = storage_port
    application.state.task_repository = (
        PostgresTaskRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.task_queue = (
        SupabaseQueue(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.agent_task_queue = (
        SupabaseQueue(database_connection_factory, queue_name=AGENT_TASK_QUEUE_NAME)
        if database_connection_factory is not None
        else None
    )
    application.state.orchestration_repository = (
        PostgresOrchestrationRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.underwriting_repository = (
        PostgresUnderwritingRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.legal_repository = (
        PostgresLegalRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.risk_review_repository = (
        PostgresRiskReviewRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.credit_ops_repository = (
        PostgresCreditOpsRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )

    @application.middleware("http")
    async def assign_correlation_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request.state.correlation_id = (
            supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @application.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"service": configured.service_name, "status": "ok"}

    @application.get("/api/v1/ready")
    def ready() -> dict[str, str]:
        return {"service": configured.service_name, "status": "configuration-valid"}

    application.include_router(cases_router)
    application.include_router(uploads_router)
    application.include_router(tasks_router)
    application.include_router(orchestration_router)
    application.include_router(underwriting_router)
    application.include_router(legal_router)
    application.include_router(risk_review_router)
    application.include_router(credit_ops_router)
    return application


app = create_app()
