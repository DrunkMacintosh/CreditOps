import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import React from "react";
import { describe, expect, it } from "vitest";

import { ApiClientError } from "../../lib/api/client";
import {
  UNDERWRITING_NOT_AVAILABLE,
  parseUnderwritingAssessment,
  type UnderwritingAssessmentView,
} from "../../lib/api/underwriting";
import { UnderwritingWorksheet } from "../../components/underwriting/underwriting-worksheet";

// A wire payload shaped exactly like the BFF response: a camelCase envelope
// whose `assessment` is the domain model_dump (snake_case, Decimals as strings).
function rawWire() {
  return {
    assessmentId: "50000000-0000-0000-0000-000000000001",
    caseId: "case-uw-1",
    caseVersion: 2,
    agentRole: "CREDIT_UNDERWRITING",
    executionId: "e1e2e3e4-aaaa-bbbb-cccc-dddddddddddd",
    promptVersion: "underwriting-prompt-v1",
    createdAt: "2026-07-18T10:00:00Z",
    assessment: {
      id: "50000000-0000-0000-0000-000000000001",
      provenance: {
        case_id: "case-uw-1",
        case_version: 2,
        agent_role: "CREDIT_UNDERWRITING",
        execution_id: "e1e2e3e4-aaaa-bbbb-cccc-dddddddddddd",
        task_id: "task-1",
        prompt_version: "underwriting-prompt-v1",
        model_id: "synthetic-mock-model",
        endpoint_id: "endpoint-1",
        evidence_view_built_at: "2026-07-18T09:57:00Z",
        created_at: "2026-07-18T09:59:00Z",
      },
      business: {
        findings: [
          {
            statement_vi: "Doanh nghiệp hoạt động ổn định trong ngành thực phẩm.",
            citations: [{ kind: "CALCULATOR_RESULT", result_id: "calc_curr" }],
            confidence: "HIGH",
            uncertainty_vi: "",
          },
        ],
      },
      financial: { findings: [] },
      cash_flow: { findings: [] },
      repayment_source: { findings: [], downside_scenarios: [] },
      proposed_structure: {
        instrument_vi: "Hạn mức vốn lưu động",
        proposed_amount_vnd: "5000000000",
        tenor_months: 12,
        findings: [
          {
            statement_vi: "Cấu trúc đề xuất căn cứ nhu cầu vốn lưu động đã tính.",
            citations: [{ kind: "CALCULATOR_RESULT", result_id: "calc_curr" }],
            confidence: "MEDIUM",
            uncertainty_vi: "",
          },
        ],
      },
      risks: [
        {
          risk_id: "R1",
          description_vi: "Phụ thuộc vào một số khách hàng lớn.",
          citations: [{ kind: "CONFIRMED_FACT", confirmed_fact_id: "fact-rev" }],
          confidence: "MEDIUM",
          uncertainty_vi: "",
        },
      ],
      mitigants: [
        {
          risk_id: "R1",
          description_vi: "Đa dạng hóa nguồn thu trong 12 tháng tới.",
          citations: [{ kind: "CONFIRMED_FACT", confirmed_fact_id: "fact-rev" }],
          confidence: "LOW",
          uncertainty_vi: "",
        },
      ],
      assumptions: [
        {
          statement_vi: "Giả định giá vốn ổn định trong kỳ.",
          rationale_vi: "Chưa có dữ kiện biến động giá đầu vào.",
          basis_citations: [],
        },
      ],
      evidence_gaps: [
        {
          missing_information_vi: "Thiếu dữ kiện đã xác nhận cho trường 'financials.inventory'.",
          why_needed_vi: "Cần cho tính toán hệ số thanh toán nhanh.",
          blocking_level: "BLOCKING",
          suggested_evidence_vi: ["Báo cáo tài chính đã xác nhận bởi cán bộ phụ trách."],
        },
      ],
      calculator_results: [
        {
          result_id: "calc_curr",
          calculator: "current_ratio",
          inputs: [
            {
              name: "current_assets",
              value: "1000000000",
              fact_refs: [{ kind: "CONFIRMED_FACT", ref_id: "fact-ca" }],
            },
            {
              name: "current_liabilities",
              value: "500000000",
              fact_refs: [{ kind: "CONFIRMED_FACT", ref_id: "fact-cl" }],
            },
          ],
          outcome: { status: "COMPUTED", value: "2.000000" },
        },
        {
          result_id: "calc_quick",
          calculator: "quick_ratio",
          inputs: [
            {
              name: "current_assets",
              value: "1000000000",
              fact_refs: [{ kind: "CONFIRMED_FACT", ref_id: "fact-ca" }],
            },
            { name: "inventory", value: null, fact_refs: [] },
            {
              name: "current_liabilities",
              value: "500000000",
              fact_refs: [{ kind: "CONFIRMED_FACT", ref_id: "fact-cl" }],
            },
          ],
          outcome: {
            status: "NOT_COMPUTABLE",
            reason: "not computable: missing input inventory",
          },
        },
      ],
      trend_results: [
        {
          result_id: "trend_rev",
          calculator: "trend_analysis",
          metric: "revenue",
          points: [
            {
              period: "ky_truoc",
              value: "800000000",
              fact_refs: [{ kind: "CONFIRMED_FACT", ref_id: "fact-prev" }],
            },
            {
              period: "ky_hien_tai",
              value: "1000000000",
              fact_refs: [{ kind: "CONFIRMED_FACT", ref_id: "fact-cur" }],
            },
          ],
          steps: [
            {
              from_period: "ky_truoc",
              to_period: "ky_hien_tai",
              delta: { status: "COMPUTED", value: "200000000" },
              growth_rate: { status: "COMPUTED", value: "0.250000" },
            },
          ],
        },
      ],
      scenario_results: [
        {
          result_id: "scn_down",
          calculator: "scenario_projection",
          scenario_name: "doanh_thu_giam_20pct",
          adjustments: [
            { metric: "revenue", relative_change: "-0.200000", absolute_change: "0" },
          ],
          metrics: [
            {
              metric: "revenue",
              base: { status: "COMPUTED", value: "1000000000" },
              adjusted: { status: "COMPUTED", value: "800000000" },
            },
          ],
          inputs: [],
        },
      ],
    },
    handoff: {
      handoffId: "h1",
      state: "READY_FOR_RISK_REVIEW",
      createdAt: "2026-07-18T10:01:00Z",
    },
  };
}

