import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { SettlementWorkspace } from "../../components/settlement/settlement-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  RecoveryCase,
  RecoveryCases,
  SettlementCheck,
  SettlementReceipt,
  SettlementRecoveryApiClient,
  SettlementView,
} from "../../lib/api/settlement-recovery";

type FakeApi = Pick<
  SettlementRecoveryApiClient,
  | "getSettlement"
  | "createSettlementCheck"
  | "confirmSettlement"
  | "getRecovery"
  | "openRecovery"
  | "approveStrategy"
>;

function buildCheck(overrides: Partial<SettlementCheck> = {}): SettlementCheck {
  return {
    id: "chk-1",
    caseId: "case-1",
    caseVersion: 5,
    outstandingPrincipal: "0",
    outstandingInterest: "0",
    outstandingFees: "0",
    openExceptionCount: 0,
    zeroBalanceConfirmed: true,
    createdAt: "2026-07-18T08:00:00Z",
    ...overrides,
  };
}

function buildReceipt(overrides: Partial<SettlementReceipt> = {}): SettlementReceipt {
  return {
    id: "rcp-1",
    settlementCheckId: "chk-1",
    kind: "MOCK_CLOSURE",
    note: "Tất toán khoản vay (chứng từ mô phỏng).",
    createdAt: "2026-07-18T09:00:00Z",
    ...overrides,
  };
}

function buildView(overrides: Partial<SettlementView> = {}): SettlementView {
  return {
    checks: [],
    receipts: [],
    caseVersion: 5,
    confirmable: false,
    ...overrides,
  };
}

function buildRecoveryCase(overrides: Partial<RecoveryCase> = {}): RecoveryCase {
  return {
    id: "rec-1",
    caseId: "case-1",
    caseVersion: 5,
    triggerSummary: "Shortfall kéo dài 4 kỳ.",
    escalatedBy: "actor-esc",
    escalationRationale: "Trình cấp có thẩm quyền xử lý.",
    status: "PREPARING",
    evidenceRefs: ["evidence://ledger/1"],
    options: [
      {
        label: "Cơ cấu lại nợ",
        description: "Giãn kỳ hạn 6 tháng.",
        consequences: "Giảm áp lực dòng tiền.",
        dependencies: null,
      },
    ],
    approvedBy: null,
    createdAt: "2026-07-18T10:00:00Z",
    ...overrides,
  };
}

function buildRecoveryCases(cases: RecoveryCase[] = []): RecoveryCases {
  return { recoveryCases: cases, caseVersion: 5 };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    getSettlement: vi.fn(async () => buildView()),
    createSettlementCheck: vi.fn(async () => buildCheck()),
    confirmSettlement: vi.fn(async () => ({
      gateType: "HG_SETTLEMENT_CONFIRMED",
      status: "SATISFIED",
      caseVersion: 5,
      dispositionRef: "settlement:5",
      receipts: [buildReceipt()],
    })),
    getRecovery: vi.fn(async () => buildRecoveryCases()),
    openRecovery: vi.fn(async () => buildRecoveryCase()),
    approveStrategy: vi.fn(async () => ({
      gateType: "HG_RECOVERY_STRATEGY_APPROVED",
      status: "SATISFIED",
      caseVersion: 5,
      dispositionRef: "recovery-strategy:rec-1",
      recoveryCase: buildRecoveryCase({ status: "STRATEGY_APPROVED", approvedBy: "actor-app" }),
    })),
    ...overrides,
  };
}

describe("SettlementWorkspace — states", () => {
  it("shows the loading skeleton before the reads resolve", () => {
    const api = fakeApi({ getSettlement: vi.fn(() => new Promise<SettlementView>(() => {})) });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    expect(screen.getByLabelText("Đang tải tất toán và xử lý nợ")).toBeVisible();
  });

  it("shows an error and a retry on read failure", async () => {
    const api = fakeApi({ getSettlement: vi.fn().mockRejectedValue(new Error("network")) });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.getSettlement).toHaveBeenCalledTimes(2));
  });

  it("renders the unauthorized panel on a 403 read", async () => {
    const api = fakeApi({
      getSettlement: vi
        .fn()
        .mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Bạn không có vai trò tham gia hồ sơ để xem tất toán và xử lý nợ."),
    ).toBeVisible();
  });

  it("renders both branch headings when loaded", async () => {
    render(<SettlementWorkspace api={fakeApi()} caseId="case-1" />);
    expect(await screen.findByText("14A · Tất toán khoản vay")).toBeVisible();
    expect(screen.getByText("14B · Chuẩn bị xử lý nợ")).toBeVisible();
  });

  it("reflects the server confirmable flag in the readiness chip", async () => {
    const api = fakeApi({ getSettlement: vi.fn(async () => buildView({ confirmable: true })) });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Đủ điều kiện tất toán")).toBeVisible();
  });
});

