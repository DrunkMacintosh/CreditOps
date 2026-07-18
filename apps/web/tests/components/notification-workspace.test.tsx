import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { NotificationWorkspace } from "../../components/notifications/notification-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  NotificationsApiClient,
  NotificationStatus,
} from "../../lib/api/notifications";

// Characterization tests for the stage-7 notification workspace. The component
// takes its api as a Pick<NotificationsApiClient, ...> prop; we inject a fake.

type FakeApi = Pick<
  NotificationsApiClient,
  "getStatus" | "createDraft" | "approve" | "deliver"
>;

function buildStatus(overrides: Partial<NotificationStatus> = {}): NotificationStatus {
  return {
    draft: {
      id: "draft-1111-4111-8111-111111111111",
      caseId: "case-1",
      caseVersion: 3,
      decisionId: "decision-1",
      content: "Ngân hàng thông báo khoản tín dụng đã được phê duyệt.",
      contentHash: "a".repeat(64),
      createdBy: "maker-1",
      createdAt: "2026-07-18T08:00:00Z",
    },
    receipt: null,
    approvalGateStatus: "OPEN",
    ...overrides,
  };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    getStatus: vi.fn(async () => buildStatus()),
    createDraft: vi.fn(async () => buildStatus().draft!),
    approve: vi.fn(async () => ({
      gateType: "HG_CREDIT_NOTIFICATION_APPROVED",
      status: "SATISFIED",
      draftId: "draft-1111-4111-8111-111111111111",
      dispositionRef: "notification-draft:draft-1",
    })),
    deliver: vi.fn(async () => ({
      id: "receipt-1",
      draftId: "draft-1111-4111-8111-111111111111",
      deliveredVia: "MOCK",
      contentHash: "a".repeat(64),
      receiptNote: "Đã ghi nhận giao thông báo qua kênh mock.",
      recordedBy: "checker-1",
      createdAt: "2026-07-18T09:00:00Z",
    })),
    ...overrides,
  };
}

describe("NotificationWorkspace — states", () => {
  it("shows the loading skeleton before the request resolves", () => {
    const api = fakeApi({ getStatus: vi.fn(() => new Promise<NotificationStatus>(() => {})) });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    expect(screen.getByLabelText("Đang tải thông báo tín dụng")).toBeVisible();
  });

  it("shows a Vietnamese error and a retry on API failure", async () => {
    const api = fakeApi({ getStatus: vi.fn().mockRejectedValue(new Error("network")) });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Không thể hoàn tất yêu cầu. Vui lòng thử lại.");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.getStatus).toHaveBeenCalledTimes(2));
  });

  it("renders NO mutation controls on a 403 load", async () => {
    const api = fakeApi({
      getStatus: vi.fn().mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Bạn không có vai trò tham gia hồ sơ để xem thông báo tín dụng."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Duyệt nội dung thông báo tín dụng" }),
    ).not.toBeInTheDocument();
  });

  it("always shows the mandatory 'not a disbursement' disclaimer", async () => {
    const api = fakeApi();
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Thông báo tín dụng không phải xác nhận giải ngân."),
    ).toBeVisible();
  });

  it("shows the empty state and a create action when there is no draft", async () => {
    const api = fakeApi({ getStatus: vi.fn(async () => buildStatus({ draft: null })) });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Chưa có bản nháp thông báo tín dụng")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Tạo bản nháp thông báo tín dụng" }),
    );
    await waitFor(() => expect(api.createDraft).toHaveBeenCalledWith("case-1"));
  });

  it("renders the unsupported label for an unknown gate status (fail closed)", async () => {
    const api = fakeApi({ getStatus: vi.fn(async () => buildStatus({ approvalGateStatus: "WAT" })) });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Trạng thái chưa được hỗ trợ/)).toBeVisible();
  });
});

describe("NotificationWorkspace — approval gate", () => {
  it("requires a rationale: an empty submit does not call the API", async () => {
    const api = fakeApi();
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nội dung bản nháp thông báo");
    await userEvent.click(
      screen.getByRole("button", { name: "Duyệt nội dung thông báo tín dụng" }),
    );
    expect(screen.getByText("Nhập lý do trước khi ghi; đây là trường bắt buộc.")).toBeVisible();
    expect(api.approve).not.toHaveBeenCalled();
  });

  it("approves pinned to the exact draft id and refetches", async () => {
    const api = fakeApi();
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nội dung bản nháp thông báo");
    await userEvent.type(
      screen.getByLabelText(/Lý do phê duyệt nội dung thông báo/),
      "Đã rà soát nội dung thông báo, đủ căn cứ phê duyệt.",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Duyệt nội dung thông báo tín dụng" }),
    );
    await waitFor(() =>
      expect(api.approve).toHaveBeenCalledWith("case-1", {
        draftId: "draft-1111-4111-8111-111111111111",
        rationale: "Đã rà soát nội dung thông báo, đủ căn cứ phê duyệt.",
      }),
    );
    await waitFor(() => expect(api.getStatus).toHaveBeenCalledTimes(2));
  });

  it("keeps the draft and offers a reload on a 409 stale draft", async () => {
    const api = fakeApi({
      approve: vi.fn().mockRejectedValue(new ApiClientError(409, "STALE_NOTIFICATION_DRAFT", "", false)),
    });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nội dung bản nháp thông báo");
    await userEvent.type(screen.getByLabelText(/Lý do phê duyệt nội dung thông báo/), "Duyệt.");
    await userEvent.click(
      screen.getByRole("button", { name: "Duyệt nội dung thông báo tín dụng" }),
    );
    expect(await screen.findByRole("button", { name: "Tải lại" })).toBeVisible();
  });
});

describe("NotificationWorkspace — delivery gate", () => {
  it("shows the mock-delivery section only once the gate is satisfied", async () => {
    const api = fakeApi({
      getStatus: vi.fn(async () => buildStatus({ approvalGateStatus: "SATISFIED" })),
    });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByRole("button", { name: "Ghi nhận giao nhận mô phỏng" }),
    ).toBeVisible();
    // The approval form is gone once the gate is satisfied.
    expect(
      screen.queryByRole("button", { name: "Duyệt nội dung thông báo tín dụng" }),
    ).not.toBeInTheDocument();
  });

  it("renders the recorded mock receipt when one exists", async () => {
    const api = fakeApi({
      getStatus: vi.fn(async () =>
        buildStatus({
          approvalGateStatus: "SATISFIED",
          receipt: {
            id: "receipt-1",
            draftId: "draft-1111-4111-8111-111111111111",
            deliveredVia: "MOCK",
            contentHash: "a".repeat(64),
            receiptNote: "Đã ghi nhận giao thông báo qua kênh mock.",
            recordedBy: "checker-1",
            createdAt: "2026-07-18T09:00:00Z",
          },
        }),
      ),
    });
    render(<NotificationWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Biên nhận giao nhận mô phỏng")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Ghi nhận giao nhận mô phỏng" }),
    ).not.toBeInTheDocument();
  });
});
