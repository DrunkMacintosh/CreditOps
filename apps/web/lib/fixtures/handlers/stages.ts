// -----------------------------------------------------------------------------
// Stage-focus fixture handlers.
//
// Each scenario spotlights one workflow stage; these handlers give that stage a
// real, navigable synthetic read (and, where relevant, the one mutation the
// scenario exercises). Stages a scenario does not focus fall through to the
// router's honest "not available / empty state" fallback.
//
// Shapes must match lib/api/*.ts exactly. These stage clients read camelCase off
// the wire (unlike the underwriting/legal-assessment bodies, which are
// snake_case), so every field below is camelCase.
//
// Each handler is GATED by the data its scenario needs (store.scenarioId, or a
// flag for legal) and returns null otherwise, so a non-focused stage keeps its
// honest empty/not-available state instead of showing fabricated data.
// -----------------------------------------------------------------------------

import type { Disposition } from "../../api/risk-review";
import type { ConditionLedger, DisbursementCondition } from "../../api/conditions";
import type {
  DisbursementAction,
  DisbursementList,
  ExecutionReceipt,
  ExecutionStatus,
} from "../../api/disbursements";
import type {
  CollectionsException,
  Facility,
  LedgerPeriod,
  LedgerSnapshot,
  RepaymentEvent,
} from "../../api/repayments";
import type {
  RecoveryCase,
  RecoveryCases,
  RecoveryOption,
  SettlementCheck,
  SettlementView,
} from "../../api/settlement-recovery";
import type { FixtureStore } from "../store";
import { apiError, type FixtureHandler, type FixtureRequest, type FixtureResponse } from "../types";

// segments = ["api","v1","cases",{id}, resource, ...]. Mirrors core.ts helpers.
const seg = (r: FixtureRequest, i: number): string | undefined => r.segments[i];
const ok = (body: unknown, status = 200): FixtureResponse => ({ status, body });

const isCaseResource = (r: FixtureRequest, resource: string): boolean =>
  seg(r, 2) === "cases" && seg(r, 4) === resource;

// --- 1. Independent Risk Review (scenario risk-challenge) ----------------------

const getRiskReview: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "risk-review") || r.segments.length !== 5) return null;
  if (store.scenarioId !== "risk-challenge") return null;

  const mustRevise =
    store.getSlice<{ disposition?: string }>("risk")?.disposition === "MAKER_MUST_REVISE";

  const dispositions: Disposition[] = mustRevise
    ? [
        {
          id: store.nextId("disp"),
          dispositionType: "MAKER_MUST_REVISE",
          rationale:
            "Yêu cầu bên lập (maker) chỉnh sửa: giả định DSCR chưa có căn cứ từ dòng tiền xác nhận. Gói đề xuất không được finalize cho đến khi maker phản hồi.",
          actorId: store.case.assignedOfficerId,
          actorRole: "INDEPENDENT_RISK_REVIEWER",
          createdAt: store.now(),
        },
      ]
    : [];

  // The risk-review parser reads `target` and `citations` in SNAKE_CASE
  // (result_id / maker_source / …) even though the surrounding envelope is
  // camelCase — mirror that wire shape exactly so the challenge renders real
  // data instead of blank fields.
  const challenge = {
    id: "chal-dscr",
    target: {
      maker_source: "CREDIT_UNDERWRITING",
      maker_assessment_id: "uw-assessment-1",
      section_path: "repaymentCapacity.dscr",
    },
    challengeType: "UNSUPPORTED_ASSUMPTION",
    statement:
      "Giả định DSCR ≥ 1,3 dựa trên doanh thu dự phóng chưa được chứng minh bằng dòng tiền thực tế đã xác nhận; cần bên lập bổ sung căn cứ trước khi finalize.",
    citations: [{ kind: "CALCULATOR_RESULT", result_id: "calc-dscr" }],
    severity: "HIGH",
    confidence: "HIGH",
    raisedBy: "LLM",
    dispositions,
  };

  const body = {
    assessmentId: "risk-assessment-1",
    caseId: store.case.id,
    caseVersion: store.case.version,
    agentRole: "INDEPENDENT_RISK_REVIEWER",
    executionId: "risk-exec-1",
    promptVersion: "risk-review@1",
    createdAt: store.now(),
    handoff: {
      handoffId: store.handoff?.handoffId ?? "ho-risk-1",
      state: "READY_FOR_RISK_REVIEW",
      createdAt: store.now(),
    },
    challenges: [challenge],
    assessmentLevelDispositions: [],
    unresolvedChallengeCount: mustRevise ? 0 : 1,
    // Gate stays OPEN either way: a MAKER_MUST_REVISE disposition does not
    // satisfy the gate (the maker must still revise).
    gateStatus: "OPEN",
  };
  return ok(body);
};

