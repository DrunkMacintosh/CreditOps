import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { RepaymentWorkspace } from "../../components/repayments/repayment-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  CollectionsException,
  Facility,
  LedgerPeriod,
  LedgerSnapshot,
  RepaymentEvent,
  RepaymentsApiClient,
} from "../../lib/api/repayments";

type FakeApi = Pick<
  RepaymentsApiClient,
  "createFacility" | "recordEvent" | "getLedger" | "createNote"
>;

function buildFacility(overrides: Partial<Facility> = {}): Facility {
  return {
    id: "fac-1",
    caseId: "case-1",
    caseVersion: 4,
    decisionId: "decision-1",
    principal: "1000000000",
    annualRatePercent: "12.5",
    termMonths: 12,
    periodicFee: "50000",
    repaymentStyle: "EQUAL_PRINCIPAL",
    firstPaymentDate: "2026-08-01",
    ...overrides,
  };
}

function buildPeriod(overrides: Partial<LedgerPeriod> = {}): LedgerPeriod {
  return {
    period: 1,
    dueDate: "2026-08-01",
    expectedFee: "50000",
    expectedInterest: "104166.67",
    expectedPrincipal: "83333333.33",
    allocatedFee: "50000",
    allocatedInterest: "104166.67",
    allocatedPrincipal: "83333333.33",
    outstandingTotal: "0",
    status: "PAID",
    overdue: false,
    ...overrides,
  };
}

function buildException(overrides: Partial<CollectionsException> = {}): CollectionsException {
  return {
    kind: "OVERDUE_INSTALLMENT",
    period: 2,
    amount: "83487500",
    detailVi: "Kỳ 2 quá hạn chưa thu.",
    ...overrides,
  };
}

function buildLedger(overrides: Partial<LedgerSnapshot> = {}): LedgerSnapshot {
  return {
    facilityId: "fac-1",
    asOf: "2026-09-01",
    allocationPolicyVersion: "collections-allocation-v1",
    netPaid: "83487500",
    outstandingFees: "0",
    outstandingInterest: "0",
    outstandingPrincipal: "0",
    outstandingTotal: "0",
    overpayment: "0",
    isSettled: false,
    periods: [buildPeriod()],
    exceptions: [],
    notes: [],
    ...overrides,
  };
}

function buildEvent(overrides: Partial<RepaymentEvent> = {}): RepaymentEvent {
  return {
    id: "evt-1",
    facilityId: "fac-1",
    kind: "PAYMENT",
    amount: "83487500",
    externalReference: "TT-2026-0001",
    reversedEventId: null,
    effectiveDate: "2026-08-01",
    created: true,
    ...overrides,
  };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    createFacility: vi.fn(async () => buildFacility()),
    recordEvent: vi.fn(async () => buildEvent()),
    getLedger: vi.fn(async () => buildLedger()),
    createNote: vi.fn(async () => ({
      id: "note-1",
      noteKind: "OBSERVATION",
      noteText: "Đã liên hệ khách hàng.",
      proposedAction: null,
      authorRole: "OPS_OFFICER",
    })),
    ...overrides,
  };
}

async function openFacility(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Gốc vay/), "1000000000");
  await user.type(screen.getByLabelText(/Lãi suất năm/), "12.5");
  await user.type(screen.getByLabelText(/Kỳ hạn/), "12");
  await user.selectOptions(screen.getByLabelText(/Kiểu trả nợ/), "EQUAL_PRINCIPAL");
  fireEvent.change(screen.getByLabelText(/Kỳ trả đầu tiên/), {
    target: { value: "2026-08-01" },
  });
  await user.click(screen.getByRole("button", { name: "Mở khoản vay giải ngân" }));
}

describe("RepaymentWorkspace — entry + states", () => {
  it("shows the open-facility entry when no facility is open", () => {
    render(<RepaymentWorkspace api={fakeApi()} caseId="case-1" />);
    expect(screen.getByText("Chưa mở khoản vay để theo dõi thu nợ")).toBeVisible();
    expect(screen.getByRole("button", { name: "Mở khoản vay giải ngân" })).toBeVisible();
  });

  it("shows the ledger loading skeleton while the recompute is in flight", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ getLedger: vi.fn(() => new Promise<LedgerSnapshot>(() => {})) });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByLabelText("Đang tải sổ thu nợ")).toBeVisible();
  });

  it("shows an error and a retry when the ledger recompute fails", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ getLedger: vi.fn().mockRejectedValue(new Error("network")) });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.getLedger).toHaveBeenCalledTimes(2));
  });

  it("renders the unauthorized panel on a 403 ledger read", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      getLedger: vi.fn().mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(
      await screen.findByText("Bạn không có vai trò tham gia hồ sơ để xem sổ thu nợ."),
    ).toBeVisible();
  });

  it("shows the empty-schedule state when the ledger has no periods", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ getLedger: vi.fn(async () => buildLedger({ periods: [] })) });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByText("Chưa có kỳ trả nợ nào")).toBeVisible();
  });

  it("surfaces the approval-required 409 when opening a facility", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      createFacility: vi.fn().mockRejectedValue(
        new ApiClientError(
          409,
          "FACILITY_REQUIRES_APPROVAL_DECISION",
          "Chưa có quyết định phê duyệt tín dụng.",
          false,
        ),
      ),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByText("Chưa có quyết định phê duyệt tín dụng.")).toBeVisible();
  });
});

