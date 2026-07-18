"""The COMMITTED goal-contract registry (master design section 10.1, P0 #12).

One immutable, versioned :class:`~creditops.domain.goal_contracts.GoalContract`
literal per agent task type binds every model call the workforce makes: its
objective (Vietnamese), the synthetic actions it may and -- non-negotiably --
may not take, its success conditions, the output schema it must satisfy, the
human gate its result feeds, and its labelled-synthetic budget.  The domain
model guarantees the hard invariant (``prohibited_actions`` is a superset of the
universal human-only bans of master design section 3.2); this registry only
chooses the per-agent specifics.

Two properties matter for the rest of the system:

- **Stable contract ids.**  Every contract's ``id`` is derived deterministically
  from ``(contract_key, version)`` (``uuid5``), so the id a
  ``ContextManifest.goal_contract_id`` records is identical to the row
  ``ensure_goal_contract_rows`` seeds into ``public.goal_contracts`` -- no
  bootstrap ordering, no lookup.
- **Independent version literals (drift guard).**  The prompt/schema version
  strings here are hand-copied literals, NOT imports of the processor
  constants.  ``tests/unit/governance/test_goal_contract_registry.py`` asserts
  each equals the live processor constant, so bumping a processor's prompt or
  schema version without updating its contract fails the suite.

``goal_contract_for(task_type)`` fails CLOSED: an unmapped task type raises
``KeyError`` and the caller treats that as manual review, never a silent
default.  The Independent Risk Review runs two distinct model calls (a blind
Pass A pre-analysis and a Pass B checker assessment), so it carries TWO
contracts -- the blind one is reached by key, the checker one is the task
type's primary contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from creditops.domain.credit_ops import CREDIT_OPS_AGENT_ROLE
from creditops.domain.goal_contracts import (
    UNIVERSAL_PROHIBITED_ACTIONS,
    BudgetSpec,
    GoalContract,
)
from creditops.domain.legal import LEGAL_AGENT_ROLE
from creditops.domain.orchestration import TaskType
from creditops.domain.risk_review import RISK_REVIEW_AGENT_ROLE
from creditops.domain.underwriting import UNDERWRITING_AGENT_ROLE

# ---------------------------------------------------------------------------
# Governance-owned version literals.
#
# Copied by hand from the processor modules so a drift is a test failure, never
# a silent mismatch (see module docstring).  The profile versions are wholly
# governance-owned (there is no processor counterpart); the prompt/schema
# versions MUST equal the specialist processors' live constants.
# ---------------------------------------------------------------------------

_UNDERWRITING_PROFILE_VERSION = "underwriting-profile-v1"
_UNDERWRITING_PROMPT_VERSION = "underwriting-prompt-v1"
_UNDERWRITING_SCHEMA_VERSION = "underwriting-assessment-v1"

_LEGAL_PROFILE_VERSION = "legal-profile-v1"
_LEGAL_PROMPT_VERSION = "legal-prompt-v1"
_LEGAL_SCHEMA_VERSION = "legal-assessment-v1"

_RISK_PRE_ANALYSIS_PROFILE_VERSION = "risk-pre-analysis-profile-v1"
_RISK_PRE_ANALYSIS_PROMPT_VERSION = "risk-pre-analysis-prompt-v1"
_RISK_PRE_ANALYSIS_SCHEMA_VERSION = "risk-pre-analysis-v1"

_RISK_REVIEW_PROFILE_VERSION = "risk-review-profile-v1"
_RISK_REVIEW_PROMPT_VERSION = "risk-review-prompt-v1"
_RISK_REVIEW_SCHEMA_VERSION = "risk-review-assessment-v1"

_CREDIT_OPS_PROFILE_VERSION = "credit-ops-profile-v1"
_CREDIT_OPS_PROMPT_VERSION = "credit-ops-prompt-v1"
_CREDIT_OPS_SCHEMA_VERSION = "credit-ops-package-v1"

_ORCHESTRATOR_PROFILE_VERSION = "orchestrator-profile-v1"
_ORCHESTRATOR_PROMPT_VERSION = "orchestration-plan-prompt-v1"
_ORCHESTRATOR_SCHEMA_VERSION = "orchestration-plan-v1"

_DOCUMENT_INGESTION_PROFILE_VERSION = "document-ingestion-profile-v1"
_DOCUMENT_INGESTION_PROMPT_VERSION = "document-ingestion-prompt-v1"
_DOCUMENT_INGESTION_SCHEMA_VERSION = "document-ingestion-v1"

#: Contract key for the blind Pass A pre-analysis (reached by key, not by task
#: type -- its task type's primary contract is the Pass B checker assessment).
RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY = "risk-review-pre-analysis"

#: Labelled-synthetic layered budgets (master design section 12.3).  No figure
#: here is derived from a real provider quota; each is a demonstration ceiling.
_MAKER_BUDGET = BudgetSpec(max_input_tokens=120_000, max_output_tokens=8_000, max_tool_calls=16)
_REVIEW_BUDGET = BudgetSpec(max_input_tokens=140_000, max_output_tokens=8_000, max_tool_calls=24)
_BLIND_BUDGET = BudgetSpec(max_input_tokens=90_000, max_output_tokens=6_000, max_tool_calls=8)
_OPS_BUDGET = BudgetSpec(max_input_tokens=160_000, max_output_tokens=10_000, max_tool_calls=20)
_PLAN_BUDGET = BudgetSpec(max_input_tokens=60_000, max_output_tokens=4_000, max_tool_calls=6)
_INGESTION_BUDGET = BudgetSpec(max_input_tokens=40_000, max_output_tokens=4_000, max_tool_calls=6)

#: Every contract restates exactly the universal human-only bans (the domain
#: validator requires a superset; the sorted universal set is a valid superset).
_PROHIBITED = tuple(sorted(UNIVERSAL_PROHIBITED_ACTIONS))


def _contract_id(contract_key: str, version: int) -> UUID:
    """Deterministic contract id so manifests and seeded rows agree on it."""

    return uuid5(NAMESPACE_URL, f"creditops-goal-contract:{contract_key}:v{version}")


def _contract(
    *,
    contract_key: str,
    version: int,
    objective_vi: str,
    allowed_actions: tuple[str, ...],
    success_conditions_vi: tuple[str, ...],
    required_evidence_kinds: tuple[str, ...],
    output_schema_ref: str,
    output_schema_version: str,
    required_human_gate: str | None,
    budgets: BudgetSpec,
) -> GoalContract:
    return GoalContract(
        id=_contract_id(contract_key, version),
        contract_key=contract_key,
        version=version,
        objective_vi=objective_vi,
        allowed_actions=allowed_actions,
        prohibited_actions=_PROHIBITED,
        success_conditions_vi=success_conditions_vi,
        required_evidence_kinds=required_evidence_kinds,
        output_schema_ref=output_schema_ref,
        output_schema_version=output_schema_version,
        required_human_gate=required_human_gate,
        budgets=budgets,
    )


@dataclass(frozen=True, slots=True)
class AgentGovernance:
    """Everything a processor needs to build one call's context manifest.

    Bundles the immutable goal contract with the governance-owned profile
    version and the prompt/schema versions the manifest must record.  The
    ``goal_contract`` alone carries the allowed/prohibited actions, budgets and
    output schema; the version strings pin the exact prompt and schema the call
    was authorized to run.
    """

    task_type: TaskType
    contract_key: str
    agent_role: str
    profile_version: str
    prompt_version: str
    schema_version: str
    goal_contract: GoalContract


_UNDERWRITING = AgentGovernance(
    task_type=TaskType.CREDIT_UNDERWRITING,
    contract_key="underwriting-assessment",
    agent_role=UNDERWRITING_AGENT_ROLE,
    profile_version=_UNDERWRITING_PROFILE_VERSION,
    prompt_version=_UNDERWRITING_PROMPT_VERSION,
    schema_version=_UNDERWRITING_SCHEMA_VERSION,
    goal_contract=_contract(
        contract_key="underwriting-assessment",
        version=1,
        objective_vi=(
            "Thẩm định tín dụng khách hàng doanh nghiệp trên các Sự kiện đã Xác "
            "nhận: phân tích năng lực tài chính, dòng tiền và nguồn trả nợ, đề "
            "xuất cấu trúc khoản vay sơ bộ có dẫn chiếu bằng chứng, không đưa ra "
            "bất kỳ quyết định phê duyệt hay từ chối tín dụng nào."
        ),
        allowed_actions=(
            "READ_CONFIRMED_FACTS",
            "RUN_DETERMINISTIC_CALCULATOR",
            "DRAFT_UNDERWRITING_ASSESSMENT",
            "SURFACE_PROVISIONAL_GAP",
        ),
        success_conditions_vi=(
            "Mọi nhận định trọng yếu đều có dẫn chiếu tới Sự kiện đã Xác nhận "
            "hoặc kết quả máy tính xác định.",
            "Các thiếu hụt bằng chứng được nêu thành khoảng trống PROVISIONAL "
            "kèm mức độ chặn.",
        ),
        required_evidence_kinds=("CONFIRMED_FACT", "CALCULATOR_RESULT"),
        output_schema_ref="underwriting-assessment-output",
        output_schema_version=_UNDERWRITING_SCHEMA_VERSION,
        required_human_gate="HG_UNDERWRITING_ASSESSMENT_REVIEWED",
        budgets=_MAKER_BUDGET,
    ),
)

_LEGAL = AgentGovernance(
    task_type=TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    contract_key="legal-compliance-collateral-assessment",
    agent_role=LEGAL_AGENT_ROLE,
    profile_version=_LEGAL_PROFILE_VERSION,
    prompt_version=_LEGAL_PROMPT_VERSION,
    schema_version=_LEGAL_SCHEMA_VERSION,
    goal_contract=_contract(
        contract_key="legal-compliance-collateral-assessment",
        version=1,
        objective_vi=(
            "Đánh giá pháp lý, tuân thủ và tài sản bảo đảm trên các Sự kiện đã "
            "Xác nhận, hồ sơ trong phạm vi và kết quả kiểm tra được kiểm soát "
            "(KYC, AML/danh sách cảnh báo, bên liên quan), có dẫn chiếu tới kho "
            "chính sách; tuyệt đối không ký, không miễn trừ chính sách và không "
            "ra quyết định pháp lý ràng buộc."
        ),
        allowed_actions=(
            "READ_CONFIRMED_FACTS",
            "RETRIEVE_POLICY_CORPUS",
            "INVOKE_CONTROLLED_CHECK",
            "DRAFT_LEGAL_ASSESSMENT",
            "SURFACE_PROVISIONAL_GAP",
        ),
        success_conditions_vi=(
            "Mỗi kết luận pháp lý/tuân thủ có dẫn chiếu tới bằng chứng, chính "
            "sách hoặc kết quả kiểm tra được kiểm soát.",
            "Khi không có kho chính sách hợp lệ, phần rà soát chính sách để "
            "trống và ghi nhận khoảng trống CONDITIONAL (ADR-0002).",
        ),
        required_evidence_kinds=(
            "CONFIRMED_FACT",
            "POLICY_HIT",
            "CONTROLLED_CHECK_RESULT",
        ),
        output_schema_ref="legal-assessment-output",
        output_schema_version=_LEGAL_SCHEMA_VERSION,
        required_human_gate="HG_LEGAL_ASSESSMENT_REVIEWED",
        budgets=_REVIEW_BUDGET,
    ),
)

_RISK_PRE_ANALYSIS = AgentGovernance(
    task_type=TaskType.INDEPENDENT_RISK_REVIEW,
    contract_key=RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY,
    agent_role=RISK_REVIEW_AGENT_ROLE,
    profile_version=_RISK_PRE_ANALYSIS_PROFILE_VERSION,
    prompt_version=_RISK_PRE_ANALYSIS_PROMPT_VERSION,
    schema_version=_RISK_PRE_ANALYSIS_SCHEMA_VERSION,
    goal_contract=_contract(
        contract_key=RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY,
        version=1,
        objective_vi=(
            "Pass A — hình thành ĐỘC LẬP và MÙ một bản tiền phân tích rủi ro chỉ "
            "từ khung bằng chứng mù (Sự kiện đã Xác nhận), TRƯỚC khi nhìn thấy "
            "bất kỳ kết luận nào của maker; nêu rủi ro và quan sát độc lập, "
            "không thách thức và không kết luận thay cho Pass B."
        ),
        allowed_actions=(
            "READ_BLIND_EVIDENCE",
            "DRAFT_BLIND_PRE_ANALYSIS",
        ),
        success_conditions_vi=(
            "Bản tiền phân tích chỉ dựa trên bằng chứng mù, không tham chiếu bất "
            "kỳ kết luận maker nào.",
        ),
        required_evidence_kinds=("CONFIRMED_FACT",),
        output_schema_ref="risk-pre-analysis-output",
        output_schema_version=_RISK_PRE_ANALYSIS_SCHEMA_VERSION,
        required_human_gate=None,
        budgets=_BLIND_BUDGET,
    ),
)

_RISK_REVIEW = AgentGovernance(
    task_type=TaskType.INDEPENDENT_RISK_REVIEW,
    contract_key="risk-review-assessment",
    agent_role=RISK_REVIEW_AGENT_ROLE,
    profile_version=_RISK_REVIEW_PROFILE_VERSION,
    prompt_version=_RISK_REVIEW_PROMPT_VERSION,
    schema_version=_RISK_REVIEW_SCHEMA_VERSION,
    goal_contract=_contract(
        contract_key="risk-review-assessment",
        version=1,
        objective_vi=(
            "Pass B — rà soát rủi ro độc lập trên cả hai đầu ra của maker "
            "(thẩm định và pháp lý) cùng bản tiền phân tích mù Pass A; nêu các "
            "thách thức có dẫn chiếu và đánh dấu thách thức nào đã xuất hiện khi "
            "phân tích mù, không tự phê duyệt hay xử lý challenge của chính mình."
        ),
        allowed_actions=(
            "READ_CONFIRMED_FACTS",
            "READ_MAKER_OUTPUTS",
            "RAISE_CHALLENGE",
            "DRAFT_RISK_REVIEW_ASSESSMENT",
            "SURFACE_PROVISIONAL_GAP",
        ),
        success_conditions_vi=(
            "Mỗi thách thức có dẫn chiếu tới đầu ra maker, bằng chứng hoặc kết "
            "quả kiểm tra được kiểm soát.",
            "Định danh execution của checker khác mọi execution maker được rà "
            "soát (tách biệt maker-checker).",
        ),
        required_evidence_kinds=(
            "CONFIRMED_FACT",
            "MAKER_OUTPUT",
            "BLIND_PRE_ANALYSIS",
        ),
        output_schema_ref="risk-review-assessment-output",
        output_schema_version=_RISK_REVIEW_SCHEMA_VERSION,
        required_human_gate="G3_RISK_DISPOSITION",
        budgets=_REVIEW_BUDGET,
    ),
)

_CREDIT_OPS = AgentGovernance(
    task_type=TaskType.CREDIT_OPERATIONS,
    contract_key="credit-operations-package",
    agent_role=CREDIT_OPS_AGENT_ROLE,
    profile_version=_CREDIT_OPS_PROFILE_VERSION,
    prompt_version=_CREDIT_OPS_PROMPT_VERSION,
    schema_version=_CREDIT_OPS_SCHEMA_VERSION,
    goal_contract=_contract(
        contract_key="credit-operations-package",
        version=1,
        objective_vi=(
            "Tổng hợp gói tác nghiệp tín dụng từ toàn bộ đầu ra thượng nguồn "
            "(tiếp nhận, thẩm định, pháp lý, rà soát rủi ro độc lập): soạn tờ "
            "trình có dẫn chiếu, hợp nhất yêu cầu bổ sung hồ sơ và đề xuất hành "
            "động để con người phê duyệt; tuyệt đối không tự thực thi hành động "
            "hay giải ngân."
        ),
        allowed_actions=(
            "READ_UPSTREAM_ARTIFACTS",
            "CONSOLIDATE_DOCUMENT_REQUESTS",
            "DRAFT_CREDIT_MEMO",
            "PROPOSE_ACTION",
        ),
        success_conditions_vi=(
            "Tờ trình dẫn chiếu tới các artifact thượng nguồn bất biến và bản "
            "tổng hợp xử lý challenge của con người.",
            "Mọi hành động đề xuất chờ phê duyệt của con người, không có hành "
            "động nào được đánh dấu đã thực thi.",
        ),
        required_evidence_kinds=(
            "INTAKE_HANDOFF",
            "UNDERWRITING_OUTPUT",
            "LEGAL_OUTPUT",
            "RISK_REVIEW_OUTPUT",
        ),
        output_schema_ref="credit-ops-package-output",
        output_schema_version=_CREDIT_OPS_SCHEMA_VERSION,
        required_human_gate="G4_OPS_AUTHORIZATION",
        budgets=_OPS_BUDGET,
    ),
)

_ORCHESTRATOR = AgentGovernance(
    task_type=TaskType.ORCHESTRATOR_PLAN,
    contract_key="orchestrator-plan",
    agent_role="ORCHESTRATOR",
    profile_version=_ORCHESTRATOR_PROFILE_VERSION,
    prompt_version=_ORCHESTRATOR_PROMPT_VERSION,
    schema_version=_ORCHESTRATOR_SCHEMA_VERSION,
    goal_contract=_contract(
        contract_key="orchestrator-plan",
        version=1,
        objective_vi=(
            "Đọc trạng thái case và suy ra đồ thị tác vụ tiếp theo một cách "
            "tất định theo mẫu phụ thuộc; chỉ lập lịch tác vụ, không tự thay "
            "đổi trạng thái nghiệp vụ (ADR-0001) và không ra quyết định tín dụng."
        ),
        allowed_actions=(
            "READ_CASE_STATE",
            "DERIVE_TASK_GRAPH",
            "SCHEDULE_TASK",
        ),
        success_conditions_vi=(
            "Đồ thị tác vụ suy ra khớp mẫu phụ thuộc chuẩn và không mâu thuẫn "
            "với readiness tất định.",
        ),
        required_evidence_kinds=("CASE_STATE",),
        output_schema_ref="orchestration-plan-output",
        output_schema_version=_ORCHESTRATOR_SCHEMA_VERSION,
        required_human_gate=None,
        budgets=_PLAN_BUDGET,
    ),
)

_DOCUMENT_INGESTION = AgentGovernance(
    task_type=TaskType.DOCUMENT_INGESTION,
    contract_key="document-ingestion",
    agent_role="DOCUMENT_INGESTION",
    profile_version=_DOCUMENT_INGESTION_PROFILE_VERSION,
    prompt_version=_DOCUMENT_INGESTION_PROMPT_VERSION,
    schema_version=_DOCUMENT_INGESTION_SCHEMA_VERSION,
    goal_contract=_contract(
        contract_key="document-ingestion",
        version=1,
        objective_vi=(
            "Trích xuất Sự kiện Ứng viên từ một phiên bản tài liệu và đề xuất "
            "để con người xác nhận; tuyệt đối không tự xác nhận Sự kiện, không "
            "đóng khoảng trống và không ra bất kỳ quyết định nào."
        ),
        allowed_actions=(
            "READ_DOCUMENT",
            "EXTRACT_CANDIDATE_FACT",
            "PROPOSE_CANDIDATE_FACT",
        ),
        success_conditions_vi=(
            "Mọi Sự kiện Ứng viên đề xuất đều dẫn chiếu tới vùng nguồn trong "
            "phiên bản tài liệu và ở trạng thái chờ con người xác nhận.",
        ),
        required_evidence_kinds=("DOCUMENT_VERSION",),
        output_schema_ref="document-ingestion-output",
        output_schema_version=_DOCUMENT_INGESTION_SCHEMA_VERSION,
        required_human_gate=None,
        budgets=_INGESTION_BUDGET,
    ),
)

#: Primary governance bundle per task type.  INDEPENDENT_RISK_REVIEW maps to its
#: Pass B checker contract; the blind Pass A contract is reached by key.
_BY_TASK_TYPE: dict[TaskType, AgentGovernance] = {
    TaskType.CREDIT_UNDERWRITING: _UNDERWRITING,
    TaskType.LEGAL_COMPLIANCE_COLLATERAL: _LEGAL,
    TaskType.INDEPENDENT_RISK_REVIEW: _RISK_REVIEW,
    TaskType.CREDIT_OPERATIONS: _CREDIT_OPS,
    TaskType.ORCHESTRATOR_PLAN: _ORCHESTRATOR,
    TaskType.DOCUMENT_INGESTION: _DOCUMENT_INGESTION,
}

#: Every governance bundle, keyed by contract key (includes the blind Pass A).
_BY_KEY: dict[str, AgentGovernance] = {
    bundle.contract_key: bundle
    for bundle in (
        _UNDERWRITING,
        _LEGAL,
        _RISK_PRE_ANALYSIS,
        _RISK_REVIEW,
        _CREDIT_OPS,
        _ORCHESTRATOR,
        _DOCUMENT_INGESTION,
    )
}


def governance_for(task_type: TaskType) -> AgentGovernance:
    """The primary governance bundle for ``task_type``.

    Fails CLOSED: an unmapped task type raises ``KeyError`` so the caller
    treats it as manual review, never a silent default contract.
    """

    return _BY_TASK_TYPE[task_type]


def governance_by_key(contract_key: str) -> AgentGovernance:
    """The governance bundle for a specific ``contract_key`` (fails closed)."""

    return _BY_KEY[contract_key]


def goal_contract_for(task_type: TaskType) -> GoalContract:
    """The committed goal contract bounding ``task_type`` (fails closed)."""

    return governance_for(task_type).goal_contract


#: The blind Pass A pre-analysis governance bundle for the Independent Risk
#: Review, exported for the risk-review processor's first (blind) model call.
RISK_REVIEW_PRE_ANALYSIS_GOVERNANCE = _RISK_PRE_ANALYSIS


def all_goal_contracts() -> tuple[GoalContract, ...]:
    """Every committed contract, for one-time seeding at composition time.

    Includes BOTH Independent Risk Review contracts (blind Pass A and checker
    Pass B) plus the orchestrator and document-ingestion contracts, so the
    ``public.goal_contracts`` seed is the complete committed set.
    """

    return tuple(bundle.goal_contract for bundle in _BY_KEY.values())