describe("SettlementWorkspace — settlement (14A)", () => {
  it("renders the derived details when a check is ineligible (409)", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      createSettlementCheck: vi.fn().mockRejectedValue(
        new ApiClientError(409, "SETTLEMENT_NOT_ELIGIBLE", "", false, null, {
          zeroBalance: false,
          outstandingPrincipal: "1500000",
          outstandingInterest: "0",
          outstandingFees: "0",
          openExceptionCount: 1,
        }),
      ),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    await screen.findByText("14A · Tất toán khoản vay");
    await user.type(screen.getByLabelText(/Dư nợ gốc/), "1500000");
    await user.type(screen.getByLabelText(/Dư lãi/), "0");
    await user.type(screen.getByLabelText(/Dư phí/), "0");
    await user.type(screen.getByLabelText(/Số exception còn mở/), "1");
    await user.click(screen.getByRole("button", { name: "Ghi nhận kiểm tra tất toán" }));
    expect(await screen.findByText("Số liệu ledger chưa đủ điều kiện tất toán")).toBeVisible();
    expect(screen.getByText("1500000")).toBeVisible();
  });

  it("requires a rationale before confirming settlement", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ getSettlement: vi.fn(async () => buildView({ confirmable: true })) });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Xác nhận tất toán" });
    await user.click(screen.getByRole("button", { name: "Xác nhận tất toán" }));
    expect(screen.getByText("Nhập lý do trước khi ghi; đây là trường bắt buộc.")).toBeVisible();
    expect(api.confirmSettlement).not.toHaveBeenCalled();
  });

  it("confirms settlement with a rationale", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ getSettlement: vi.fn(async () => buildView({ confirmable: true })) });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Xác nhận tất toán" });
    await user.type(screen.getByLabelText(/Lý do xác nhận tất toán/), "Đã tất toán đầy đủ.");
    await user.click(screen.getByRole("button", { name: "Xác nhận tất toán" }));
    await waitFor(() => expect(api.confirmSettlement).toHaveBeenCalledWith("case-1"));
  });

  it("labels mock receipts and renders the receipt kind label", async () => {
    const api = fakeApi({
      getSettlement: vi.fn(async () =>
        buildView({ confirmable: true, checks: [buildCheck()], receipts: [buildReceipt()] }),
      ),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Tất toán khoản vay (mô phỏng)")).toBeVisible();
    expect(screen.getAllByText("Biên nhận mô phỏng").length).toBeGreaterThan(0);
  });

  it("fails closed on an unknown receipt kind", async () => {
    const api = fakeApi({
      getSettlement: vi.fn(async () =>
        buildView({ receipts: [buildReceipt({ kind: "MOCK_TELEPORT" })] }),
      ),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Trạng thái chưa được hỗ trợ/)).toBeVisible();
  });
});

describe("SettlementWorkspace — recovery (14B)", () => {
  it("makes the escalation rationale mandatory when opening a recovery case", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Mở hồ sơ xử lý nợ" });
    await user.type(screen.getByLabelText(/Tổng dư nợ/), "5000000");
    await user.type(screen.getByLabelText(/Số kỳ shortfall/), "4");
    await user.type(screen.getByLabelText(/Tóm tắt trigger/), "Shortfall kéo dài.");
    await user.click(screen.getByRole("button", { name: "Mở hồ sơ xử lý nợ" }));
    expect(screen.getByText("Lý do trình cấp có thẩm quyền là bắt buộc.")).toBeVisible();
    expect(api.openRecovery).not.toHaveBeenCalled();
  });

  it("renders the not-triggered derived details distinctly (409)", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      openRecovery: vi.fn().mockRejectedValue(
        new ApiClientError(409, "RECOVERY_NOT_TRIGGERED", "", false, null, {
          outstandingTotal: "5000000",
          periodsInShortfall: 1,
          thresholdPeriods: 3,
        }),
      ),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Mở hồ sơ xử lý nợ" });
    await user.type(screen.getByLabelText(/Tổng dư nợ/), "5000000");
    await user.type(screen.getByLabelText(/Số kỳ shortfall/), "1");
    await user.type(screen.getByLabelText(/Tóm tắt trigger/), "Một kỳ trễ.");
    await user.type(screen.getByLabelText(/Ghi nhận trình cấp có thẩm quyền/), "Trình cấp trên.");
    await user.type(screen.getByLabelText(/Tham chiếu bằng chứng/), "evidence://x");
    await user.type(screen.getByLabelText(/Nhãn phương án 1/), "Cơ cấu nợ");
    await user.type(screen.getByLabelText(/Mô tả phương án 1/), "Giãn nợ");
    await user.type(screen.getByLabelText(/Hệ quả phương án 1/), "Giảm áp lực");
    await user.click(screen.getByRole("button", { name: "Mở hồ sơ xử lý nợ" }));
    expect(await screen.findByText("Chưa đủ điều kiện mở hồ sơ xử lý nợ")).toBeVisible();
    expect(screen.getByText("Ngưỡng số kỳ")).toBeVisible();
  });

  it("renders the recovery status label and fails closed on an unknown status", async () => {
    const api = fakeApi({
      getRecovery: vi.fn(async () =>
        buildRecoveryCases([buildRecoveryCase({ status: "TELEPORTED" })]),
      ),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Trạng thái chưa được hỗ trợ/)).toBeVisible();
  });

  it("shows the approval form only for a PREPARING case", async () => {
    const api = fakeApi({
      getRecovery: vi.fn(async () =>
        buildRecoveryCases([buildRecoveryCase({ status: "STRATEGY_APPROVED", approvedBy: "actor-app" })]),
      ),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Đã duyệt phương án")).toBeVisible();
    expect(screen.getByText("Phương án đã được phê duyệt.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Duyệt phương án xử lý nợ" }),
    ).not.toBeInTheDocument();
  });

  it("surfaces SAME_ACTOR_FORBIDDEN distinctly on strategy approval", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      getRecovery: vi.fn(async () => buildRecoveryCases([buildRecoveryCase()])),
      approveStrategy: vi
        .fn()
        .mockRejectedValue(new ApiClientError(409, "SAME_ACTOR_FORBIDDEN", "", false)),
    });
    render(<SettlementWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Shortfall kéo dài 4 kỳ.");
    await user.click(screen.getByRole("button", { name: "Duyệt phương án xử lý nợ" }));
    expect(
      await screen.findByText(
        "Không thể duyệt: người phê duyệt chiến lược phải khác với người đã escalate hồ sơ.",
      ),
    ).toBeVisible();
  });
});
