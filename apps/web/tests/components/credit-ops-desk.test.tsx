import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { CreditOpsDesk } from "../../components/credit-ops/credit-ops-desk";
import { ApiClientError } from "../../lib/api/client";
import type {
  ActionAuthorization,
  CreditOpsApiClient,
  CreditOpsStatus,
} from "../../lib/api/credit-ops";

// Characterization tests for the credit-ops desk ("Bàn tổng hợp gói hồ sơ").
// The component takes its api as a Pick<CreditOpsApiClient, ...> prop with a
// default of the real client, so — exactly like the orchestration-console and
// legal-assessment suites — we inject a fake api object directly rather than
// vi.mock the module boundary.

type FakeApi = Pick<
  CreditOpsApiClient,
  "getCreditOps" | "authorizeAction" | "approveDocumentRequest"
>;

function buildStatus(overrides: Partial<CreditOpsStatus> = {}): CreditOpsStatus {
  return {
    packageId: "pkg-00000000-1111",
    caseId: "case-1",
    caseVersion: 3,
    agentRole: "CREDIT_OPERATIONS",
    executionId: "exec-00000000-2222",
    promptVersion: "credit-ops-prompt-v1",
    createdAt: "2026-07-18T08:00:00Z",
    handoff: null,
    packageCompleteness: {
      artifacts: [
        {
          artifact: "INTAKE_HANDOFF",
          status: "PRESENT",
          detailVi: "Đã có bàn giao tiếp nhận.",
          referenceId: "handoff-1",
        },
        {
          artifact: "UNDERWRITING_ASSESSMENT",
          status: "PRESENT",
          detailVi: "Đã có thẩm định tín dụng.",
          referenceId: "uw-1",
        },
        {
          artifact: "LEGAL_ASSESSMENT",
          status: "PRESENT",
          detailVi: "Đã có pháp chế.",
          referenceId: "legal-1",
        },
        {
          artifact: "RISK_REVIEW_ASSESSMENT",
          status: "PRESENT",
          detailVi: "Đã có rà soát rủi ro.",
          referenceId: "risk-1",
        },
      ],
      dispositionsStateVi: "Đã xử lý toàn bộ thách thức.",
      unresolvedChallengeCount: 0,
      openBlockingGapCount: 0,
      allRequiredPresent: true,
    },
    evidenceConsolidation: {
      entries: [
        {
          artifact: "UNDERWRITING_ASSESSMENT",
          assessmentId: "uw-1",
          executionId: "exec-uw",
          handoffId: null,
          citationCount: 4,
        },
      ],
      distinctCitationCount: 4,
    },
    draftMemo: {
      present: true,
      syntheticDisclaimerVi: "Dữ liệu tổng hợp dùng cho trình diễn.",
      dispositionStatusVi: "Đã xử lý",
      sections: [
        { key: "tom_tat_nhu_cau", statementCount: 2, citationCount: 3 },
        { key: "phan_tich_maker", statementCount: 1, citationCount: 1 },
        { key: "ra_soat_phap_ly_tsbd", statementCount: 1, citationCount: 1 },
        { key: "thach_thuc_checker", statementCount: 0, citationCount: 0 },
        { key: "dieu_kien_de_xuat", statementCount: 1, citationCount: 0 },
        { key: "phu_luc_bang_chung", statementCount: 0, citationCount: 0 },
      ],
    },
    documentRequests: [
      {
        id: "req-1",
        originatingGapId: "gap-1",
        requestText: "Bổ sung báo cáo tài chính năm gần nhất.",
        blockingLevel: "BLOCKING",
        approvalStatus: "PENDING_APPROVAL",
        approvals: [],
      },
    ],
    proposedActions: [
      {
        id: "act-1",
        actionType: "PREPARE_DOCUMENT_REQUEST",
        description: "Chuẩn bị hồ sơ yêu cầu bổ sung tài liệu cho khách hàng.",
        executionStatus: "DRAFT",
        relatedDocumentRequestId: "req-1",
        authorized: false,
        authorizations: [],
      },
    ],
    g2GateStatus: "OPEN",
    g4GateStatus: "OPEN",
    ...overrides,
  };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    getCreditOps: vi.fn(async () => buildStatus()),
    authorizeAction: vi.fn(async () => ({
      id: "auth-1",
      actionId: "act-1",
      actorId: "officer-1",
      actorRole: "CREDIT_OPERATIONS_OFFICER",
      rationale: "ok",
      createdAt: "2026-07-18T09:00:00Z",
    })),
    approveDocumentRequest: vi.fn(async () => ({
      id: "appr-1",
      requestId: "req-1",
      actorId: "officer-1",
      actorRole: "CREDIT_OPERATIONS_OFFICER",
      rationale: "ok",
      createdAt: "2026-07-18T09:00:00Z",
    })),
    ...overrides,
  };
}

