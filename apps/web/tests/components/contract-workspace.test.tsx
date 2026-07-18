import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { ContractWorkspace } from "../../components/contracts/contract-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  ContractPackagesApiClient,
  ContractPackageView,
} from "../../lib/api/contract-packages";

type FakeApi = Pick<
  ContractPackagesApiClient,
  "getView" | "createPackage" | "addRedline" | "approve" | "confirmSignatureAuthority" | "sign"
>;

function buildView(overrides: Partial<ContractPackageView> = {}): ContractPackageView {
  return {
    package: {
      id: "pkg-1",
      caseId: "case-1",
      caseVersion: 3,
      decisionId: "decision-1",
      termSnapshotHash: "b".repeat(64),
      content: "Hợp đồng tín dụng mô phỏng số 1.",
      contentHash: "a".repeat(64),
      packageVersion: 1,
      state: "DRAFT",
      createdBy: "maker-1",
      createdAt: "2026-07-18T08:00:00Z",
    },
    redlines: [],
    signatureEvidence: null,
    ...overrides,
  };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    getView: vi.fn(async () => buildView()),
    createPackage: vi.fn(async () => buildView().package),
    addRedline: vi.fn(async () => ({ redline: {} as never, package: {} as never })),
    approve: vi.fn(async () => ({
      gateType: "HG_CONTRACT_PACKAGE_APPROVED",
      status: "SATISFIED",
      packageId: "pkg-1",
      dispositionRef: "contract-package:pkg-1",
    })),
    confirmSignatureAuthority: vi.fn(async () => ({
      gateType: "HG_SIGNATURE_AUTHORITY_CONFIRMED",
      status: "SATISFIED",
      packageId: "pkg-1",
      dispositionRef: "signature-authority:pkg-1",
    })),
    sign: vi.fn(async () => ({
      gateType: "HG_CONTRACTS_SIGNED",
      status: "SATISFIED",
      package: buildView().package,
      signatureEvidence: {
        id: "sig-1",
        packageId: "pkg-1",
        kind: "MOCK_SIGNATURE",
        signerNames: ["Nguyễn Văn A"],
        evidenceNote: null,
        recordedBy: "checker-1",
        createdAt: "2026-07-18T09:00:00Z",
      },
      dispositionRef: "contracts-signed:sig-1",
    })),
    ...overrides,
  };
}

describe("ContractWorkspace — states", () => {
  it("shows the loading skeleton before the request resolves", () => {
    const api = fakeApi({ getView: vi.fn(() => new Promise<ContractPackageView>(() => {})) });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    expect(screen.getByLabelText("Đang tải hồ sơ hợp đồng")).toBeVisible();
  });

  it("shows a Vietnamese error and a retry on API failure", async () => {
    const api = fakeApi({ getView: vi.fn().mockRejectedValue(new Error("network")) });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.getView).toHaveBeenCalledTimes(2));
  });

  it("renders NO gate controls on a 403 load", async () => {
    const api = fakeApi({
      getView: vi.fn().mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Bạn không có vai trò tham gia hồ sơ để xem hồ sơ hợp đồng."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Duyệt nội dung gói hợp đồng" }),
    ).not.toBeInTheDocument();
  });

  it("always shows the mock-contract disclaimer", async () => {
    const api = fakeApi();
    render(<ContractWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Hợp đồng mô phỏng — không có hiệu lực pháp lý."),
    ).toBeVisible();
  });

  it("shows the empty state and a create action when no package exists (404)", async () => {
    const api = fakeApi({
      getView: vi.fn().mockRejectedValue(new ApiClientError(404, "NO_CONTRACT_PACKAGE", "", false)),
    });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText("Chưa có hồ sơ hợp đồng")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Lập hồ sơ hợp đồng từ điều khoản đã duyệt" }),
    );
    await waitFor(() => expect(api.createPackage).toHaveBeenCalledWith("case-1"));
  });

  it("renders the unsupported label for an unknown package state (fail closed)", async () => {
    const api = fakeApi({
      getView: vi.fn(async () => buildView({ package: { ...buildView().package, state: "ODD" } })),
    });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Trạng thái chưa được hỗ trợ/)).toBeVisible();
  });
});