describe("RepaymentWorkspace — labels + fail closed", () => {
  it("renders the facility summary with the repayment-style label", async () => {
    const user = userEvent.setup();
    render(<RepaymentWorkspace api={fakeApi()} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByText("Trả gốc đều")).toBeVisible();
    expect(screen.getByText("Còn dư nợ")).toBeVisible();
  });

  it("renders the exact period status label from the ledger", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      getLedger: vi.fn(async () => buildLedger({ periods: [buildPeriod({ status: "PARTIALLY_PAID" })] })),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByText("Trả một phần")).toBeVisible();
  });

  it("fails closed on an unknown period status", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      getLedger: vi.fn(async () => buildLedger({ periods: [buildPeriod({ status: "FROZEN" })] })),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByText(/Loại chưa được hỗ trợ/)).toBeVisible();
  });

  it("groups exceptions by kind with Vietnamese labels", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      getLedger: vi.fn(async () =>
        buildLedger({
          exceptions: [
            buildException({ kind: "OVERDUE_INSTALLMENT", detailVi: "Kỳ 2 quá hạn." }),
            buildException({ kind: "UNMATCHED_PAYMENT", period: null, detailVi: "Thu vượt lịch." }),
          ],
        }),
      ),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByText("Kỳ trả nợ quá hạn")).toBeVisible();
    expect(screen.getByText("Khoản thu chưa khớp")).toBeVisible();
    expect(screen.getByText("Kỳ 2 quá hạn.")).toBeVisible();
  });

  it("fails closed on an unknown exception kind", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      getLedger: vi.fn(async () =>
        buildLedger({ exceptions: [buildException({ kind: "MYSTERY_KIND", detailVi: "Ngoại lệ lạ." })] }),
      ),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    expect(await screen.findByText(/Loại chưa được hỗ trợ/)).toBeVisible();
    expect(screen.getByText("Ngoại lệ lạ.")).toBeVisible();
  });
});

