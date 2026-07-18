// -----------------------------------------------------------------------------
// Underwriting (tham-dinh, stage 4) fixture handler.
//
// Serves GET /api/v1/cases/{id}/underwriting for the two scenarios that focus
// the underwriting stage (clean-complete, downstream-stale) and returns null
// otherwise, so the router's fallback gives the honest UNDERWRITING_NOT_AVAILABLE
// empty state for every other scenario.
//
// WIRE SHAPE — matches lib/api/underwriting.ts parseUnderwritingAssessment
// EXACTLY. The ENVELOPE is camelCase (assessmentId, caseId, caseVersion,
// agentRole, executionId, promptVersion, createdAt, assessment, handoff and the
// handoff's own handoffId/state/createdAt). The nested `assessment` body is the
// persisted domain model_dump: SNAKE_CASE keys, and every Decimal crosses the
// wire as a STRING. See the key map in tests/lib/fixtures/underwriting-fixture
// .test.ts. Content is synthetic; there is NO decision field and NO raw provider
// text / chain-of-thought anywhere — only reviewed statements with citations.
// -----------------------------------------------------------------------------

import type { FixtureStore } from "../store";
import { type FixtureHandler, type FixtureRequest, type FixtureResponse } from "../types";

// segments = ["api","v1","cases",{id}, resource, ...]. Mirrors stages.ts helpers.
const seg = (r: FixtureRequest, i: number): string | undefined => r.segments[i];
const ok = (body: unknown, status = 200): FixtureResponse => ({ status, body });

const isCaseResource = (r: FixtureRequest, resource: string): boolean =>
  seg(r, 2) === "cases" && seg(r, 4) === resource;

// --- snake_case wire builders (the assessment body) --------------------------

// Citation kinds the parser accepts: CONFIRMED_FACT | CALCULATOR_RESULT |
// DOCUMENT_REGION. Only the fields matching each kind are read.
const factCite = (confirmedFactId: string) => ({
  kind: "CONFIRMED_FACT",
  confirmed_fact_id: confirmedFactId,
});
const calcCite = (resultId: string) => ({
  kind: "CALCULATOR_RESULT",
  result_id: resultId,
});

interface FindingSeed {
  statementVi: string;
  citations: ReadonlyArray<Record<string, unknown>>;
  confidence: "HIGH" | "MEDIUM" | "LOW";
  uncertaintyVi: string;
}

const finding = (seed: FindingSeed) => ({
  statement_vi: seed.statementVi,
  citations: seed.citations,
  confidence: seed.confidence,
  uncertainty_vi: seed.uncertaintyVi,
});

// Deterministic DSCR (debt-service coverage ratio) computation, emitted as a
// COMPUTED calculator result with the value as an exact-decimal STRING.
function buildDscrCalculator(stale: boolean) {
  return {
    result_id: "calc-dscr",
    calculator: "dscr",
    inputs: [
      {
        name: "dong_tien_hoat_dong",
        value: "6960000000",
        fact_refs: [
          { kind: "CONFIRMED_FACT", ref_id: "ev-cf-doanh-thu" },
          { kind: "CONFIRMED_FACT", ref_id: "ev-cf-loi-nhuan" },
        ],
      },
      {
        name: "nghia_vu_tra_no_nam",
        value: "4800000000",
        fact_refs: [{ kind: "CONFIRMED_FACT", ref_id: "ev-cf-loi-nhuan" }],
      },
    ],
    // 6.960.000.000 / 4.800.000.000 = 1,45.
    outcome: { status: "COMPUTED", value: stale ? "1.320000" : "1.450000" },
  };
}

