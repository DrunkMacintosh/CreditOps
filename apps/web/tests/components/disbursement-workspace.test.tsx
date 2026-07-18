import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { DisbursementWorkspace } from "../../components/disbursements/disbursement-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  DisbursementAction,
  DisbursementActionDetail,
  DisbursementList,
  DisbursementsApiClient,
} from "../../lib/api/disbursements";

type FakeApi = Pick<
  DisbursementsApiClient,
  "list" | "create" | "validate" | "authorize" | "execute" | "reconcile"
>;

function buildAction(
  status: string = "PROPOSED",
  overrides: Partial<DisbursementAction> = {},
): DisbursementAction {
  return {
    id: "action-1",
    caseId: "case-1",
    caseVersion: 4,
    decisionId: "decision-1",
    amount: "5000000000.00",
    currency: "VND",
    beneficiaryRef: "Cty TNHH ABC",
    accountRef: "STK 0011",
    status,
    createdBy: "maker-1",
    createdAt: "2026-07-18T08:00:00Z",
    ...overrides,
  };
}

function buildDetail(
  status: string = "PROPOSED",
  opts: {
    validated?: boolean;
    authorized?: boolean;
    receipts?: DisbursementActionDetail["receipts"];
  } = {},
): DisbursementActionDetail {
  return {
    action: buildAction(status),
    receipts: opts.receipts ?? [],
    validatedGateStatus: opts.validated ? "SATISFIED" : "OPEN",
    authorizedGateStatus: opts.authorized ? "SATISFIED" : "OPEN",
  };
}

function buildList(actions: DisbursementActionDetail[] = [buildDetail()]): DisbursementList {
  return { actions, caseVersion: 4 };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    list: vi.fn(async () => buildList()),
    create: vi.fn(async () => buildAction()),
    validate: vi.fn(async () => ({})),
    authorize: vi.fn(async () => ({})),
    execute: vi.fn(async () => ({})),
    reconcile: vi.fn(async () => buildAction("CONFIRMED_NOT_EXECUTED")),
    ...overrides,
  };
}