describe("RepaymentWorkspace — record event", () => {
  it("does not preselect an event kind and blocks a submit without one", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("heading", { name: "Sự kiện thu nợ" });
    expect(screen.getByRole("radio", { name: "Thanh toán" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Bút toán đảo" })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Ghi nhận sự kiện thu nợ" }));
    expect(
      screen.getByText("Chọn loại sự kiện: thanh toán hoặc bút toán đảo."),
    ).toBeVisible();
    expect(api.recordEvent).not.toHaveBeenCalled();
  });

  it("requires an external reference before recording an event", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("heading", { name: "Sự kiện thu nợ" });
    await user.click(screen.getByRole("radio", { name: "Thanh toán" }));
    await user.type(screen.getByLabelText(/Số tiền/), "100");
    fireEvent.change(screen.getByLabelText(/Ngày hiệu lực/), { target: { value: "2026-08-01" } });
    await user.click(screen.getByRole("button", { name: "Ghi nhận sự kiện thu nợ" }));
    expect(
      screen.getByText("Tham chiếu ngoài là bắt buộc để bảo đảm tính idempotent."),
    ).toBeVisible();
    expect(api.recordEvent).not.toHaveBeenCalled();
  });

  it("reports a genuinely new event distinctly from a duplicate", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      recordEvent: vi.fn(async () => buildEvent({ created: true, externalReference: "TT-9" })),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("heading", { name: "Sự kiện thu nợ" });
    await user.click(screen.getByRole("radio", { name: "Thanh toán" }));
    await user.type(screen.getByLabelText(/Số tiền/), "83487500");
    fireEvent.change(screen.getByLabelText(/Ngày hiệu lực/), { target: { value: "2026-08-01" } });
    await user.type(screen.getByLabelText(/Tham chiếu ngoài/), "TT-9");
    await user.click(screen.getByRole("button", { name: "Ghi nhận sự kiện thu nợ" }));
    expect(await screen.findByText(/Đã ghi sự kiện mới/)).toBeVisible();
  });

  it("renders the 200 idempotent duplicate as 'Sự kiện đã tồn tại'", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      recordEvent: vi.fn(async () => buildEvent({ created: false, externalReference: "TT-DUP" })),
    });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("heading", { name: "Sự kiện thu nợ" });
    await user.click(screen.getByRole("radio", { name: "Thanh toán" }));
    await user.type(screen.getByLabelText(/Số tiền/), "83487500");
    fireEvent.change(screen.getByLabelText(/Ngày hiệu lực/), { target: { value: "2026-08-01" } });
    await user.type(screen.getByLabelText(/Tham chiếu ngoài/), "TT-DUP");
    await user.click(screen.getByRole("button", { name: "Ghi nhận sự kiện thu nợ" }));
    expect(await screen.findByText(/Sự kiện đã tồn tại/)).toBeVisible();
  });

  it("lists a recorded payment and renders reversal linkage", async () => {
    const user = userEvent.setup();
    const payment = buildEvent({ id: "pay-1", externalReference: "TT-1", created: true });
    const reversal = buildEvent({
      id: "rev-1",
      kind: "REVERSAL",
      externalReference: "REV-1",
      reversedEventId: "pay-1",
      created: true,
    });
    const recordEvent = vi
      .fn()
      .mockResolvedValueOnce(payment)
      .mockResolvedValueOnce(reversal);
    const api = fakeApi({ recordEvent });
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("heading", { name: "Sự kiện thu nợ" });

    // Record the payment first.
    await user.click(screen.getByRole("radio", { name: "Thanh toán" }));
    await user.type(screen.getByLabelText(/Số tiền/), "83487500");
    fireEvent.change(screen.getByLabelText(/Ngày hiệu lực/), { target: { value: "2026-08-01" } });
    await user.type(screen.getByLabelText(/Tham chiếu ngoài/), "TT-1");
    await user.click(screen.getByRole("button", { name: "Ghi nhận sự kiện thu nợ" }));
    expect(await screen.findByText("Tham chiếu: TT-1")).toBeVisible();

    // Then a reversal referencing it.
    await user.click(screen.getByRole("radio", { name: "Bút toán đảo" }));
    await user.type(screen.getByLabelText(/Số tiền/), "83487500");
    fireEvent.change(screen.getByLabelText(/Ngày hiệu lực/), { target: { value: "2026-08-02" } });
    await user.type(screen.getByLabelText(/Tham chiếu ngoài/), "REV-1");
    await user.selectOptions(screen.getByLabelText(/Khoản thanh toán bị đảo/), "pay-1");
    await user.click(screen.getByRole("button", { name: "Ghi nhận sự kiện thu nợ" }));
    expect(await screen.findByText(/Đảo khoản thanh toán/)).toBeVisible();
  });
});

describe("RepaymentWorkspace — collection note", () => {
  it("requires a proposed action for a PROPOSED_ACTION note", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("heading", {
      name: "Đề xuất hành động thu nợ — chờ cấp có thẩm quyền",
    });
    await user.click(screen.getByRole("radio", { name: "Đề xuất hành động" }));
    await user.type(screen.getByLabelText(/Nội dung ghi chú/), "Đề nghị nhắc nợ.");
    await user.click(screen.getByRole("button", { name: "Lưu đề xuất hành động thu nợ" }));
    expect(
      screen.getByText("Đề xuất hành động phải nêu rõ hành động được đề xuất."),
    ).toBeVisible();
    expect(api.createNote).not.toHaveBeenCalled();
  });

  it("submits an observation note and reloads the ledger", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<RepaymentWorkspace api={api} caseId="case-1" />);
    await openFacility(user);
    await screen.findByRole("heading", {
      name: "Đề xuất hành động thu nợ — chờ cấp có thẩm quyền",
    });
    await user.click(screen.getByRole("radio", { name: "Quan sát" }));
    await user.type(screen.getByLabelText(/Nội dung ghi chú/), "Đã liên hệ khách hàng.");
    await user.click(screen.getByRole("button", { name: "Lưu đề xuất hành động thu nợ" }));
    await waitFor(() =>
      expect(api.createNote).toHaveBeenCalledWith("case-1", "fac-1", {
        noteKind: "OBSERVATION",
        noteText: "Đã liên hệ khách hàng.",
      }),
    );
  });
});