function buildAssessment(store: FixtureStore) {
  const stale = store.scenarioId === "downstream-stale";
  const builtAt = store.now();

  // For downstream-stale the revenue fact (ev-cf-doanh-thu) was confirmed at an
  // earlier case version and is now superseded; we do NOT invent a wire "stale"
  // field the parser lacks — we reference the supersession in the statement and
  // uncertainty text so the reviewer sees it honestly.
  const staleNote = stale
    ? " Lưu ý: dữ kiện doanh thu (ev-cf-doanh-thu) được xác nhận ở phiên bản hồ sơ trước và đã bị thay thế bởi chứng cứ mới hơn; cần xác nhận lại trước khi dựa vào con số này."
    : "";

  return {
    id: "uw-assessment-1",
    provenance: {
      case_id: store.case.id,
      case_version: store.case.version,
      agent_role: "CREDIT_UNDERWRITING",
      execution_id: "uw-exec-1",
      task_id: "uw-task-1",
      prompt_version: "underwriting-prompt@1",
      model_id: "synthetic-mock-model",
      endpoint_id: "synthetic-endpoint",
      evidence_view_built_at: builtAt,
      created_at: builtAt,
    },
    business: {
      findings: [
        finding({
          statementVi:
            "Công ty TNHH Thương mại Trường An hoạt động ổn định trong lĩnh vực thương mại thiết bị, có hợp đồng cung cấp đầu ra làm cơ sở cho nhu cầu vốn lưu động.",
          citations: [factCite("ev-cf-doanh-thu")],
          confidence: "MEDIUM",
          uncertaintyVi:
            "Chưa có đủ dữ kiện về mức độ tập trung khách hàng để loại trừ rủi ro phụ thuộc." +
            staleNote,
        }),
      ],
    },
    financial: {
      findings: [
        finding({
          statementVi:
            "Doanh thu 2025 đạt 48,2 tỷ VND và lợi nhuận ròng 3,1 tỷ VND theo báo cáo tài chính đã được cán bộ xác nhận.",
          citations: [factCite("ev-cf-doanh-thu"), factCite("ev-cf-loi-nhuan")],
          confidence: stale ? "LOW" : "HIGH",
          uncertaintyVi: stale
            ? "Số liệu doanh thu dựa trên dữ kiện đã bị thay thế; độ tin cậy hạ xuống cho tới khi xác nhận lại."
            : "",
        }),
      ],
    },
    cash_flow: {
      findings: [
        finding({
          statementVi:
            "Dòng tiền từ hoạt động kinh doanh ước tính khoảng 6,96 tỷ VND/năm, đủ để phủ nghĩa vụ trả nợ dự kiến của khoản vay 5 tỷ VND.",
          citations: [calcCite("calc-dscr"), factCite("ev-cf-loi-nhuan")],
          confidence: "MEDIUM",
          uncertaintyVi:
            "Ước tính dòng tiền chưa tính đến biến động mùa vụ; cần sao kê ngân hàng để kiểm chứng." +
            staleNote,
        }),
      ],
    },
    repayment_source: {
      findings: [
        finding({
          statementVi:
            "Nguồn trả nợ chính là dòng tiền từ hợp đồng cung cấp thiết bị và doanh thu thương mại thường xuyên.",
          citations: [factCite("ev-cf-doanh-thu"), calcCite("calc-dscr")],
          confidence: "MEDIUM",
          uncertaintyVi: "Phụ thuộc vào tiến độ thanh toán của bên mua theo hợp đồng đầu ra.",
        }),
      ],
      downside_scenarios: [
        finding({
          statementVi:
            "Kịch bản bất lợi: nếu doanh thu giảm 20%, hệ số DSCR giảm về khoảng 1,16 lần, vẫn trên 1,0 nhưng biên an toàn mỏng.",
          citations: [calcCite("calc-dscr")],
          confidence: "LOW",
          uncertaintyVi: "Kịch bản mang tính minh họa, chưa phản ánh chi phí lãi vay thực tế theo kỳ.",
        }),
      ],
    },
    proposed_structure: {
      instrument_vi: "Hạn mức vốn lưu động có thời hạn",
      proposed_amount_vnd: "5000000000",
      tenor_months: 12,
      findings: [
        finding({
          statementVi:
            "Đề xuất cấp hạn mức vốn lưu động 5 tỷ VND, kỳ hạn 12 tháng, giải ngân theo hợp đồng đầu ra và có tài sản bảo đảm.",
          citations: [calcCite("calc-dscr")],
          confidence: "MEDIUM",
          uncertaintyVi: "Cơ cấu đề xuất là dữ liệu tổng hợp; quyết định cuối thuộc thẩm quyền phê duyệt.",
        }),
      ],
    },
    risks: [
      {
        risk_id: "R1",
        description_vi:
          "Rủi ro tập trung đầu ra: phần lớn doanh thu đến từ một số ít hợp đồng cung cấp thiết bị.",
        citations: [factCite("ev-cf-doanh-thu")],
        confidence: "MEDIUM",
        uncertainty_vi: "Chưa có phân tích cơ cấu khách hàng chi tiết." + staleNote,
      },
      {
        risk_id: "R2",
        description_vi:
          "Rủi ro thanh khoản ngắn hạn nếu bên mua chậm thanh toán, ảnh hưởng dòng tiền trả nợ theo kỳ.",
        citations: [calcCite("calc-dscr"), factCite("ev-cf-loi-nhuan")],
        confidence: "LOW",
        uncertainty_vi: "Thiếu sao kê dòng tiền để lượng hóa độ trễ thanh toán.",
      },
    ],
    mitigants: [
      {
        risk_id: "R1",
        description_vi:
          "Yêu cầu đa dạng hóa hợp đồng đầu ra và theo dõi tỷ trọng khách hàng lớn trong 12 tháng.",
        citations: [factCite("ev-cf-doanh-thu")],
        confidence: "LOW",
        uncertainty_vi: "Hiệu quả phụ thuộc vào khả năng mở rộng tệp khách hàng của doanh nghiệp.",
      },
      {
        risk_id: "R2",
        description_vi:
          "Giải ngân bám theo hợp đồng và thu hồi qua tài khoản chỉ định để kiểm soát dòng tiền trả nợ.",
        citations: [calcCite("calc-dscr")],
        confidence: "MEDIUM",
        uncertainty_vi: "",
      },
    ],
    assumptions: [
      {
        statement_vi: "Giả định biên lợi nhuận gộp ổn định trong kỳ vay 12 tháng.",
        rationale_vi:
          "Dựa trên lợi nhuận ròng 2025 đã xác nhận; chưa có dữ kiện biến động giá đầu vào.",
        basis_citations: [factCite("ev-cf-loi-nhuan")],
      },
      {
        statement_vi: "Giả định hợp đồng đầu ra được thực hiện theo tiến độ cam kết.",
        rationale_vi: "Cần đối chiếu với sao kê dòng tiền để kiểm chứng.",
        basis_citations: [],
      },
    ],
    evidence_gaps: [
      {
        missing_information_vi:
          "Thiếu sao kê ngân hàng 6 tháng gần nhất để kiểm chứng dòng tiền hoạt động thực tế.",
        why_needed_vi:
          "Cần để xác nhận ước tính dòng tiền và độ ổn định của nguồn trả nợ chính.",
        blocking_level: "CONDITIONAL",
        suggested_evidence_vi: [
          "Sao kê tài khoản thanh toán 6 tháng gần nhất.",
          "Sổ phụ dòng tiền theo hợp đồng đầu ra.",
        ],
      },
    ],
    calculator_results: [buildDscrCalculator(stale)],
    trend_results: [],
    scenario_results: [],
  };
}

const getUnderwriting: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || !isCaseResource(r, "underwriting") || r.segments.length !== 5) {
    return null;
  }
  // Only the two scenarios that focus the underwriting stage get a rich read;
  // every other scenario returns null so the router falls back to the honest
  // UNDERWRITING_NOT_AVAILABLE empty state.
  if (store.scenarioId !== "clean-complete" && store.scenarioId !== "downstream-stale") {
    return null;
  }

  const body = {
    assessmentId: "uw-assessment-1",
    caseId: store.case.id,
    caseVersion: store.case.version,
    agentRole: "CREDIT_UNDERWRITING",
    executionId: "uw-exec-1",
    promptVersion: "underwriting-prompt@1",
    createdAt: store.now(),
    assessment: buildAssessment(store),
    handoff: {
      handoffId: store.handoff?.handoffId ?? "ho-uw-1",
      state: "READY_FOR_RISK_REVIEW",
      createdAt: store.now(),
    },
  };
  return ok(body);
};

export const underwritingHandlers: readonly FixtureHandler[] = [getUnderwriting];
