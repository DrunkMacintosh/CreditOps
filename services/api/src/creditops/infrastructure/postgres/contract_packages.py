"""Durable Postgres adapter for the stage-8 contract package workflow.

Every write is bounded, append-only, and human-only.  A new ``package_version``
row is written per change (never an edit), and each write commits its audit event
in the SAME transaction.  Reads are always scoped by ``case_id`` + the current
``case_version``.  Nothing here satisfies a gate, resolves a gap, confirms a
fact, or drives orchestration -- gate writes go through the orchestration
repository from the API.

State machine (append-only; each transition writes a new package_version):

    DRAFT --add_redline--> REDLINED            (redline row + new version, 1 txn)
      \\--mark_material_change--> MATERIAL_CHANGE_DETECTED   (blocks all gates)
      \\--record_signature_evidence--> READY_FOR_SIGNATURE   (+ 1:1 MOCK evidence)

"Signed" is the presence of the MOCK evidence row on a READY_FOR_SIGNATURE
version; there is no separate SIGNED state.  Real e-sign / execution is OUT OF
SCOPE.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

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
from creditops.domain.contract_packages import ContractPackageState, SignatureEvidenceKind
from creditops.domain.credit_decisions import APPROVAL_DECISIONS, CreditDecisionType
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

#: Fixed audit actor types per human operation (mirrors the API role gates).
_ACTOR_TYPE_CREATE = "HUMAN:OPS_OFFICER"
_ACTOR_TYPE_REDLINE = "HUMAN:LEGAL_REVIEWER"
_ACTOR_TYPE_MATERIAL_CHANGE = "HUMAN:OPS_CHECKER"
_ACTOR_TYPE_SIGN = "HUMAN:OPS_CHECKER"

_ARTIFACT_TYPE = "CONTRACT_PACKAGE"
_REDLINE_ARTIFACT_TYPE = "CONTRACT_REDLINE"
_EVIDENCE_ARTIFACT_TYPE = "CONTRACT_SIGNATURE_EVIDENCE"

#: A concurrent append can lose the (case, version, package_version) race; retry a
#: bounded number of times so it simply lands at the next version.
_MAX_APPEND_ATTEMPTS = 8

_PACKAGE_COLUMNS = """
  id, case_id, case_version, decision_id, term_snapshot_hash, content_vi,
  content_hash, package_version, state, created_by, created_at
