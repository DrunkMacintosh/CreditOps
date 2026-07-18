// -----------------------------------------------------------------------------
// Credit-ops desk (tong-hop, stage 5 "Tổng hợp") fixture handler.
//
// Answers GET /api/v1/cases/{id}/credit-ops with a rich synthetic
// CreditOpsStatus for the ONE scenario that focuses this stage
// (clean-complete), and null for every other scenario so the router falls
// through to its honest CREDIT_OPS_NOT_AVAILABLE (404) empty state.
//
// WIRE SHAPE — matches lib/api/credit-ops.ts parseCreditOpsStatus EXACTLY:
//   * The ENVELOPE is camelCase (packageId, caseId, caseVersion, agentRole,
//     executionId, promptVersion, createdAt, handoff, packageCompleteness,
//     evidenceConsolidation, draftMemo, documentRequests, proposedActions,
//     g2GateStatus, g4GateStatus).
//   * handoff / documentRequests / proposedActions (and their nested
//     approvals / authorizations) are camelCase.
//   * BUT the stored dicts that pass through the proxy verbatim are SNAKE_CASE:
//       - packageCompleteness.artifacts[]: detail_vi, reference_id;
//         packageCompleteness: dispositions_state_vi, unresolved_challenge_count,
//         open_blocking_gap_count, all_required_present.
//       - evidenceConsolidation.entries[]: assessment_id, execution_id,
//         handoff_id, citation_count; evidenceConsolidation:
//         distinct_citation_count.
//       - draftMemo: synthetic_disclaimer_vi, the six memo section keys, and
//         each section's `statements` (each statement a `citations` array);
//         thach_thuc_checker additionally carries disposition_status_vi.
//
// This is MAKER assembly only: it consolidates upstream evidence and drafts a
// memo. There is NO credit decision anywhere — nothing approved or rejected —
// and the memo carries an explicit synthetic disclaimer. Humans decide.
// -----------------------------------------------------------------------------

import type { FixtureStore } from "../store";
import { type FixtureHandler, type FixtureRequest, type FixtureResponse } from "../types";

const seg = (r: FixtureRequest, i: number): string | undefined => r.segments[i];
const ok = (body: unknown, status = 200): FixtureResponse => ({ status, body });

const isCaseResource = (r: FixtureRequest, resource: string): boolean =>
  seg(r, 2) === "cases" && seg(r, 4) === resource;

// A memo statement: free narrative text plus its supporting citations. The
// parser only counts `statements` and each statement's `citations`, but we
// mirror the stored snake_case shape so the summary counts are truthful.
function statement(text: string, citations: readonly string[]) {
  return {
    text,
    citations: citations.map((referenceId) => ({
      kind: "CONFIRMED_FACT",
      reference_id: referenceId,
    })),
  };
}

const getCreditOps: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "credit-ops") || r.segments.length !== 5) {
    return null;
  }
  // Only the clean-complete scenario has all four upstream artifacts present, so
  // only it can assemble a package. Every other scenario falls through to the
  // router's honest CREDIT_OPS_NOT_AVAILABLE empty state.
  if (store.scenarioId !== "clean-complete") return null;

  return ok(buildStatus(store));
};

