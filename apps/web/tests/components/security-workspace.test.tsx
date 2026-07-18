import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { SecurityWorkspace } from "../../components/security/security-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  InterestWithItems,
  PerfectionStatus,
  SecurityInterestsApiClient,
  SecurityLedger,
} from "../../lib/api/security-interests";

type FakeApi = Pick<
  SecurityInterestsApiClient,
  "getLedger" | "createInterest" | "addItem" | "transitionItem" | "confirm"
>;

function buildInterest(
  itemStatus: PerfectionStatus | string = "PENDING",
  overrides: Partial<InterestWithItems> = {},
): InterestWithItems {
  return {
    interest: {
      id: "interest-1",
      caseId: "case-1",
      caseVersion: 3,
      assetDescription: "Nhà đất số 10",
      assetKind: "REAL_ESTATE",
      ownerName: "Ông A",
      valuationReference: null,
      notes: null,
      createdBy: "maker-1",
      createdAt: "2026-07-18T08:00:00Z",
    },
    items: [
      {
        id: "item-1",
        interestId: "interest-1",
        requirement: "Đăng ký thế chấp tại văn phòng đăng ký đất đai",
        status: itemStatus,
        evidenceRefs: [],
        filingReference: null,
        effectiveDate: null,
        expiryDate: null,
        completedBy: null,
        completedAt: null,
        createdAt: "2026-07-18T08:05:00Z",
      },
    ],
    ...overrides,
  };
}

function buildLedger(interests: InterestWithItems[] = [buildInterest()]): SecurityLedger {
  return { interests };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    getLedger: vi.fn(async () => buildLedger()),
    createInterest: vi.fn(async () => buildInterest().interest),
    addItem: vi.fn(async () => buildInterest().items[0]),
    transitionItem: vi.fn(async () => buildInterest().items[0]),
    confirm: vi.fn(async () => ({
      gateType: "HG_SECURITY_PERFECTION_CONFIRMED",
      status: "SATISFIED",
      dispositionRef: "security-perfection:3",
    })),
    ...overrides,
  };
}

describe("SecurityWorkspace — states", () => {
  it("shows the loading skeleton before the request resolves", () => {
    const api = fakeApi({ getLedger: vi.fn(() => new Promise<SecurityLedger>(() => {})) });
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    expect(screen.getByLabelText("Đang tải biện pháp bảo đảm")).toBeVisible();
  });

  it("shows an error and a retry on API failure", async () => {
    const api = fakeApi({ getLedger: vi.fn().mockRejectedValue(new Error("network")) });
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.getLedger).toHaveBeenCalledTimes(2));
  });

  it("renders NO ledger controls on a 403 load", async () => {
    const api = fakeApi({
      getLedger: vi.fn().mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Bạn không có thẩm quyền xem sổ hoàn thiện biện pháp bảo đảm."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Thêm biện pháp bảo đảm" }),
    ).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no interests", async () => {
    const api = fakeApi({ getLedger: vi.fn(async () => buildLedger([])) });
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Chưa có biện pháp bảo đảm nào")).toBeVisible();
  });

  it("renders the per-interest item ledger with kind and status labels", async () => {
    const api = fakeApi();
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Nhà đất số 10")).toBeVisible();
    // Asset-kind chip + item status label.
    expect(screen.getAllByText("Bất động sản").length).toBeGreaterThan(0);
    expect(screen.getByText("Chờ xử lý")).toBeVisible();
    expect(
      screen.getByText("Đăng ký thế chấp tại văn phòng đăng ký đất đai"),
    ).toBeVisible();
  });

  it("renders the unsupported label for an unknown item status (fail closed)", async () => {
    const api = fakeApi({ getLedger: vi.fn(async () => buildLedger([buildInterest("WEIRD")])) });
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Trạng thái chưa được hỗ trợ/)).toBeVisible();
  });
});

