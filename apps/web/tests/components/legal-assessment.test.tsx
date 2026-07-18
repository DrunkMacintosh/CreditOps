import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import React from "react";
import { describe, expect, it } from "vitest";

import {
  LegalAssessmentScreen,
  LegalAssessmentView,
} from "../../components/legal/legal-assessment";
import { ApiClientError } from "../../lib/api/client";
import { parseLegalAssessment, type LegalApi } from "../../lib/api/legal";

// A wire payload shaped exactly like the BFF response: a camelCase envelope
// whose `assessment` is the domain model_dump (snake_case throughout).
function rawWire() {
  return {
    assessmentId: "a1b2c3d4-1111-2222-3333-444455556666",
    caseId: "case-legal-1",
    caseVersion: 3,
    agentRole: "LEGAL_COMPLIANCE_COLLATERAL",
    executionId: "e1e2e3e4-aaaa-bbbb-cccc-dddddddddddd",
    promptVersion: "legal-prompt-v1",
    createdAt: "2026-07-18T08:00:00Z",
    assessment: {
      id: "a1b2c3d4-1111-2222-3333-444455556666",
      provenance: {
        case_id: "case-legal-1",
        case_version: 3,
        agent_role: "LEGAL_COMPLIANCE_COLLATERAL",
        execution_id: "e1e2e3e4-aaaa-bbbb-cccc-dddddddddddd",
        task_id: "task-1",
        prompt_version: "legal-prompt-v1",
        model_id: "synthetic-mock-model",
        endpoint_id: "endpoint-1",
        evidence_view_built_at: "2026-07-18T07:57:00Z",
        created_at: "2026-07-18T07:59:00Z",
      },
      legal_entity_review: {
        findings: [
          {
            statement_vi: "Pháp nhân có đăng ký doanh nghiệp hợp lệ.",
            citations: [{ kind: "CONFIRMED_FACT", confirmed_fact_id: "f1a2b3c4-0000" }],
            confidence: "HIGH",
            uncertainty_vi: "",
          },
        ],
      },
      authority_signatory_review: {
        findings: [
          {
            statement_vi: "Người ký có thẩm quyền theo điều lệ.",
            citations: [{ kind: "DOCUMENT_REGION", document_version_id: "d1", region: "Trang 2" }],
            confidence: "MEDIUM",
            uncertainty_vi: "Chưa có bản cập nhật điều lệ mới nhất.",
          },
        ],
      },
      ownership_consistency: {
        findings: [
          {
            statement_vi: "Tên chủ sở hữu nhất quán trên các tài liệu.",
            citations: [{ kind: "CONFIRMED_FACT", confirmed_fact_id: "f2" }],
            confidence: "HIGH",
            uncertainty_vi: "",
          },
        ],
        inconsistencies: [
          {
            description_vi: "Tên chủ thể đăng ký không khớp với tài sản bảo đảm.",
            citations: [
              { kind: "CONFIRMED_FACT", confirmed_fact_id: "f1" },
              { kind: "CONFIRMED_FACT", confirmed_fact_id: "f2" },
            ],
            detected_by: "DETERMINISTIC_CROSS_CHECK",
            confidence: "HIGH",
          },
        ],
      },
      policy_review: [
        {
          possible_issue_vi: "Có thể áp dụng điều khoản về tài sản bảo đảm.",
          citations: [
            {
              kind: "POLICY_CITATION",
              corpus_id: "corpus-synthetic",
              corpus_version: "v1",
              document_id: "tai_san_bao_dam",
              clause_id: "TSBD-01",
              quoted_text_vi: "Tài sản bảo đảm phải có giấy chứng nhận hợp lệ.",
            },
          ],
          confidence: "MEDIUM",
          uncertainty_vi: "Cần đối chiếu bản gốc.",
        },
      ],
      controlled_check_interpretations: [
        {
          invocation_id: "inv-hit",
          statement_vi: "Kết quả trùng khớp danh sách cảnh báo cần con người rà soát.",
          confidence: "LOW",
          uncertainty_vi: "Có thể trùng tên.",
        },
      ],
      collateral_review: {
        document_items: [
          {
            document_type_key: "hop_dong_the_chap",
            label_vi: "Hợp đồng thế chấp tài sản bảo đảm",
            status: "PRESENT",
            citations: [{ kind: "CONFIRMED_FACT", confirmed_fact_id: "f4" }],
            expiry_date: null,
            notes_vi: "",
          },
          {
            document_type_key: "giay_chung_nhan_qsdd",
            label_vi: "Giấy chứng nhận quyền sử dụng đất",
            status: "MISSING",
            citations: [{ kind: "CONFIRMED_FACT", confirmed_fact_id: "f3" }],
            expiry_date: null,
            notes_vi: "",
          },
        ],
        ownership_evidence_findings: [
          {
            statement_vi: "Chứng cứ quyền sở hữu dẫn chiếu tài liệu định giá.",
            citations: [{ kind: "DOCUMENT_REGION", document_version_id: "d2", region: "Trang 1" }],
            confidence: "HIGH",
            uncertainty_vi: "",
          },
        ],
      },
      exceptions: [
        {
          category: "COLLATERAL",
          possible_issue_vi: "Thiếu tài liệu tài sản bảo đảm bắt buộc.",
          citations: [{ kind: "CONFIRMED_FACT", confirmed_fact_id: "f3" }],
          confidence: "MEDIUM",
          uncertainty_vi: "Cần cán bộ xác minh với khách hàng.",
        },
      ],
      assumptions: [],
      evidence_gaps: [
        {
          missing_information_vi: "Chưa làm rõ ngày định giá gần nhất.",
          why_needed_vi: "Để xác định tài liệu còn hiệu lực.",
          blocking_level: "CLARIFICATION",
          suggested_evidence_vi: [],
        },
        {
          missing_information_vi: "Thiếu giấy chứng nhận quyền sử dụng đất.",
          why_needed_vi: "Bắt buộc theo danh mục kiểm tra tài sản bảo đảm.",
          blocking_level: "BLOCKING",
          suggested_evidence_vi: ["Giấy chứng nhận quyền sử dụng đất do cán bộ xác nhận."],
        },
      ],
      policy_hits: [
        {
          corpus_id: "corpus-synthetic",
          corpus_version: "v1",
          document_id: "tai_san_bao_dam",
          clause_id: "TSBD-01",
          quoted_text_vi: "Tài sản bảo đảm phải có giấy chứng nhận hợp lệ.",
        },
      ],
      policy_corpus_ref: {
        corpus_id: "corpus-synthetic",
        version: "v1",
        checksum_sha256: "abc123",
        is_synthetic: true,
      },
      controlled_check_results: [
        {
          invocation_id: "inv-clear",
          check_type: "KYC",
          provider_id: "synthetic-mock-compliance-provider",
          tool_name: "kyc-check",
          tool_version: "1.0",
          subject_type: "ENTITY",
          subject_ref_vi: "Công ty TNHH A",
          status: "CLEAR",
          result_summary_vi: "Không phát hiện vấn đề định danh.",
          invoked_at: "2026-07-18T07:58:00Z",
          is_mock: true,
        },
        {
          invocation_id: "inv-hit",
          check_type: "AML_WATCHLIST",
          provider_id: "synthetic-mock-compliance-provider",
          tool_name: "aml-check",
          tool_version: "1.0",
          subject_type: "ENTITY",
          subject_ref_vi: "Công ty TNHH A",
          status: "HIT",
          result_summary_vi: "Có kết quả trùng khớp danh sách cảnh báo.",
          invoked_at: "2026-07-18T07:58:30Z",
          is_mock: true,
        },
      ],
    },
    handoff: {
      handoffId: "h1",
      state: "READY_FOR_RISK_REVIEW",
      createdAt: "2026-07-18T08:01:00Z",
    },
  };
}

