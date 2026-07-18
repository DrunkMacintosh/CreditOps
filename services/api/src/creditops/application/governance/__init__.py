"""Agent governance runtime: the committed goal-contract registry and the
context-manifest builder that bound every specialist model call (master design
sections 10.1, 10.2; P0 #12).
"""

from __future__ import annotations

from creditops.application.governance.contracts import (
    RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY,
    RISK_REVIEW_PRE_ANALYSIS_GOVERNANCE,
    AgentGovernance,
    all_goal_contracts,
    goal_contract_for,
    governance_by_key,
    governance_for,
)
from creditops.application.governance.manifest import (
    build_context_manifest,
    manifest_from_governance,
)

__all__ = [
    "RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY",
    "RISK_REVIEW_PRE_ANALYSIS_GOVERNANCE",
    "AgentGovernance",
    "all_goal_contracts",
    "build_context_manifest",
    "goal_contract_for",
    "governance_by_key",
    "governance_for",
    "manifest_from_governance",
]