describe("ContractWorkspace — gate actions", () => {
  it("requires a rationale on approve; an empty submit does not call the API", async () => {
    const api = fakeApi();
    render(<ContractWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nội dung gói hợp đồng");
    await userEvent.click(screen.getByRole("button", { name: "Duyệt nội dung gói hợp đồng" }));
    expect(screen.getByText("Nhập lý do trước khi ghi; đây là trường bắt buộc.")).toBeVisible();
    expect(api.approve).not.toHaveBeenCalled();
  });

  it("approves with a rationale and refetches", async () => {
    const api = fakeApi();
    render(<ContractWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nội dung gói hợp đồng");
    await userEvent.type(screen.getByLabelText(/Lý do duyệt gói hợp đồng/), "Đủ căn cứ duyệt gói.");
    await userEvent.click(screen.getByRole("button", { name: "Duyệt nội dung gói hợp đồng" }));
    await waitFor(() =>
      expect(api.approve).toHaveBeenCalledWith("case-1", { rationale: "Đủ căn cứ duyệt gói." }),
    );
    await waitFor(() => expect(api.getView).toHaveBeenCalledTimes(2));
  });

  it("shows the distinct material-change fenced state when approve returns 409 MATERIAL_CHANGE_DETECTED", async () => {
    const api = fakeApi({
      approve: vi
        .fn()
        .mockRejectedValue(new ApiClientError(409, "MATERIAL_CHANGE_DETECTED", "", false)),
    });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nội dung gói hợp đồng");
    await userEvent.type(screen.getByLabelText(/Lý do duyệt gói hợp đồng/), "Duyệt.");
    await userEvent.click(screen.getByRole("button", { name: "Duyệt nội dung gói hợp đồng" }));
    expect(await screen.findByText("Phát hiện thay đổi trọng yếu")).toBeVisible();
    // The fenced state removes the gate forms.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Duyệt nội dung gói hợp đồng" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps the draft and offers a reload on a non-material 409 (gate order)", async () => {
    const api = fakeApi({
      confirmSignatureAuthority: vi
        .fn()
        .mockRejectedValue(new ApiClientError(409, "GATE_ORDER_VIOLATION", "", false)),
    });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Nội dung gói hợp đồng");
    await userEvent.type(
      screen.getByLabelText(/Lý do xác nhận thẩm quyền ký/),
      "Người ký có thẩm quyền.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận thẩm quyền ký kết" }));
    expect(await screen.findByRole("button", { name: "Tải lại" })).toBeVisible();
  });

  it("shows the mock-signature label and requires at least one signer", async () => {
    const api = fakeApi();
    render(<ContractWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Ghi nhận chữ ký mô phỏng" });
    // The 'Chữ ký mô phỏng' label appears in the sign section lead.
    expect(screen.getByText(/Nhãn: Chữ ký mô phỏng/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận chữ ký mô phỏng" }));
    expect(screen.getByText("Nhập ít nhất một người ký (mỗi dòng một tên).")).toBeVisible();
    expect(api.sign).not.toHaveBeenCalled();
  });

  it("signs with the entered signer names", async () => {
    const api = fakeApi();
    render(<ContractWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("heading", { name: "Ghi nhận chữ ký mô phỏng" });
    await userEvent.type(
      screen.getByLabelText(/Người ký \(mỗi dòng một tên\)/),
      "Nguyễn Văn A\nTrần B",
    );
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận chữ ký mô phỏng" }));
    await waitFor(() =>
      expect(api.sign).toHaveBeenCalledWith("case-1", {
        signerNames: ["Nguyễn Văn A", "Trần B"],
        evidenceNote: undefined,
      }),
    );
  });

  it("renders the signed mock evidence and hides the gate forms once signed", async () => {
    const api = fakeApi({
      getView: vi.fn(async () =>
        buildView({
          package: { ...buildView().package, state: "READY_FOR_SIGNATURE" },
          signatureEvidence: {
            id: "sig-1",
            packageId: "pkg-1",
            kind: "MOCK_SIGNATURE",
            signerNames: ["Nguyễn Văn A"],
            evidenceNote: null,
            recordedBy: "checker-1",
            createdAt: "2026-07-18T09:00:00Z",
          },
        }),
      ),
    });
    render(<ContractWorkspace api={api} caseId="case-1" />);
    expect(await screen.findAllByText("Chữ ký mô phỏng")).not.toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: "Duyệt nội dung gói hợp đồng" }),
    ).not.toBeInTheDocument();
  });
});
