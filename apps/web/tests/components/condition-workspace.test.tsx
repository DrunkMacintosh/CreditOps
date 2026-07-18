import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { ConditionWorkspace } from "../../components/conditions/condition-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  ConditionLedger,
  ConditionsApiClient,
  ConditionStatus,
  DisbursementCondition,
} from "../../lib/api/conditions";

type FakeApi = Pick<
  ConditionsApiClient,
  "getLedger" | "createCondition" | "transition" | "confirm"
>;

function buildCondition(
  status: ConditionStatus | string = "PENDING",
  overrides: Partial<DisbursementCondition> = {},
): DisbursementCondition {
  return {
    id: "cond-1",
    caseId: "case-1",
    caseVersion: 3,
    decisionId: "decision-1",
    conditionText: "Bổ sung vốn tự có tham gia 30%",
    owner: "Phòng khách hàng",
    dueDate: null,
    status,
    evidenceRefs: [],
    createdAt: "2026-07-18T08:00:00Z",
    ...overrides,
  };
}

function buildLedger(
  conditions: DisbursementCondition[] = [buildCondition()],
  confirmable = false,
): ConditionLedger {
  return { conditions, caseVersion: 3, confirmable };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    getLedger: vi.fn(async () => buildLedger()),
    createCondition: vi.fn(async () => buildCondition()),
    transition: vi.fn(async () => buildCondition()),
    confirm: vi.fn(async () => ({
      gateType: "HG_DISBURSEMENT_CONDITIONS_CONFIRMED",
      status: "SATISFIED",
      caseVersion: 3,
      dispositionRef: "disbursement-conditions:3",
    })),
    ...overrides,
  };
}

describe("ConditionWorkspace — states", () => {
  it("shows the loading skeleton before the request resolves", () => {
    const api = fakeApi({ getLedger: vi.fn(() => new Promise<ConditionLedger>(() => {})) });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    expect(screen.getByLabelText("Đang tải điều kiện giải ngân")).toBeVisible();
  });

  it("shows an error and a retry on API failure", async () => {
    const api = fakeApi({ getLedger: vi.fn().mockRejectedValue(new Error("network")) });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.getLedger).toHaveBeenCalledTimes(2));
  });

  it("renders NO ledger controls on a 403 load", async () => {
    const api = fakeApi({
      getLedger: vi.fn().mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Bạn không có vai trò tham gia hồ sơ để xem điều kiện giải ngân."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Xác nhận điều kiện giải ngân" }),
    ).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no conditions", async () => {
    const api = fakeApi({ getLedger: vi.fn(async () => buildLedger([])) });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Chưa có điều kiện giải ngân nào")).toBeVisible();
  });

  it("renders a condition with its Vietnamese status label", async () => {
    const api = fakeApi({ getLedger: vi.fn(async () => buildLedger([buildCondition("VERIFIED")])) });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Bổ sung vốn tự có tham gia 30%")).toBeVisible();
    expect(screen.getByText("Đã xác minh")).toBeVisible();
  });

  it("renders the unsupported label for an unknown status (fail closed)", async () => {
    const api = fakeApi({ getLedger: vi.fn(async () => buildLedger([buildCondition("NEW_STATE")])) });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Trạng thái chưa được hỗ trợ/)).toBeVisible();
  });

  it("reflects the server confirmable flag in the readiness chip", async () => {
    const api = fakeApi({
      getLedger: vi.fn(async () => buildLedger([buildCondition("VERIFIED")], true)),
    });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Sẵn sàng xác nhận")).toBeVisible();
  });
});

describe("ConditionWorkspace — write flows", () => {
  it("requires a condition text before opening a condition", async () => {
    const api = fakeApi();
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Mở điều kiện giải ngân" });
    await userEvent.click(screen.getByRole("button", { name: "Mở điều kiện giải ngân" }));
    expect(screen.getByText("Nhập nội dung điều kiện giải ngân.")).toBeVisible();
    expect(api.createCondition).not.toHaveBeenCalled();
  });

  it("does not preselect a transition target and requires a rationale for a not-applicable ruling", async () => {
    const api = fakeApi();
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Bổ sung vốn tự có tham gia 30%");
    const notApplicable = screen.getByRole("radio", {
      name: "Xác định không áp dụng (ghi thẩm quyền)",
    });
    expect(notApplicable).not.toBeChecked();
    await userEvent.click(notApplicable);
    await userEvent.click(
      screen.getByRole("button", { name: "Ghi nhận chuyển trạng thái điều kiện" }),
    );
    expect(
      screen.getByText(
        "Miễn trừ / không áp dụng là quyết định có thẩm quyền: bắt buộc nhập lý do.",
      ),
    ).toBeVisible();
    expect(api.transition).not.toHaveBeenCalled();
  });

  it("transitions with the chosen target and rationale", async () => {
    const api = fakeApi();
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Bổ sung vốn tự có tham gia 30%");
    await userEvent.click(
      screen.getByRole("radio", { name: "Xác định không áp dụng (ghi thẩm quyền)" }),
    );
    await userEvent.type(screen.getByLabelText(/Lý do/), "Điều kiện không áp dụng cho hồ sơ này.");
    await userEvent.click(
      screen.getByRole("button", { name: "Ghi nhận chuyển trạng thái điều kiện" }),
    );
    await waitFor(() =>
      expect(api.transition).toHaveBeenCalledWith("case-1", "cond-1", {
        toStatus: "NOT_APPLICABLE_BY_HUMAN",
        rationale: "Điều kiện không áp dụng cho hồ sơ này.",
        evidenceRefs: [],
      }),
    );
  });

  it("confirms with no rationale body (empty-body gate) and refetches", async () => {
    const api = fakeApi({
      getLedger: vi.fn(async () => buildLedger([buildCondition("VERIFIED")], true)),
    });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Xác nhận điều kiện giải ngân" });
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận điều kiện giải ngân" }));
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith("case-1"));
    await waitFor(() => expect(api.getLedger).toHaveBeenCalledTimes(2));
  });

  it("keeps the draft and offers a reload on a 409 confirm (separation of duty)", async () => {
    const api = fakeApi({
      getLedger: vi.fn(async () => buildLedger([buildCondition("VERIFIED")], true)),
      confirm: vi.fn().mockRejectedValue(new ApiClientError(409, "SAME_ACTOR_FORBIDDEN", "", false)),
    });
    render(<ConditionWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Xác nhận điều kiện giải ngân" });
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận điều kiện giải ngân" }));
    expect(await screen.findByRole("button", { name: "Tải lại" })).toBeVisible();
  });
});
