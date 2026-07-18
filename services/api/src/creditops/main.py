from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import cast, get_args
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response
from starlette.types import ExceptionHandler

from creditops.api.audit import router as audit_router
from creditops.api.audit_search import router as audit_search_router
from creditops.api.auth import JwksKeyResolver, JwtVerifier, RemoteJwksKeyResolver
from creditops.api.cases import router as cases_router
from creditops.api.conditions import router as conditions_router
from creditops.api.config_view import router as config_view_router
from creditops.api.contract_packages import router as contract_packages_router
from creditops.api.credit_decisions import router as credit_decisions_router
from creditops.api.credit_ops import router as credit_ops_router
from creditops.api.demo_sessions import TokenBucket
from creditops.api.demo_sessions import router as demo_sessions_router
from creditops.api.disbursements import router as disbursements_router
from creditops.api.errors import (
    ApiException,
    api_exception_handler,
    unexpected_exception_handler,
    validation_exception_handler,
)
from creditops.api.evidence_review import router as evidence_review_router
from creditops.api.financing import router as financing_router
from creditops.api.gap_requests import router as gap_requests_router
from creditops.api.intake import router as intake_router
from creditops.api.legal import router as legal_router
from creditops.api.monitoring import router as monitoring_router
from creditops.api.notifications import router as notifications_router
from creditops.api.orchestration import router as orchestration_router
from creditops.api.prospects import router as prospects_router
from creditops.api.repayments import router as repayments_router
from creditops.api.reporting import router as reporting_router
from creditops.api.risk_review import router as risk_review_router
from creditops.api.security_interests import router as security_interests_router
from creditops.api.settlement_recovery import router as settlement_recovery_router
from creditops.api.tasks import router as tasks_router
from creditops.api.underwriting import router as underwriting_router
from creditops.api.uploads import router as uploads_router
from creditops.api.work_items import router as work_items_router
from creditops.application.ports.storage import StoragePort
from creditops.application.unit_of_work import UnitOfWorkFactory
from creditops.config import Settings
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI
from creditops.infrastructure.demo.signer import DemoJwtSigner
from creditops.infrastructure.fpt.catalog import CapabilityName, FPTCatalog
from creditops.infrastructure.gcp.cloud_run_dispatcher import CloudRunDispatcher
from creditops.infrastructure.gcp.metadata_token import MetadataTokenProvider
from creditops.infrastructure.mock.disbursement_adapter import (
    MockDisbursementExecutionAdapter,
)
from creditops.infrastructure.postgres.conditions import (
    PostgresConditionLedgerRepository,
)
from creditops.infrastructure.postgres.contract_packages import (
    PostgresContractPackageRepository,
)
from creditops.infrastructure.postgres.credit_decisions import (
    PostgresCreditDecisionRepository,
)
from creditops.infrastructure.postgres.credit_ops import PostgresCreditOpsRepository
from creditops.infrastructure.postgres.disbursements import (
    PostgresDisbursementRepository,
)
from creditops.infrastructure.postgres.evidence_review import (
    PostgresEvidenceReviewRepository,
)
from creditops.infrastructure.postgres.financing import PostgresFinancingRepository
from creditops.infrastructure.postgres.gap_request_batches import (
    PostgresGapRequestRepository,
)
from creditops.infrastructure.postgres.intake import PostgresIntakeRepository
from creditops.infrastructure.postgres.legal import PostgresLegalRepository
from creditops.infrastructure.postgres.monitoring import (
    PostgresMonitoringRepository,
)
from creditops.infrastructure.postgres.notifications import (
    PostgresNotificationRepository,
)
from creditops.infrastructure.postgres.orchestration import (
    PostgresOrchestrationRepository,
)
from creditops.infrastructure.postgres.prospects import PostgresProspectRepository
from creditops.infrastructure.postgres.repayments import (
    PostgresRepaymentLedgerRepository,
)
from creditops.infrastructure.postgres.reporting import (
    PostgresReportingRepository,
)
from creditops.infrastructure.postgres.repositories import PostgresUnitOfWorkFactory
from creditops.infrastructure.postgres.risk_review import PostgresRiskReviewRepository
from creditops.infrastructure.postgres.security_interests import (
    PostgresSecurityInterestRepository,
)
from creditops.infrastructure.postgres.session import PsycopgConnectionFactory
from creditops.infrastructure.postgres.settlement_recovery import (
    PostgresSettlementRecoveryRepository,
)
from creditops.infrastructure.postgres.tasks import PostgresTaskRepository
from creditops.infrastructure.postgres.underwriting import (
    PostgresUnderwritingRepository,
)
from creditops.infrastructure.postgres.work_items import PostgresWorkItemRepository
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

    oidc_configured = all(
        (configured.oidc_issuer, configured.oidc_audience, configured.oidc_jwks_url)
    )
    if jwt_verifier is None and oidc_configured:
        jwt_verifier = JwtVerifier(
            issuer=cast(str, configured.oidc_issuer),
            audience=cast(str, configured.oidc_audience),
            key_resolver=RemoteJwksKeyResolver(cast(str, configured.oidc_jwks_url)),
        )

    # Demo mode: build a LOCAL signer + a verifier seeded from its PUBLIC key so
    # the API validates exactly the demo JWTs it mints.  The external OIDC path
    # above still wins when it is configured; the demo verifier only fills in
    # when no external issuer exists (fail closed to no verifier otherwise).
    demo_signer: DemoJwtSigner | None = None
    if configured.demo_session_enabled and configured.demo_jwt_private_key is not None:
        demo_signer = DemoJwtSigner(
            private_key_pem=configured.demo_jwt_private_key.get_secret_value(),
            issuer=configured.demo_jwt_issuer,
            audience=configured.demo_jwt_audience,
            kid=configured.demo_jwt_kid,
            ttl_seconds=configured.demo_session_ttl_seconds,
        )
        if jwt_verifier is None:
            jwt_verifier = JwtVerifier(
                issuer=configured.demo_jwt_issuer,
                audience=configured.demo_jwt_audience,
                key_resolver=JwksKeyResolver(demo_signer.public_jwks()),
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
    application.state.demo_signer = demo_signer
    application.state.demo_rate_limiter = (
        TokenBucket(
            burst=configured.demo_session_rate_limit_burst,
            refill_per_second=configured.demo_session_rate_limit_refill_per_second,
        )
        if demo_signer is not None
        else None
    )
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
    application.state.gap_request_repository = (
        PostgresGapRequestRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.intake_repository = (
        PostgresIntakeRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.evidence_review_repository = (
        PostgresEvidenceReviewRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.work_item_repository = (
        PostgresWorkItemRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.financing_repository = (
        PostgresFinancingRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.prospect_repository = (
        PostgresProspectRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.credit_decision_repository = (
        PostgresCreditDecisionRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.notification_repository = (
        PostgresNotificationRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.condition_ledger_repository = (
        PostgresConditionLedgerRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.security_interest_repository = (
        PostgresSecurityInterestRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.contract_package_repository = (
        PostgresContractPackageRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.disbursement_repository = (
        PostgresDisbursementRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    # Labelled synthetic mock: no real core-banking execution exists or is
    # authorized in the current scope.
    application.state.disbursement_execution_adapter = (
        MockDisbursementExecutionAdapter()
        if database_connection_factory is not None
        else None
    )
    application.state.monitoring_repository = (
        PostgresMonitoringRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.repayment_ledger_repository = (
        PostgresRepaymentLedgerRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.settlement_recovery_repository = (
        PostgresSettlementRecoveryRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.reporting_repository = (
        PostgresReportingRepository(database_connection_factory)
        if database_connection_factory is not None
        else None
    )
    application.state.worker_dispatcher = (
        CloudRunDispatcher(
            project_id=cast(str, configured.gcp_project_id),
            location=cast(str, configured.gcp_location),
            job_name=cast(str, configured.gcp_worker_job_name),
            token_provider=MetadataTokenProvider(),
        )
        if all(
            (
                configured.gcp_project_id,
                configured.gcp_location,
                configured.gcp_worker_job_name,
            )
        )
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

    async def _probe_backends() -> tuple[str, str]:
        """Bounded probe of the database and (PGMQ) queue reachability.

        Returns ``(database_status, queue_status)`` where each is ``"ok"``,
        ``"unavailable"`` or ``"disabled"``.  The probe opens one short-lived
        connection, runs ``SELECT 1`` and checks for the ``pgmq`` schema; it
        leaks no secret and is time-boxed so ``/ready`` cannot hang.
        """

        if database_connection_factory is None:
            return "disabled", "disabled"
        queue_configured = application.state.task_queue is not None
        try:
            async with asyncio.timeout(3.0):
                async with database_connection_factory() as connection:
                    cursor = await connection.execute("select 1")
                    row = await cursor.fetchone()
                    database_ok = row is not None and row[0] == 1
                    queue_ok = False
                    if queue_configured:
                        queue_cursor = await connection.execute(
                            "select 1 from pg_namespace where nspname = 'pgmq'"
                        )
                        queue_ok = await queue_cursor.fetchone() is not None
        except Exception:
            return "unavailable", ("unavailable" if queue_configured else "disabled")
        database_status = "ok" if database_ok else "unavailable"
        if not queue_configured:
            return database_status, "disabled"
        return database_status, ("ok" if queue_ok else "unavailable")

    def _fpt_capability_states() -> list[dict[str, str]]:
        """Non-secret ACTIVE/DISABLED state per FPT capability (fail closed).

        Only capability names and their route state are exposed; endpoint URLs,
        ids and keys are never touched.  Any configuration error collapses to
        every capability DISABLED.
        """

        names = tuple(str(name) for name in get_args(CapabilityName))
        active: set[str] = set()
        try:
            catalog = FPTCatalog.from_configuration()
            active = {str(capability) for capability in catalog.capabilities}
        except Exception:
            active = set()
        return [
            {"capability": name, "state": "ACTIVE" if name in active else "DISABLED"}
            for name in names
        ]

    if oidc_configured:
        auth_mode = "oidc"
    elif demo_signer is not None:
        auth_mode = "demo"
    else:
        auth_mode = "none"

    @application.get("/api/v1/ready")
    async def ready() -> dict[str, object]:
        database_status, queue_status = await _probe_backends()
        storage_status = "ok" if application.state.storage is not None else "disabled"
        auth_status = "ok" if application.state.jwt_verifier is not None else "disabled"
        is_ready = (
            database_status == "ok"
            and queue_status == "ok"
            and storage_status == "ok"
            and auth_status == "ok"
        )
        return {
            "service": configured.service_name,
            "status": "ready" if is_ready else "not-ready",
            "ready": is_ready,
            "components": {
                "database": {"status": database_status},
                "queue": {"status": queue_status},
                "storage": {"status": storage_status},
                "auth": {"status": auth_status, "mode": auth_mode},
                "fpt": {"capabilities": _fpt_capability_states()},
            },
            "disclaimer": SYNTHETIC_NOTICE_VI,
        }

    if demo_signer is not None:
        application.include_router(demo_sessions_router)
    application.include_router(cases_router)
    application.include_router(uploads_router)
    application.include_router(tasks_router)
    application.include_router(orchestration_router)
    application.include_router(underwriting_router)
    application.include_router(legal_router)
    application.include_router(risk_review_router)
    application.include_router(credit_ops_router)
    application.include_router(gap_requests_router)
    application.include_router(audit_router)
    application.include_router(intake_router)
    application.include_router(evidence_review_router)
    application.include_router(work_items_router)
    application.include_router(financing_router)
    application.include_router(prospects_router)
    application.include_router(credit_decisions_router)
    application.include_router(notifications_router)
    application.include_router(conditions_router)
    application.include_router(security_interests_router)
    application.include_router(contract_packages_router)
    application.include_router(disbursements_router)
    application.include_router(monitoring_router)
    application.include_router(repayments_router)
    application.include_router(settlement_recovery_router)
    application.include_router(reporting_router)
    application.include_router(audit_search_router)
    application.include_router(config_view_router)
    return application


app = create_app()