describe("CreditOpsDesk — loading, error, and 404 states", () => {
  it("shows the loading skeleton before the request resolves", () => {
    const api = fakeApi({
      getCreditOps: vi.fn(() => new Promise<CreditOpsStatus>(() => {})),
    });
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    expect(screen.getByLabelText("Đang tải gói tổng hợp")).toBeVisible();
  });

  it("shows a Vietnamese error and no data on API failure — no silent mock fallback", async () => {
    const api = fakeApi({
      getCreditOps: vi.fn().mockRejectedValue(new Error("network down")),
    });
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Không thể hoàn tất yêu cầu. Vui lòng thử lại.");
    // No package data — nothing renders as if the request had quietly succeeded.
    expect(screen.queryByText("Mã gói")).not.toBeInTheDocument();
    expect(screen.queryByText("Bàn giao gói hồ sơ")).not.toBeInTheDocument();
  });

  it("retries the load when 'Thử tải lại' is clicked after an error", async () => {
    const api = fakeApi({
      getCreditOps: vi
        .fn()
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValueOnce(buildStatus()),
    });
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));

    await waitFor(() => expect(api.getCreditOps).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Mã gói")).toBeVisible();
  });

  it("shows the not-available empty state (404) with links to the upstream desks", async () => {
    const api = fakeApi({
      getCreditOps: vi
        .fn()
        .mockRejectedValue(
          new ApiClientError(404, "CREDIT_OPS_NOT_AVAILABLE", "", false),
        ),
    });
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    expect(
      await screen.findByText("Chưa có gói tổng hợp cho phiên bản hồ sơ này"),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Mở thẩm định" })).toHaveAttribute(
      "href",
      "/ho-so/case-1/tham-dinh",
    );
    expect(screen.getByRole("link", { name: "Mở pháp chế" })).toHaveAttribute(
      "href",
      "/ho-so/case-1/phap-che",
    );
    expect(screen.getByRole("link", { name: "Mở rà soát rủi ro" })).toHaveAttribute(
      "href",
      "/ho-so/case-1/rui-ro",
    );
  });
});

