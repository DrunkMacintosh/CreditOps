"""Role constants for orchestration, mirroring ``INTAKE_OFFICER_ROLE``.

``CASE_ORCHESTRATOR_ROLE`` is the AGENT role recorded on every orchestrator
output for provenance; it is not an authentication role a human token carries.
``RISK_REVIEWER_ROLE`` and ``OPS_OFFICER_ROLE`` are human case-participant roles
used to gate the orchestration API, alongside the existing intake role.

ASSUMPTION: the official SHB role mapping is an OPEN QUESTION
(docs/AGENT_ARCHITECTURE.md); these identifiers are synthetic.
"""

from __future__ import annotations

from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE

CASE_ORCHESTRATOR_ROLE = "CASE_ORCHESTRATOR"
RISK_REVIEWER_ROLE = "RISK_REVIEWER"
OPS_OFFICER_ROLE = "OPS_OFFICER"

# Human roles permitted to view or trigger orchestration for a case they are
# assigned to.  Row access is still enforced by the case-assignment filter.
CASE_PARTICIPANT_ROLES = frozenset(
    {INTAKE_OFFICER_ROLE, RISK_REVIEWER_ROLE, OPS_OFFICER_ROLE}
)

__all__ = [
    "CASE_ORCHESTRATOR_ROLE",
    "CASE_PARTICIPANT_ROLES",
    "INTAKE_OFFICER_ROLE",
    "OPS_OFFICER_ROLE",
    "RISK_REVIEWER_ROLE",
]