// --- 2. Disbursement conditions ledger (scenario pending-condition) ------------

const getConditions: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "conditions") || r.segments.length !== 5) return null;
  if (store.scenarioId !== "pending-condition") return null;

  const condition: DisbursementCondition = {
    id: "cond-bao-hiem-tai-san",
    caseId: store.case.id,
    caseVersion: store.case.version,
    decisionId: "decision-approve-1",
    conditionText:
      "Bổ sung bằng chứng mua bảo hiểm tài sản bảo đảm (đơn bảo hiểm còn hiệu lực, người thụ hưởng là ngân hàng) trước khi giải ngân.",
    owner: store.case.assignedOfficerId,
    dueDate: null,
    status: "PENDING",
    evidenceRefs: [],
    createdAt: store.now(),
  };

  const body: ConditionLedger = {
    conditions: [condition],
    caseVersion: store.case.version,
    // A PENDING condition blocks confirmation: the disbursement gate stays shut.
    confirmable: false,
  };
  return ok(body);
};

// --- 3. Proposed disbursements + reconcile (scenario execution-unknown) --------

function disbursementAction(store: FixtureStore, status: ExecutionStatus | string): DisbursementAction {
  return {
    id: "disb-attempt-1",
    caseId: store.case.id,
    caseVersion: store.case.version,
    decisionId: "decision-approve-1",
    amount: "5000000000",
    currency: "VND",
    beneficiaryRef: "ben-cong-ty-abc",
    accountRef: "acc-1234567890",
    status,
    createdBy: store.case.assignedOfficerId,
    createdAt: store.now(),
  };
}

const getProposedDisbursements: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "proposed-disbursements") || r.segments.length !== 5) {
    return null;
  }
  if (store.scenarioId !== "execution-unknown") return null;

  const state = store.getSlice<{ state?: string }>("disbursement")?.state ?? "EXECUTION_UNKNOWN";
  const action = disbursementAction(store, state);

  const receipt: ExecutionReceipt = {
    id: "receipt-1",
    actionId: action.id,
    idempotencyKey: "idem-disb-1",
    adapterLabel: "MOCK_DISBURSEMENT_EXECUTION_ADAPTER",
    resultStatus: "EXECUTION_UNKNOWN",
    receiptRef: null,
    recordedBy: store.case.assignedOfficerId,
    createdAt: store.now(),
  };

  const body: DisbursementList = {
    actions: [
      {
        action,
        receipts: [receipt],
        // Both human gates satisfied; the unknown result is an EXECUTION state,
        // not a gate failure — the human must reconcile it.
        validatedGateStatus: "SATISFIED",
        authorizedGateStatus: "SATISFIED",
      },
    ],
    caseVersion: store.case.version,
  };
  return ok(body);
};

const reconcileDisbursement: FixtureHandler = (r, store) => {
  if (
    r.method !== "POST" ||
    !isCaseResource(r, "proposed-disbursements") ||
    r.segments.length !== 7 ||
    seg(r, 6) !== "reconcile"
  ) {
    return null;
  }
  if (store.scenarioId !== "execution-unknown") return null;

  const outcome = (r.body as { outcome?: string } | null)?.outcome;
  const resolved =
    outcome === "CONFIRMED_EXECUTED" || outcome === "CONFIRMED_NOT_EXECUTED"
      ? outcome
      : "CONFIRMED_NOT_EXECUTED";
  store.setSlice("disbursement", { state: resolved });
  store.recordAudit({
    eventType: "DISBURSEMENT_RECONCILED",
    actorType: "HUMAN",
    actorId: store.case.assignedOfficerId,
    artifactType: "DISBURSEMENT_ACTION",
    artifactId: "disb-attempt-1",
    eventData: { outcome: resolved },
  });
  return ok(disbursementAction(store, resolved), 201);
};