function fakeApi(
  impl: (caseId: string) => Promise<unknown>,
): Pick<LegalApi, "getLegalAssessment"> {
  return {
    getLegalAssessment: async (caseId: string) =>
      parseLegalAssessment(await impl(caseId)) as never,
  };
}

describe("legal assessment parser", () => {
  it("maps the camelCase envelope and snake_case assessment body", () => {
    const parsed = parseLegalAssessment(rawWire());
    expect(parsed.assessmentId).toBe("a1b2c3d4-1111-2222-3333-444455556666");
    expect(parsed.caseVersion).toBe(3);
    expect(parsed.handoff?.state).toBe("READY_FOR_RISK_REVIEW");
    expect(parsed.assessment.policyCorpusRef?.isSynthetic).toBe(true);

    const checks = parsed.assessment.controlledCheckResults;
    expect(checks).toHaveLength(2);
    expect(checks[1].checkType).toBe("AML_WATCHLIST");
    expect(checks[1].status).toBe("HIT");

    const firstFact = parsed.assessment.legalEntityReview[0].citations[0];
    expect(firstFact).toEqual({ kind: "CONFIRMED_FACT", confirmedFactId: "f1a2b3c4-0000" });
    expect(parsed.assessment.collateralReview.documentItems[1].status).toBe("MISSING");
  });

  it("throws a typed error when the assessment body is missing", () => {
    expect(() => parseLegalAssessment({ assessmentId: "x" })).toThrow(
      /không hợp lệ/,
    );
  });
});

