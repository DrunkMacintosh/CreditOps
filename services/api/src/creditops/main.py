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
from creditops.api.errors import (
    ApiException,
    api_exception_handler,
    unexpected_exception_handler,
    validation_exception_handler,
)
from creditops.application.unit_of_work import UnitOfWorkFactory
from creditops.config import Settings
from creditops.infrastructure.postgres.repositories import PostgresUnitOfWorkFactory
from creditops.infrastructure.postgres.session import PsycopgConnectionFactory

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def create_app(
    *,
    settings: Settings | None = None,
    jwt_verifier: JwtVerifier | None = None,
    uow_factory: UnitOfWorkFactory | None = None,
) -> FastAPI:
    configured = settings or Settings()
    if configured.app_env != "test" and (jwt_verifier is not None or uow_factory is not None):
        raise ValueError("Dependency injection overrides are available only in APP_ENV=test")

    application = FastAPI(title="SHB CreditOps EvidenceGraph", version="0.1.0")
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
    if uow_factory is None and configured.database_url:
        uow_factory = PostgresUnitOfWorkFactory(
            PsycopgConnectionFactory(configured.database_url.get_secret_value())
        )

    application.state.jwt_verifier = jwt_verifier
    application.state.uow_factory = uow_factory

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
    return application


app = create_app()