describe("CreditOpsDesk — 403-mapped load error", () => {
  it("shows the role-specific Vietnamese message on a 403 (no distinct capability model beyond the generic error branch)", async () => {
    const api = fakeApi({
      getCreditOps: vi
        .fn()
        .mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    expect(
      await screen.findByText(
        "Bạn không có vai trò vận hành tín dụng để ghi ủy quyền hoặc phê duyệt.",
      ),
    ).toBeVisible();
    // No mutation surfaces render at all in this state — there is nothing to
    // authorize or approve because the package itself never loaded.
    expect(screen.queryByRole("button", { name: /Ghi phê duyệt/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ghi ủy quyền/ })).not.toBeInTheDocument();
  });
});

describe("CreditOpsDesk — package rendering from server data", () => {
  it("renders provenance, completeness checklist, evidence index, requests, actions, and manifest", async () => {
    const api = fakeApi();
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    expect(await screen.findByText("Mã gói")).toBeVisible();
    // Provenance
    expect(screen.getByText("v3")).toBeVisible();
    // Completeness checklist — labels come from ARTIFACT_LABELS. Each label
    // renders twice (once in the checklist, once in the assessments rollup).
    expect(screen.getAllByText("Thẩm định tín dụng").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Pháp chế & tài sản bảo đảm").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Rà soát rủi ro độc lập").length).toBeGreaterThan(0);
    // Evidence consolidation index header
    expect(screen.getByText("4 trích dẫn")).toBeVisible();
    // Document request + proposed action text
    expect(screen.getByText("Bổ sung báo cáo tài chính năm gần nhất.")).toBeVisible();
    expect(
      screen.getByText("Chuẩn bị hồ sơ yêu cầu bổ sung tài liệu cho khách hàng."),
    ).toBeVisible();
    // Handoff is gated off because gates aren't satisfied yet
    expect(screen.getByText("Bàn giao (chưa sẵn sàng)")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Mở bàn giao →" })).not.toBeInTheDocument();
  });

  it("offers the handoff link once every gate and artifact is satisfied", async () => {
    const readyStatus = buildStatus({
      g2GateStatus: "SATISFIED",
      g4GateStatus: "SATISFIED",
      documentRequests: [],
      proposedActions: [],
    });
    const api = fakeApi({ getCreditOps: vi.fn(async () => readyStatus) });
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    expect(await screen.findByText("Gói hồ sơ đã sẵn sàng bàn giao")).toBeVisible();
    expect(screen.getByRole("link", { name: "Mở bàn giao →" })).toHaveAttribute(
      "href",
      "/ho-so/case-1/ban-giao",
    );
  });
});

describe("CreditOpsDesk — document request approval flow", () => {
  it("requires a rationale: an empty submission does not call the API", async () => {
    const api = fakeApi();
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    await screen.findByText("Bổ sung báo cáo tài chính năm gần nhất.");
    await userEvent.click(screen.getByRole("button", { name: "Ghi phê duyệt yêu cầu" }));

    expect(
      screen.getByText("Nhập lý do trước khi ghi; đây là trường bắt buộc."),
    ).toBeVisible();
    expect(api.approveDocumentRequest).not.toHaveBeenCalled();
  });

  it("calls the API with the rationale on submit and re-fetches on success", async () => {
    const api = fakeApi();
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    await screen.findByText("Bổ sung báo cáo tài chính năm gần nhất.");
    const rationaleField = screen.getByLabelText(/Lý do phê duyệt/);
    await userEvent.type(rationaleField, "Đã đối chiếu hồ sơ khách hàng, đủ căn cứ phê duyệt.");
    await userEvent.click(screen.getByRole("button", { name: "Ghi phê duyệt yêu cầu" }));

    await waitFor(() =>
      expect(api.approveDocumentRequest).toHaveBeenCalledWith("case-1", "req-1", {
        rationale: "Đã đối chiếu hồ sơ khách hàng, đủ căn cứ phê duyệt.",
      }),
    );
    await waitFor(() => expect(api.getCreditOps).toHaveBeenCalledTimes(2));
  });

  it("shows the distinct 'recorded but could not reload' message when the write succeeds but the refresh fails", async () => {
    const api = fakeApi({
      getCreditOps: vi
        .fn()
        .mockResolvedValueOnce(buildStatus())
        .mockRejectedValueOnce(new Error("read hiccup")),
    });
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    await screen.findByText("Bổ sung báo cáo tài chính năm gần nhất.");
    const rationaleField = screen.getByLabelText(/Lý do phê duyệt/);
    await userEvent.type(rationaleField, "Đã đối chiếu hồ sơ, đủ căn cứ phê duyệt yêu cầu.");
    await userEvent.click(screen.getByRole("button", { name: "Ghi phê duyệt yêu cầu" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Đã ghi vào sổ, nhưng không tải lại được bản mới nhất: Không thể hoàn tất yêu cầu. Vui lòng thử lại.",
    );
    // The write itself is not reported as failed — the approve API was called
    // exactly once, and the (still-stale) request keeps its form rather than
    // showing a false "approved" state.
    expect(api.approveDocumentRequest).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Bổ sung báo cáo tài chính năm gần nhất.")).toBeVisible();
  });
});

describe("CreditOpsDesk — proposed action authorization flow (AuthorizationForm)", () => {
  it("does not call the API when the rationale is empty (validation blocks submission)", async () => {
    const api = fakeApi();
    render(<CreditOpsDesk api={api} caseId="case-1" />);

    await screen.findByText("Chuẩn bị hồ sơ yêu cầu bổ sung tài liệu cho khách hàng.");
    await userEvent.click(screen.getByRole("button", { name: "Ghi ủy quyền hành động" }));

    expect(
      screen.getByText("Nhập lý do trước khi ghi; đây là trường bắt buộc."),
    ).toBeVisible();
    expect(api.authorizeAction).not.toHaveBeenCalled();
  });

  it("awaits the server before clearing — no optimistic authorized state while pending", async () => {
    const api = fakeApi();
    let resolveAuthorize: (() => void) | undefined;
    api.authorizeAction = vi.fn(
      () =>
        new Promise<ActionAuthorization>((resolve) => {
          resolveAuthorize = () =>
            resolve({
              id: "auth-1",
              actionId: "act-1",
              actorId: "officer-1",
              actorRole: "CREDIT_OPERATIONS_OFFICER",
              rationale: "ok",
              createdAt: "2026-07-18T09:00:00Z",
            });
        }),
    );

    render(<CreditOpsDesk api={api} caseId="case-1" />);

    await screen.findByText("Chuẩn bị hồ sơ yêu cầu bổ sung tài liệu cho khách hàng.");
    const rationaleField = screen.getByLabelText(/Lý do ủy quyền/);
    await userEvent.type(rationaleField, "Đã rà soát và đồng ý cho phép thực hiện.");
    await userEvent.click(screen.getByRole("button", { name: "Ghi ủy quyền hành động" }));

    // Still pending: button shows the busy label, the action is still shown
    // as awaiting authorization (StatusChip has not optimistically flipped),
    // and the read has not been re-issued yet.
    expect(await screen.findByRole("button", { name: "Đang ghi vào sổ…" })).toBeVisible();
    expect(screen.getByText("Chờ ủy quyền")).toBeVisible();
    expect(api.getCreditOps).toHaveBeenCalledTimes(1);

    resolveAuthorize?.();

    await waitFor(() => expect(api.getCreditOps).toHaveBeenCalledTimes(2));
  });
});