// --- 4. Legal policy-unavailable (scenario policy-unavailable) -----------------

const getLegal: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "legal") || r.segments.length !== 5) return null;
  // Gate on the flag, NOT the scenario: fail closed when policy is down. Never
  // fabricate a legal assessment — let the fallback give the honest not-available
  // empty state otherwise.
  if (!store.flags.policyUnavailable) return null;
  return apiError(
    503,
    "POLICY_SOURCE_UNAVAILABLE",
    "Nguồn chính sách hiện không khả dụng. Hệ thống không suy đoán kết quả pháp chế; vui lòng thử lại sau ít phút.",
    { retryable: true },
  );
};

// --- 5. Repayment ledger (scenario repayment-anomaly) --------------------------

const createFacility: FixtureHandler = (r, store) => {
  if (r.method !== "POST" || !isCaseResource(r, "repayments") || r.segments.length !== 5) return null;
  if (store.scenarioId !== "repayment-anomaly") return null;

  const facility: Facility = {
    id: "fac-1",
    caseId: store.case.id,
    caseVersion: store.case.version,
    decisionId: "decision-approve-1",
    principal: "5000000000",
    annualRatePercent: "11.5",
    termMonths: 12,
    periodicFee: "500000",
    repaymentStyle: "EQUAL_PRINCIPAL",
    firstPaymentDate: "2026-08-18",
  };
  return ok(facility, 201);
};

const recordRepaymentEvent: FixtureHandler = (r, store) => {
  if (
    r.method !== "POST" ||
    !isCaseResource(r, "repayments") ||
    r.segments.length !== 7 ||
    seg(r, 6) !== "events"
  ) {
    return null;
  }
  if (store.scenarioId !== "repayment-anomaly") return null;

  const facilityId = seg(r, 5) ?? "fac-1";
  const input = (r.body ?? {}) as {
    kind?: string;
    amount?: string;
    externalReference?: string;
    effectiveDate?: string;
    reversedEventId?: string;
  };
  store.setSlice("repayment", { recognized: true });
  store.recordAudit({
    eventType: "REPAYMENT_ANOMALY_RECORDED",
    actorType: "HUMAN",
    actorId: store.case.assignedOfficerId,
    artifactType: "REPAYMENT_FACILITY",
    artifactId: facilityId,
    eventData: { kind: input.kind ?? "PAYMENT", amount: input.amount ?? "0" },
  });
  const event: RepaymentEvent = {
    id: store.nextId("evt"),
    facilityId,
    kind: input.kind ?? "PAYMENT",
    amount: input.amount ?? "3800000000",
    externalReference: input.externalReference ?? "ext-pmt-1",
    reversedEventId: input.reversedEventId ?? null,
    effectiveDate: input.effectiveDate ?? "2026-08-18",
    created: true,
  };
  return ok(event, 201);
};