describe("DisbursementWorkspace — states", () => {
  it("shows the loading skeleton before the request resolves", () => {
    const api = fakeApi({ list: vi.fn(() => new Promise<DisbursementList>(() => {})) });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(screen.getByLabelText("Đang tải hành động giải ngân")).toBeVisible();
  });

  it("shows an error and a retry on API failure", async () => {
    const api = fakeApi({ list: vi.fn().mockRejectedValue(new Error("network")) });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
  });

  it("renders NO workspace controls on a 403 load", async () => {
    const api = fakeApi({
      list: vi.fn().mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Bạn không có vai trò tham gia hồ sơ để xem hành động giải ngân."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Tạo hành động giải ngân" }),
    ).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no actions", async () => {
    const api = fakeApi({ list: vi.fn(async () => buildList([])) });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Chưa có hành động giải ngân nào")).toBeVisible();
  });

  it("renders the exact amount + currency and the Vietnamese status label", async () => {
    const api = fakeApi();
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("5000000000.00 VND")).toBeVisible();
    expect(screen.getByText("Đã đề xuất")).toBeVisible();
  });

  it("renders the unsupported label for an unknown status (fail closed)", async () => {
    const api = fakeApi({ list: vi.fn(async () => buildList([buildDetail("NEW_STATE")])) });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Trạng thái chưa được hỗ trợ/)).toBeVisible();
  });

  it("reflects both gate statuses in the gate chips", async () => {
    const api = fakeApi({
      list: vi.fn(async () => buildList([buildDetail("PROPOSED", { validated: true })])),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Kiểm tra: Đạt")).toBeVisible();
    expect(screen.getByText("Uỷ quyền: Đang chờ")).toBeVisible();
  });

  it("labels every execution receipt as a simulated (mock) execution", async () => {
    const receipts = [
      {
        id: "receipt-1",
        actionId: "action-1",
        idempotencyKey: "key-abcdef123456",
        adapterLabel: "MOCK_DISBURSEMENT_EXECUTION_ADAPTER",
        resultStatus: "CONFIRMED_EXECUTED",
        receiptRef: "MOCK-RCPT-1",
        recordedBy: "checker-1",
        createdAt: "2026-07-18T09:00:00Z",
      },
    ];
    const api = fakeApi({
      list: vi.fn(async () =>
        buildList([
          buildDetail("CONFIRMED_EXECUTED", { validated: true, authorized: true, receipts }),
        ]),
      ),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Thực thi mô phỏng")).toBeVisible();
    expect(screen.getByText(/MOCK-RCPT-1/)).toBeVisible();
  });
});

describe("DisbursementWorkspace — gate + execute flows", () => {
  it("records gate 1 (validate) on the labelled section button", async () => {
    const api = fakeApi();
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Xác nhận kiểm tra giải ngân" });
    await userEvent.click(
      screen.getByRole("button", { name: "Xác nhận đã kiểm tra giải ngân" }),
    );
    await waitFor(() => expect(api.validate).toHaveBeenCalledWith("case-1", "action-1"));
  });

  it("surfaces a DISTINCT message for a 409 VALIDATION_REQUIRED on authorize", async () => {
    const api = fakeApi({
      authorize: vi
        .fn()
        .mockRejectedValue(new ApiClientError(409, "VALIDATION_REQUIRED", "", false)),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Uỷ quyền hành động đề xuất" });
    await userEvent.click(
      screen.getByRole("button", { name: "Uỷ quyền hành động giải ngân" }),
    );
    expect(
      await screen.findByText(
        "Chưa thể uỷ quyền: cổng kiểm tra giải ngân (HG_DISBURSEMENT_VALIDATED) chưa được thỏa mãn trước.",
      ),
    ).toBeVisible();
  });

  it("surfaces DISTINCT messages for SAME_ACTOR_FORBIDDEN vs ALREADY_EXECUTED on execute", async () => {
    const sameActor = fakeApi({
      execute: vi
        .fn()
        .mockRejectedValue(new ApiClientError(409, "SAME_ACTOR_FORBIDDEN", "", false)),
    });
    const { unmount } = render(<DisbursementWorkspace api={sameActor} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Thực thi giải ngân (mô phỏng)" });
    await userEvent.click(
      screen.getByRole("button", { name: "Thực thi giải ngân (mô phỏng)" }),
    );
    expect(
      await screen.findByText(
        "Tách biệt nhiệm vụ: người thực hiện bước này phải khác với người đã thực hiện bước trước.",
      ),
    ).toBeVisible();
    unmount();

    const already = fakeApi({
      execute: vi
        .fn()
        .mockRejectedValue(new ApiClientError(409, "ALREADY_EXECUTED", "", false)),
    });
    render(<DisbursementWorkspace api={already} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Thực thi giải ngân (mô phỏng)" });
    await userEvent.click(
      screen.getByRole("button", { name: "Thực thi giải ngân (mô phỏng)" }),
    );
    expect(
      await screen.findByText("Hành động giải ngân đã được xác nhận thực thi."),
    ).toBeVisible();
  });
});

describe("DisbursementWorkspace — EXECUTION_UNKNOWN blocking + reconciliation", () => {
  it("renders the distinct blocking state and hides the gate/execute sections", async () => {
    const api = fakeApi({
      list: vi.fn(async () => buildList([buildDetail("EXECUTION_UNKNOWN", {
        validated: true,
        authorized: true,
      })])),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Kết quả thực thi chưa xác định — cần đối soát thủ công"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Thực thi giải ngân (mô phỏng)" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Xác nhận đã kiểm tra giải ngân" }),
    ).not.toBeInTheDocument();
  });

  it("does not preselect a reconciliation outcome and requires one before recording", async () => {
    const api = fakeApi({
      list: vi.fn(async () => buildList([buildDetail("EXECUTION_UNKNOWN")])),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Ghi nhận kết quả đối soát" });
    expect(screen.getByRole("radio", { name: "Đã thực thi (tiền đã chuyển)" })).not.toBeChecked();
    expect(
      screen.getByRole("radio", { name: "Không thực thi (tiền chưa chuyển)" }),
    ).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận kết quả đối soát" }));
    expect(screen.getByText("Chọn kết quả đối soát trước khi ghi.")).toBeVisible();
    expect(api.reconcile).not.toHaveBeenCalled();
  });

  it("requires a rationale after an outcome is chosen", async () => {
    const api = fakeApi({
      list: vi.fn(async () => buildList([buildDetail("EXECUTION_UNKNOWN")])),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Ghi nhận kết quả đối soát" });
    await userEvent.click(screen.getByRole("radio", { name: "Không thực thi (tiền chưa chuyển)" }));
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận kết quả đối soát" }));
    expect(
      screen.getByText("Đối soát là quyết định có thẩm quyền: bắt buộc nhập lý do."),
    ).toBeVisible();
    expect(api.reconcile).not.toHaveBeenCalled();
  });

  it("records the reconciliation with the chosen outcome + rationale and refetches", async () => {
    const api = fakeApi({
      list: vi.fn(async () => buildList([buildDetail("EXECUTION_UNKNOWN")])),
    });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Ghi nhận kết quả đối soát" });
    await userEvent.click(screen.getByRole("radio", { name: "Đã thực thi (tiền đã chuyển)" }));
    await userEvent.type(
      screen.getByLabelText(/Lý do/),
      "Đối soát với sao kê: tiền đã chuyển đủ.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận kết quả đối soát" }));
    await waitFor(() =>
      expect(api.reconcile).toHaveBeenCalledWith("case-1", "action-1", {
        outcome: "CONFIRMED_EXECUTED",
        rationale: "Đối soát với sao kê: tiền đã chuyển đủ.",
      }),
    );
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
  });
});

describe("DisbursementWorkspace — create", () => {
  it("requires a beneficiary and account reference before creating", async () => {
    const api = fakeApi({ list: vi.fn(async () => buildList([])) });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Tạo hành động giải ngân đề xuất" });
    await userEvent.click(screen.getByRole("button", { name: "Tạo hành động giải ngân" }));
    expect(
      screen.getByText("Nhập tham chiếu thụ hưởng và tài khoản (dữ liệu tổng hợp)."),
    ).toBeVisible();
    expect(api.create).not.toHaveBeenCalled();
  });

  it("creates a proposed action with the optional amount omitted when blank", async () => {
    const api = fakeApi({ list: vi.fn(async () => buildList([])) });
    render(<DisbursementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Tạo hành động giải ngân đề xuất" });
    await userEvent.type(screen.getByLabelText(/Tham chiếu thụ hưởng/), "Cty ABC");
    await userEvent.type(screen.getByLabelText(/Tham chiếu tài khoản/), "STK 0011");
    await userEvent.click(screen.getByRole("button", { name: "Tạo hành động giải ngân" }));
    await waitFor(() =>
      expect(api.create).toHaveBeenCalledWith("case-1", {
        beneficiaryRef: "Cty ABC",
        accountRef: "STK 0011",
      }),
    );
  });
});