function view(): UnderwritingAssessmentView {
  return parseUnderwritingAssessment(rawWire());
}

describe("underwriting parser", () => {
  it("maps the camelCase envelope and snake_case assessment body", () => {
    const parsed = view();
    expect(parsed.assessmentId).toBe("50000000-0000-0000-0000-000000000001");
    expect(parsed.caseVersion).toBe(2);
    expect(parsed.handoff?.state).toBe("READY_FOR_RISK_REVIEW");
    expect(parsed.assessment.provenance?.modelId).toBe("synthetic-mock-model");
  });

  it("parses decimal strings into numbers and keeps computed/not-computable outcomes", () => {
    const parsed = view();
    const [current, quick] = parsed.assessment.calculatorResults;
    expect(current.calculator).toBe("current_ratio");
    expect(current.outcome).toEqual({ status: "COMPUTED", value: 2, raw: "2.000000" });
    expect(current.inputs[0].value).toBe(1000000000);
    expect(quick.outcome.status).toBe("NOT_COMPUTABLE");
  });

  it("throws a typed error when the assessment id is missing", () => {
    expect(() => parseUnderwritingAssessment({ caseId: "x" })).toThrow(/thẩm định/);
  });
});

describe("UnderwritingWorksheet loader", () => {
  it("renders figures, ratios, gates and evidence once loaded", async () => {
    render(<UnderwritingWorksheet caseId="case-uw-1" load={async () => view()} />);

    await waitFor(() =>
      expect(screen.getByText("Hồ sơ làm việc thẩm định")).toBeInTheDocument(),
    );

    // Financial figure with vi-VN grouping and provenance.
    expect(screen.getByText("Tài sản ngắn hạn")).toBeInTheDocument();
    expect(screen.getAllByText(/1\.000\.000\.000\s*đ/).length).toBeGreaterThan(0);

    // Computed ratio value and computed gate. The metric label also appears as
    // a calculator-result evidence chip, so it is present more than once.
    expect(screen.getAllByText("Hệ số thanh toán hiện hành").length).toBeGreaterThan(0);
    expect(screen.getByText("2,00 lần")).toBeInTheDocument();
    expect(screen.getAllByText("Đã tính").length).toBeGreaterThan(0);

    // Not-computable ratio surfaces the missing-evidence gate.
    expect(screen.getByText("Thiếu dữ kiện")).toBeInTheDocument();

    // Handoff status and blocking gap gate.
    expect(screen.getByText("Sẵn sàng chuyển thẩm định rủi ro")).toBeInTheDocument();
    expect(screen.getByText("Chặn")).toBeInTheDocument();

    // Evidence-chain chips render.
    expect(screen.getAllByText("Dữ kiện đã xác nhận").length).toBeGreaterThan(0);
  });

  it("shows the empty state and a link to quy-trinh when no assessment exists", async () => {
    render(
      <UnderwritingWorksheet
        caseId="case-uw-1"
        load={async () => {
          throw new ApiClientError(404, UNDERWRITING_NOT_AVAILABLE, "", false);
        }}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText("Bản thẩm định sẽ xuất hiện sau khi xử lý xong"),
      ).toBeInTheDocument(),
    );
    const link = screen.getByRole("link", { name: "Xem quy trình xử lý" });
    expect(link).toHaveAttribute("href", "/ho-so/case-uw-1/quy-trinh");
  });

  it("shows a recoverable error with retry on service failure", async () => {
    render(
      <UnderwritingWorksheet
        caseId="case-uw-1"
        load={async () => {
          throw new ApiClientError(
            503,
            "UNDERWRITING_SERVICE_UNAVAILABLE",
            "Dịch vụ thẩm định tín dụng chưa sẵn sàng.",
            true,
          );
        }}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText(/Dịch vụ thẩm định tín dụng chưa sẵn sàng/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Thử tải lại" })).toBeInTheDocument();
  });
});