describe("LegalAssessmentView", () => {
  it("surfaces not-passing checks first and shows gate + evidence chips", () => {
    render(<LegalAssessmentView data={parseLegalAssessment(rawWire())} />);

    const aml = screen.getByText("Sàng lọc danh sách cảnh báo (AML)");
    const kyc = screen.getByText("Định danh khách hàng (KYC)");
    // Not-passing (HIT) must render before the cleared KYC check.
    expect(aml.compareDocumentPosition(kyc) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    expect(screen.getAllByText("Chưa đạt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Đạt").length).toBeGreaterThan(0);
    expect(screen.getByText("Diễn giải kết quả")).toBeInTheDocument();
    expect(screen.getAllByText("chứng cứ · dữ kiện").length).toBeGreaterThan(0);
  });

  it("orders collateral by missing-first and renders the checklist", () => {
    render(<LegalAssessmentView data={parseLegalAssessment(rawWire())} />);
    const missing = screen.getByText("Giấy chứng nhận quyền sử dụng đất");
    const present = screen.getByText("Hợp đồng thế chấp tài sản bảo đảm");
    expect(missing.compareDocumentPosition(present) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders exceptions, blocking-first gaps, and the authority boundary", () => {
    render(<LegalAssessmentView data={parseLegalAssessment(rawWire())} />);
    expect(screen.getByText("Ngoại lệ cần rà soát")).toBeInTheDocument();
    expect(screen.getByText("Thiếu tài liệu tài sản bảo đảm bắt buộc.")).toBeInTheDocument();

    const blocking = screen.getByText("Thiếu giấy chứng nhận quyền sử dụng đất.");
    const clarification = screen.getByText("Chưa làm rõ ngày định giá gần nhất.");
    expect(
      blocking.compareDocumentPosition(clarification) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    expect(screen.getByText(/quyết định thuộc về cán bộ/)).toBeInTheDocument();
    expect(screen.getByText(/Dữ liệu tổng hợp dùng cho trình diễn/)).toBeInTheDocument();
  });
});

describe("LegalAssessmentScreen loader", () => {
  it("renders the assessment once loaded", async () => {
    render(
      <LegalAssessmentScreen api={fakeApi(async () => rawWire())} caseId="case-legal-1" />,
    );
    await waitFor(() =>
      expect(screen.getByText("Rà soát pháp lý và tuân thủ")).toBeInTheDocument(),
    );
  });

  it("shows an inviting empty state when no assessment exists yet", async () => {
    const api: Pick<LegalApi, "getLegalAssessment"> = {
      getLegalAssessment: async () => {
        throw new ApiClientError(404, "LEGAL_ASSESSMENT_NOT_AVAILABLE", "", false);
      },
    };
    render(<LegalAssessmentScreen api={api} caseId="case-legal-1" />);
    await waitFor(() =>
      expect(screen.getByText("Chưa có bản rà soát pháp chế")).toBeInTheDocument(),
    );
  });

  it("shows a recoverable error with retry on service failure", async () => {
    const api: Pick<LegalApi, "getLegalAssessment"> = {
      getLegalAssessment: async () => {
        throw new ApiClientError(503, "LEGAL_SERVICE_UNAVAILABLE", "", true);
      },
    };
    render(<LegalAssessmentScreen api={api} caseId="case-legal-1" />);
    await waitFor(() =>
      expect(
        screen.getByText(/Dịch vụ pháp chế tạm thời chưa sẵn sàng/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Thử tải lại" })).toBeInTheDocument();
  });
});