"""


class _RetryAppend(Exception):
    """Internal control-flow signal: a lost package_version race; retry the txn."""


def _package_from_row(row: tuple[object, ...]) -> RecordedContractPackage:
    return RecordedContractPackage(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        decision_id=cast(UUID, row[3]),
        term_snapshot_hash=str(row[4]),
        content_vi=str(row[5]),
        content_hash=str(row[6]),
        package_version=int(cast(int, row[7])),
        state=str(row[8]),
        created_by=cast(UUID, row[9]),
        created_at=cast(datetime, row[10]),
    )


class PostgresContractPackageRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- reads ----------------------------------------------------------------

    async def load_permitting_decision(
        self, case_id: UUID, case_version: int
    ) -> PermittingDecisionSnapshot | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select d.id, d.decision, d.rationale_vi, d.conditions,
                       s.terms, s.snapshot_hash, d.case_version
                from public.human_credit_decisions d
                join public.approved_term_snapshots s on s.decision_id = d.id
                where d.case_id = %s and d.case_version = %s
                """,
                (case_id, case_version),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        decision_type = str(row[1])
        # Only an approval decision permits a contract package.  The snapshot
        # already implies an approval (the domain forbids a snapshot on a
        # non-approval), but re-verify fail-closed.
        if CreditDecisionType(decision_type) not in APPROVAL_DECISIONS:
            return None
        return PermittingDecisionSnapshot(
            decision_id=cast(UUID, row[0]),
            case_id=case_id,
            case_version=int(cast(int, row[6])),
            decision_type=decision_type,
            rationale_vi=str(row[2]),
            conditions=tuple(str(condition) for condition in (row[3] or [])),
            terms=cast("dict[str, object]", row[4]),
            snapshot_hash=str(row[5]),
        )

    async def load_current_package(
        self, case_id: UUID, case_version: int
    ) -> RecordedContractPackage | None:
        async with self._connection_factory() as connection:
            return await self._load_current_package_on(connection, case_id, case_version)

    @staticmethod
    async def _load_current_package_on(
        connection: DatabaseConnection, case_id: UUID, case_version: int
    ) -> RecordedContractPackage | None:
        cursor = await connection.execute(
            f"""
            select {_PACKAGE_COLUMNS}
            from public.contract_packages
            where case_id = %s and case_version = %s
            order by package_version desc
            limit 1
            """,
            (case_id, case_version),
        )
        row = await cursor.fetchone()
        return None if row is None else _package_from_row(tuple(row))

    async def load_package_view(
        self, case_id: UUID, case_version: int
    ) -> ContractPackageView | None:
        async with self._connection_factory() as connection:
            current = await self._load_current_package_on(connection, case_id, case_version)
            if current is None:
                return None
            redlines = await self._load_redlines_on(connection, case_id, case_version)
            evidence = await self._load_evidence_on(connection, current.id)
        return ContractPackageView(
            package=current, redlines=redlines, signature_evidence=evidence
        )

    @staticmethod
    async def _load_redlines_on(
        connection: DatabaseConnection, case_id: UUID, case_version: int
    ) -> tuple[RecordedContractRedline, ...]:
        cursor = await connection.execute(
            """
            select id, package_id, redline_version, change_note_vi,
                   changed_content_vi, changed_content_hash, created_by, created_at
            from public.contract_redlines
            where case_id = %s and case_version = %s
            order by redline_version
            """,
            (case_id, case_version),
        )
        rows = await cursor.fetchall()
        return tuple(
            RecordedContractRedline(
                id=cast(UUID, row[0]),
                package_id=cast(UUID, row[1]),
                redline_version=int(cast(int, row[2])),
                change_note_vi=str(row[3]),
                changed_content_vi=str(row[4]),
                changed_content_hash=str(row[5]),
                created_by=cast(UUID, row[6]),
                created_at=cast(datetime, row[7]),
            )
            for row in rows
        )

    @staticmethod
    async def _load_evidence_on(
        connection: DatabaseConnection, package_id: UUID
    ) -> RecordedSignatureEvidence | None:
        cursor = await connection.execute(
            """
            select id, package_id, kind, signer_names, evidence_note_vi,
                   recorded_by, created_at
            from public.contract_signature_evidence
            where package_id = %s
            """,
            (package_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return RecordedSignatureEvidence(
            id=cast(UUID, row[0]),
            package_id=cast(UUID, row[1]),
            kind=str(row[2]),
            signer_names=tuple(str(name) for name in (row[3] or [])),
            evidence_note_vi=cast("str | None", row[4]),
            recorded_by=cast(UUID, row[5]),
            created_at=cast(datetime, row[6]),
        )

    # -- writes ---------------------------------------------------------------

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
        """Idempotent first draft: return the current package if one already
        exists for the case version, else insert version 1 in state DRAFT."""

        new_id = uuid4()
        async with self._connection_factory() as connection:
            async with connection.transaction():
                existing = await self._load_current_package_on(
                    connection, case_id, case_version
                )
                if existing is not None:
                    return CreatedContractPackage(package=existing, created=False)
                cursor = await connection.execute(
                    """
                    insert into public.contract_packages (
                      id, case_id, case_version, decision_id, term_snapshot_hash,
                      content_vi, content_hash, package_version, state, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, 1, 'DRAFT', %s)
                    returning created_at
                    """,
                    (
                        new_id,
                        case_id,
                        case_version,
                        decision_id,
                        term_snapshot_hash,
                        content_vi,
                        content_hash,
                        actor_id,
                    ),
                )
                created_row = await cursor.fetchone()
                created_at = cast(datetime, created_row[0]) if created_row else None
                await self._append_audit_on(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type="CONTRACT_PACKAGE_DRAFTED",
                    actor_type=_ACTOR_TYPE_CREATE,
                    actor_id=actor_id,
                    artifact_type=_ARTIFACT_TYPE,
                    artifact_id=new_id,
                    event_data={"packageVersion": 1, "state": "DRAFT"},
                )
        return CreatedContractPackage(
            package=RecordedContractPackage(
                id=new_id,
                case_id=case_id,
                case_version=case_version,
                decision_id=decision_id,
                term_snapshot_hash=term_snapshot_hash,
                content_vi=content_vi,
                content_hash=content_hash,
                package_version=1,
                state=ContractPackageState.DRAFT.value,
                created_by=actor_id,
                created_at=cast(datetime, created_at),
            ),
            created=True,
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
        async with self._connection_factory() as connection:
            for _ in range(_MAX_APPEND_ATTEMPTS):
                redline_id = uuid4()
                try:
                    async with connection.transaction():
                        base = await self._require_current_package_on(
                            connection, case_id, case_version, action="redline"
                        )
                        cursor = await connection.execute(
                            """
                            select coalesce(max(redline_version), 0) + 1
                            from public.contract_redlines where package_id = %s
                            """,
                            (base.id,),
                        )
                        redline_row = await cursor.fetchone()
                        redline_version = (
                            int(cast(int, redline_row[0])) if redline_row else 1
                        )
                        cursor = await connection.execute(
                            """
                            insert into public.contract_redlines (
                              id, package_id, case_id, case_version, redline_version,
                              change_note_vi, changed_content_vi,
                              changed_content_hash, created_by
                            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            returning created_at
                            """,
                            (
                                redline_id,
                                base.id,
                                case_id,
                                case_version,
                                redline_version,
                                change_note_vi,
                                changed_content_vi,
                                changed_content_hash,
                                actor_id,
                            ),
                        )
                        redline_created = await cursor.fetchone()
                        package = await self._append_version_on(
                            connection,
                            base=base,
                            content_vi=changed_content_vi,
                            content_hash=changed_content_hash,
                            state=ContractPackageState.REDLINED,
                            actor_id=actor_id,
                        )
                        await self._append_audit_on(
                            connection,
                            case_id=case_id,
                            case_version=case_version,
                            event_type="CONTRACT_PACKAGE_REDLINED",
                            actor_type=_ACTOR_TYPE_REDLINE,
                            actor_id=actor_id,
                            artifact_type=_REDLINE_ARTIFACT_TYPE,
                            artifact_id=redline_id,
                            event_data={
                                "redlineVersion": redline_version,
                                "packageVersion": package.package_version,
                            },
                        )
                except _RetryAppend:
                    continue
                return AddedRedline(
                    redline=RecordedContractRedline(
                        id=redline_id,
                        package_id=base.id,
                        redline_version=redline_version,
                        change_note_vi=change_note_vi,
                        changed_content_vi=changed_content_vi,
                        changed_content_hash=changed_content_hash,
                        created_by=actor_id,
                        created_at=cast(
                            datetime, redline_created[0] if redline_created else None
                        ),
                    ),
                    package=package,
                )
        raise RuntimeError(
            "could not append a redline version after "
            f"{_MAX_APPEND_ATTEMPTS} attempts (persistent version contention)"
        )

    async def mark_material_change(
        self, *, case_id: UUID, case_version: int, actor_id: UUID
    ) -> RecordedContractPackage:
        async with self._connection_factory() as connection:
            for _ in range(_MAX_APPEND_ATTEMPTS):
                try:
                    async with connection.transaction():
                        base = await self._require_current_package_on(
                            connection, case_id, case_version, action="fence"
                        )
                        if (
                            base.state
                            == ContractPackageState.MATERIAL_CHANGE_DETECTED.value
                        ):
                            return base
                        package = await self._append_version_on(
                            connection,
                            base=base,
                            content_vi=base.content_vi,
                            content_hash=base.content_hash,
                            state=ContractPackageState.MATERIAL_CHANGE_DETECTED,
                            actor_id=actor_id,
                        )
                        await self._append_audit_on(
                            connection,
                            case_id=case_id,
                            case_version=case_version,
                            event_type="CONTRACT_PACKAGE_MATERIAL_CHANGE_DETECTED",
                            actor_type=_ACTOR_TYPE_MATERIAL_CHANGE,
                            actor_id=actor_id,
                            artifact_type=_ARTIFACT_TYPE,
                            artifact_id=package.id,
                            event_data={
                                "packageVersion": package.package_version,
                                "consequence": (
                                    "case must return to stage 6 for a new credit "
                                    "decision (deferred loop; not implemented)"
                                ),
                            },
                        )
                except _RetryAppend:
                    continue
                return package
        raise RuntimeError(
            "could not append a material-change version after "
            f"{_MAX_APPEND_ATTEMPTS} attempts (persistent version contention)"
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
        async with self._connection_factory() as connection:
            for _ in range(_MAX_APPEND_ATTEMPTS):
                evidence_id = uuid4()
                try:
                    async with connection.transaction():
                        base = await self._require_current_package_on(
                            connection, case_id, case_version, action="sign"
                        )
                        if (
                            base.state
                            == ContractPackageState.MATERIAL_CHANGE_DETECTED.value
                        ):
                            raise MaterialChangeBlockedError(
                                "a materially changed package cannot be signed"
                            )
                        if base.state == ContractPackageState.READY_FOR_SIGNATURE.value:
                            raise ContractPackageAlreadySignedError(
                                "the current package is already signed"
                            )
                        package = await self._append_version_on(
                            connection,
                            base=base,
                            content_vi=base.content_vi,
                            content_hash=base.content_hash,
                            state=ContractPackageState.READY_FOR_SIGNATURE,
                            actor_id=actor_id,
                        )
                        cursor = await connection.execute(
                            """
                            insert into public.contract_signature_evidence (
                              id, package_id, case_id, case_version, kind,
                              signer_names, evidence_note_vi, recorded_by
                            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                            returning created_at
                            """,
                            (
                                evidence_id,
                                package.id,
                                case_id,
                                case_version,
                                SignatureEvidenceKind.MOCK_SIGNATURE.value,
                                Jsonb(list(signer_names)),
                                evidence_note_vi,
                                actor_id,
                            ),
                        )
                        evidence_created = await cursor.fetchone()
                        await self._append_audit_on(
                            connection,
                            case_id=case_id,
                            case_version=case_version,
                            event_type="CONTRACT_PACKAGE_SIGNED_MOCK",
                            actor_type=_ACTOR_TYPE_SIGN,
                            actor_id=actor_id,
                            artifact_type=_EVIDENCE_ARTIFACT_TYPE,
                            artifact_id=evidence_id,
                            event_data={
                                "packageVersion": package.package_version,
                                "kind": SignatureEvidenceKind.MOCK_SIGNATURE.value,
                                "signerCount": len(signer_names),
                            },
                        )
                except _RetryAppend:
                    continue
                return SignedContractPackage(
                    package=package,
                    evidence=RecordedSignatureEvidence(
                        id=evidence_id,
                        package_id=package.id,
                        kind=SignatureEvidenceKind.MOCK_SIGNATURE.value,
                        signer_names=signer_names,
                        evidence_note_vi=evidence_note_vi,
                        recorded_by=actor_id,
                        created_at=cast(
                            datetime, evidence_created[0] if evidence_created else None
                        ),
                    ),
                )
        raise RuntimeError(
            "could not record signature evidence after "
            f"{_MAX_APPEND_ATTEMPTS} attempts (persistent version contention)"
        )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    async def _require_current_package_on(
        connection: DatabaseConnection,
        case_id: UUID,
        case_version: int,
        *,
        action: str,
    ) -> RecordedContractPackage:
        base = await PostgresContractPackageRepository._load_current_package_on(
            connection, case_id, case_version
        )
        if base is None:
            raise NoContractPackageError(
                f"no contract package exists to {action}"
            )
        return base

    @staticmethod
    async def _append_version_on(
        connection: DatabaseConnection,
        *,
        base: RecordedContractPackage,
        content_vi: str,
        content_hash: str,
        state: ContractPackageState,
        actor_id: UUID,
    ) -> RecordedContractPackage:
        """Append the next package_version carrying ``state`` and content.

        Raises ``_RetryAppend`` when the (case, version, package_version) unique
        guard loses a concurrent race so the caller retries the whole
        transaction.
        """

        new_id = uuid4()
        next_version = base.package_version + 1
        cursor = await connection.execute(
            """
            insert into public.contract_packages (
              id, case_id, case_version, decision_id, term_snapshot_hash,
              content_vi, content_hash, package_version, state, created_by
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (case_id, case_version, package_version) do nothing
            returning created_at
            """,
            (
                new_id,
                base.case_id,
                base.case_version,
                base.decision_id,
                base.term_snapshot_hash,
                content_vi,
                content_hash,
                next_version,
                state.value,
                actor_id,
            ),
        )
        inserted = await cursor.fetchone()
        if inserted is None:
            raise _RetryAppend()
        return RecordedContractPackage(
            id=new_id,
            case_id=base.case_id,
            case_version=base.case_version,
            decision_id=base.decision_id,
            term_snapshot_hash=base.term_snapshot_hash,
            content_vi=content_vi,
            content_hash=content_hash,
            package_version=next_version,
            state=state.value,
            created_by=actor_id,
            created_at=cast(datetime, inserted[0]),
        )

    @staticmethod
    async def _append_audit_on(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        event_type: str,
        actor_type: str,
        actor_id: UUID,
        artifact_type: str,
        artifact_id: UUID,
        event_data: dict[str, object],
    ) -> None:
        await connection.execute(
            """
            insert into public.audit_events (
              case_id, case_version, event_type, actor_type, actor_id,
              artifact_type, artifact_id, event_data
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                case_id,
                case_version,
                event_type,
                actor_type,
                actor_id,
                artifact_type,
                artifact_id,
                Jsonb(event_data),
            ),
        )