const getRepaymentLedger: FixtureHandler = (r, store) => {
  if (
    r.method !== "GET" ||
    !isCaseResource(r, "repayments") ||
    r.segments.length !== 7 ||
    seg(r, 6) !== "ledger"
  ) {
    return null;
  }
  if (store.scenarioId !== "repayment-anomaly") return null;

  const facilityId = seg(r, 5) ?? "fac-1";

  // Period 1 was underpaid by 1.200.000: allocation stops short of the expected
  // principal, leaving an outstanding balance and a collections exception.
  const period1: LedgerPeriod = {
    period: 1,
    dueDate: "2026-08-18",
    expectedFee: "500000",
    expectedInterest: "47916666",
    expectedPrincipal: "416666666",
    allocatedFee: "500000",
    allocatedInterest: "47916666",
    allocatedPrincipal: "415466666",
    outstandingTotal: "1200000",
    status: "PARTIALLY_PAID",
    overdue: true,
  };
  const period2: LedgerPeriod = {
    period: 2,
    dueDate: "2026-09-18",
    expectedFee: "500000",
    expectedInterest: "43923611",
    expectedPrincipal: "416666666",
    allocatedFee: "0",
    allocatedInterest: "0",
    allocatedPrincipal: "0",
    outstandingTotal: "461090277",
    status: "UNPAID",
    overdue: false,
  };

  const exception: CollectionsException = {
    kind: "UNDERPAID_PERIOD",
    period: 1,
    amount: "1200000",
    detailVi:
      "Kỳ 1 trả thiếu 1.200.000 so với gốc phải trả; ghi nhận khoản trả một phần, cần con người định đoạt hướng xử lý.",
  };

  const body: LedgerSnapshot = {
    facilityId,
    asOf: store.now(),
    allocationPolicyVersion: "allocation@1",
    netPaid: "3800000000",
    outstandingFees: "0",
    outstandingInterest: "0",
    outstandingPrincipal: "1200000",
    outstandingTotal: "1200000",
    overpayment: "0",
    isSettled: false,
    periods: [period1, period2],
    exceptions: [exception],
    notes: [],
  };
  return ok(body);
};

// --- 6. Settlement + recovery (scenario settlement-recovery) -------------------

const getSettlement: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "settlement") || r.segments.length !== 5) return null;
  if (store.scenarioId !== "settlement-recovery") return null;

  const check: SettlementCheck = {
    id: "settle-check-1",
    caseId: store.case.id,
    caseVersion: store.case.version,
    outstandingPrincipal: "420000000",
    outstandingInterest: "3500000",
    outstandingFees: "0",
    openExceptionCount: 1,
    zeroBalanceConfirmed: false,
    createdAt: store.now(),
  };

  const body: SettlementView = {
    checks: [check],
    receipts: [],
    caseVersion: store.case.version,
    // Non-zero balance ⇒ the case cannot be settled/closed.
    confirmable: false,
  };
  return ok(body);
};

const getRecovery: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "recovery") || r.segments.length !== 5) return null;
  if (store.scenarioId !== "settlement-recovery") return null;

  const options: RecoveryOption[] = [
    {
      label: "Cơ cấu lại thời hạn trả nợ",
      description: "Đề xuất giãn/cơ cấu kỳ hạn cho phần nghĩa vụ gốc còn lại.",
      consequences: "Kéo dài thời gian thu hồi; cần thẩm quyền phê duyệt cơ cấu.",
      dependencies: "Cần đánh giá lại dòng tiền và tài sản bảo đảm.",
    },
    {
      label: "Xử lý tài sản bảo đảm",
      description: "Chuẩn bị hồ sơ xử lý tài sản bảo đảm để thu hồi nợ.",
      consequences: "Thu hồi qua tài sản; thủ tục pháp lý kéo dài.",
      dependencies: null,
    },
  ];

  const recoveryCase: RecoveryCase = {
    id: "recovery-1",
    caseId: store.case.id,
    caseVersion: store.case.version,
    triggerSummary:
      "Còn nghĩa vụ gốc chưa hoàn tất và có kỳ quá hạn; đủ điều kiện kích hoạt chuẩn bị phương án thu hồi.",
    escalatedBy: store.case.assignedOfficerId,
    escalationRationale:
      "Nghĩa vụ chưa tất toán, cần chuẩn bị evidence pack và phương án thu hồi để trình thẩm quyền riêng.",
    status: "PREPARING",
    evidenceRefs: ["obl-goc-con-lai"],
    options,
    approvedBy: null,
    createdAt: store.now(),
  };

  const body: RecoveryCases = {
    recoveryCases: [recoveryCase],
    caseVersion: store.case.version,
  };
  return ok(body);
};

export const stageHandlers: readonly FixtureHandler[] = [
  getRiskReview,
  getConditions,
  getProposedDisbursements,
  reconcileDisbursement,
  getLegal,
  createFacility,
  recordRepaymentEvent,
  getRepaymentLedger,
  getSettlement,
  getRecovery,
];
