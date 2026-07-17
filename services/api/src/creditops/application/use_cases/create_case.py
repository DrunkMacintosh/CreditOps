from __future__ import annotations

from dataclasses import dataclass

from creditops.application.ports.repositories import (
    AuditEvent,
    CaseRecord,
    InsufficientRoleError,
)
from creditops.application.unit_of_work import ActorContext, UnitOfWorkFactory

INTAKE_OFFICER_ROLE = "INTAKE_OFFICER"


@dataclass(frozen=True, slots=True)
class CreateCaseCommand:
    requested_amount: str
    purpose_vi: str


class CreateCase:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: ActorContext, command: CreateCaseCommand) -> CaseRecord:
        if INTAKE_OFFICER_ROLE not in actor.roles:
            raise InsufficientRoleError

        async with self._uow_factory(actor) as uow:
            created = await uow.cases.create(
                actor_id=actor.actor_id,
                assigned_officer_id=actor.actor_id,
                requested_amount=command.requested_amount,
                purpose_vi=command.purpose_vi,
            )
            assigned = await uow.cases.require_assigned(created.id, actor.actor_id)
            await uow.audit.append(
                AuditEvent(
                    case_id=assigned.id,
                    case_version=assigned.version,
                    event_type="CASE_CREATED",
                    actor_id=actor.actor_id,
                    artifact_type="CREDIT_CASE",
                    artifact_id=assigned.id,
                    event_data={
                        "requestedAmount": command.requested_amount,
                        "purpose": command.purpose_vi,
                    },
                    request_id=actor.request_id,
                )
            )
            return assigned