describe("SecurityWorkspace — write flows", () => {
  it("does not preselect an asset kind and requires one before creating", async () => {
    const api = fakeApi();
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Thêm biện pháp bảo đảm" });
    // No asset-kind radio is checked on first render.
    expect(screen.getByRole("radio", { name: "Bất động sản" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Phương tiện" })).not.toBeChecked();
    // Submitting without a kind blocks the API call.
    await userEvent.click(screen.getByRole("button", { name: "Thêm biện pháp bảo đảm" }));
    expect(screen.getByText("Chọn loại tài sản bảo đảm.")).toBeVisible();
    expect(api.createInterest).not.toHaveBeenCalled();
  });

  it("creates an interest with the chosen kind and description", async () => {
    const api = fakeApi();
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Thêm biện pháp bảo đảm" });
    await userEvent.click(screen.getByRole("radio", { name: "Tiền gửi" }));
    await userEvent.type(screen.getByLabelText(/Mô tả tài sản/), "Sổ tiết kiệm 500 triệu");
    await userEvent.click(screen.getByRole("button", { name: "Thêm biện pháp bảo đảm" }));
    await waitFor(() =>
      expect(api.createInterest).toHaveBeenCalledWith("case-1", {
        assetKind: "DEPOSIT",
        assetDescription: "Sổ tiết kiệm 500 triệu",
        ownerName: undefined,
        valuationReference: undefined,
        notes: undefined,
      }),
    );
  });

  it("does not preselect a transition target and requires evidence for COMPLETED", async () => {
    const api = fakeApi({
      getLedger: vi.fn(async () => buildLedger([buildInterest("EVIDENCE_ATTACHED")])),
    });
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nhà đất số 10");
    const completed = screen.getByRole("radio", { name: "Ghi nhận hoàn thiện bảo đảm" });
    expect(completed).not.toBeChecked();
    await userEvent.click(completed);
    await userEvent.click(
      screen.getByRole("button", { name: "Ghi nhận chuyển trạng thái yêu cầu" }),
    );
    expect(
      screen.getByText("Trạng thái hoàn thiện phải kèm ít nhất một tham chiếu bằng chứng."),
    ).toBeVisible();
    expect(api.transitionItem).not.toHaveBeenCalled();
  });

  it("keeps the draft and offers a reload on a 409 when creating an interest", async () => {
    const api = fakeApi({
      createInterest: vi.fn().mockRejectedValue(new ApiClientError(409, "CONFLICT", "", false)),
    });
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Thêm biện pháp bảo đảm" });
    await userEvent.click(screen.getByRole("radio", { name: "Khác" }));
    await userEvent.type(screen.getByLabelText(/Mô tả tài sản/), "Tài sản khác");
    await userEvent.click(screen.getByRole("button", { name: "Thêm biện pháp bảo đảm" }));
    expect(await screen.findByRole("button", { name: "Tải lại" })).toBeVisible();
  });

  it("requires a rationale to confirm and blocks the API when empty", async () => {
    const api = fakeApi();
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Xác nhận hoàn thiện bảo đảm" });
    expect(screen.getByText(/Chưa thể xác nhận/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận hoàn thiện bảo đảm" }));
    expect(screen.getByText("Nhập lý do trước khi ghi; đây là trường bắt buộc.")).toBeVisible();
    expect(api.confirm).not.toHaveBeenCalled();
  });

  it("confirms with a rationale when the ledger is complete", async () => {
    const api = fakeApi();
    render(<SecurityWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Xác nhận hoàn thiện bảo đảm" });
    await userEvent.type(
      screen.getByLabelText(/Lý do xác nhận hoàn thiện bảo đảm/),
      "Đã hoàn thiện toàn bộ yêu cầu.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận hoàn thiện bảo đảm" }));
    await waitFor(() =>
      expect(api.confirm).toHaveBeenCalledWith("case-1", "Đã hoàn thiện toàn bộ yêu cầu."),
    );
  });
});