function buildStatus(store: FixtureStore): Record<string, unknown> {
  const caseId = store.case.id;
  const caseVersion = store.case.version;
  const handoffId = store.handoff?.handoffId ?? "ho-tong-hop-1";

  // package_completeness — stored dict, SNAKE_CASE. All four upstream artifacts
  // present; no unresolved challenges and no open blocking gaps.
  const packageCompleteness = {
    artifacts: [
      {
        artifact: "INTAKE_HANDOFF",
        status: "PRESENT",
        detail_vi: "Đã có bàn giao tiếp nhận với đầy đủ chứng cứ đã xác nhận.",
        reference_id: handoffId,
      },
      {
        artifact: "UNDERWRITING_ASSESSMENT",
        status: "PRESENT",
        detail_vi: "Đã có thẩm định tín dụng của bên lập (maker).",
        reference_id: "uw-assessment-1",
      },
      {
        artifact: "LEGAL_ASSESSMENT",
        status: "PRESENT",
        detail_vi: "Đã có rà soát pháp lý và tài sản bảo đảm.",
        reference_id: "legal-assessment-1",
      },
      {
        artifact: "RISK_REVIEW_ASSESSMENT",
        status: "PRESENT",
        detail_vi: "Đã có rà soát rủi ro độc lập; không còn thách thức tồn đọng.",
        reference_id: "risk-assessment-1",
      },
    ],
    dispositions_state_vi: "Đã xử lý toàn bộ thách thức từ rà soát rủi ro độc lập.",
    unresolved_challenge_count: 0,
    open_blocking_gap_count: 0,
    all_required_present: true,
  };

  // evidence_consolidation — provenance index. entries[] snake_case.
  const evidenceConsolidation = {
    entries: [
      {
        artifact: "INTAKE_HANDOFF",
        assessment_id: null,
        execution_id: "intake-exec-1",
        handoff_id: handoffId,
        citation_count: 3,
      },
      {
        artifact: "UNDERWRITING_ASSESSMENT",
        assessment_id: "uw-assessment-1",
        execution_id: "uw-exec-1",
        handoff_id: null,
        citation_count: 4,
      },
      {
        artifact: "LEGAL_ASSESSMENT",
        assessment_id: "legal-assessment-1",
        execution_id: "legal-exec-1",
        handoff_id: null,
        citation_count: 2,
      },
      {
        artifact: "RISK_REVIEW_ASSESSMENT",
        assessment_id: "risk-assessment-1",
        execution_id: "risk-exec-1",
        handoff_id: null,
        citation_count: 1,
      },
    ],
    // Distinct confirmed-fact references cited across the package.
    distinct_citation_count: 6,
  };

  // draft_memo — stored dict, SNAKE_CASE section keys. Each section holds a
  // `statements` array; each statement a `citations` array. thach_thuc_checker
  // also carries disposition_status_vi. A DRAFT for the human decision-maker —
  // NOT a decision. The disclaimer marks it synthetic.
  const draftMemo = {
    synthetic_disclaimer_vi:
      "Bản ghi nhớ nháp được tổng hợp tự động từ dữ liệu mô phỏng, chỉ phục vụ trình diễn. " +
      "Đây không phải là quyết định tín dụng; mọi phê duyệt do con người có thẩm quyền thực hiện.",
    tom_tat_nhu_cau: {
      statements: [
        statement(
          "Khách hàng đề nghị bổ sung vốn lưu động 5 tỷ đồng phục vụ hợp đồng cung cấp thiết bị.",
          ["ev-cf-doanh-thu"],
        ),
        statement(
          "Thời hạn đề xuất 12 tháng, nguồn trả nợ từ dòng tiền hợp đồng đầu ra.",
          ["ev-cf-loi-nhuan"],
        ),
      ],
    },
    phan_tich_maker: {
      statements: [
        statement("Doanh thu 2025 đạt 48,2 tỷ đồng theo báo cáo tài chính đã xác nhận.", [
          "ev-cf-doanh-thu",
        ]),
        statement("Lợi nhuận sau thuế 2025 đạt 3,1 tỷ đồng.", ["ev-cf-loi-nhuan"]),
        statement("Tổng tài sản 2025 đạt 26,5 tỷ đồng, cơ cấu tài chính lành mạnh.", [
          "ev-cf-tong-tai-san",
        ]),
      ],
    },
    ra_soat_phap_ly_tsbd: {
      statements: [
        statement("Tài sản bảo đảm có hồ sơ pháp lý đầy đủ theo rà soát pháp chế.", [
          "legal-assessment-1",
        ]),
        statement("Không phát hiện tranh chấp hay hạn chế giao dịch đối với tài sản bảo đảm.", [
          "legal-assessment-1",
        ]),
      ],
    },
    thach_thuc_checker: {
      disposition_status_vi: "Đã xử lý toàn bộ thách thức; không còn mục tồn đọng.",
      statements: [
        statement(
          "Thách thức về giả định DSCR đã được bên lập bổ sung căn cứ và rà soát rủi ro ghi nhận xử lý.",
          ["risk-assessment-1"],
        ),
      ],
    },
    dieu_kien_de_xuat: {
      statements: [
        statement("Đề xuất điều kiện: bổ sung bằng chứng bảo hiểm tài sản bảo đảm trước giải ngân.", [
          "legal-assessment-1",
        ]),
        statement("Đề xuất điều kiện: duy trì tài khoản dòng tiền hợp đồng tại ngân hàng.", [
          "ev-cf-doanh-thu",
        ]),
      ],
    },
    phu_luc_bang_chung: {
      statements: [
        statement("Phụ lục liệt kê các chứng cứ đã xác nhận và nguồn trích dẫn tương ứng.", [
          "ev-cf-tong-tai-san",
        ]),
      ],
    },
  };

  // ONE drafted document request, already APPROVED (append-only human authority
  // recorded on the G2 gate). CONDITIONAL, not BLOCKING — consistent with a
  // complete package that has no open blocking gaps.
  const documentRequests = [
    {
      id: "doc-req-bao-hiem-tsbd",
      originatingGapId: "gap-bao-hiem-tsbd",
      requestText:
        "Bổ sung đơn bảo hiểm tài sản bảo đảm còn hiệu lực, người thụ hưởng là ngân hàng.",
      blockingLevel: "CONDITIONAL",
      approvalStatus: "APPROVED",
      approvals: [
        {
          id: "doc-req-approval-1",
          requestId: "doc-req-bao-hiem-tsbd",
          actorId: store.case.assignedOfficerId,
          actorRole: "CREDIT_OPERATIONS_OFFICER",
          rationale: "Đã rà soát nội dung yêu cầu bổ sung; đồng ý ghi nhận để chuyển tới khách hàng.",
          createdAt: store.now(),
        },
      ],
    },
  ];

  // ONE proposed action: prepare the handoff package. Authorized (append-only
  // human authority recorded on the G4 gate). Nothing is executed — the receipt
  // only RECORDS the authorization.
  const proposedActions = [
    {
      id: "action-prepare-handoff",
      actionType: "PREPARE_HANDOFF_PACKAGE",
      description:
        "Chuẩn bị gói bàn giao tổng hợp để trình người có thẩm quyền quyết định tín dụng.",
      executionStatus: "AUTHORIZED",
      relatedDocumentRequestId: null,
      authorized: true,
      authorizations: [
        {
          id: "action-auth-1",
          actionId: "action-prepare-handoff",
          actorId: store.case.assignedOfficerId,
          actorRole: "CREDIT_OPERATIONS_OFFICER",
          rationale:
            "Gói hồ sơ đã đầy đủ và nhất quán; đồng ý ghi nhận ủy quyền chuẩn bị bàn giao.",
          createdAt: store.now(),
        },
      ],
    },
  ];

  return {
    packageId: "pkg-tong-hop-1",
    caseId,
    caseVersion,
    agentRole: "CREDIT_OPERATIONS",
    executionId: "credit-ops-exec-1",
    promptVersion: "credit-ops@1",
    createdAt: store.now(),
    handoff: {
      handoffId,
      state: "READY_FOR_CREDIT_OPERATIONS",
      createdAt: store.now(),
    },
    packageCompleteness,
    evidenceConsolidation,
    draftMemo,
    documentRequests,
    proposedActions,
    // Both human authority gates recorded: G2 (document request approval) and
    // G4 (action authorization). "SATISFIED" reflects recorded authority only,
    // NOT a credit decision.
    g2GateStatus: "SATISFIED",
    g4GateStatus: "SATISFIED",
  };
}

export const creditOpsHandlers: readonly FixtureHandler[] = [getCreditOps];
