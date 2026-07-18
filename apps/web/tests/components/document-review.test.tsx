import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { DocumentReview } from "../../components/review/document-review";
import { ApiClientError } from "../../lib/api/client";
import type {
  CandidateFactDto,
  DocumentReviewDto,
  PageRegionDto,
} from "../../lib/api/contracts";

function region(page: number): PageRegionDto {
  return { page, x: 0.1, y: 0.2, width: 0.3, height: 0.1 };
}

function amountCandidate(): CandidateFactDto {
  return {
    id: "cand-amount",
    caseId: "case-1",
    caseVersion: 2,
    documentVersionId: "dv-1",
    fieldKey: "requested_amount",
    proposedValue: "5000000000",
    confidence: 0.91,
    source: region(1),
  };
}

function purposeCandidate(): CandidateFactDto {
  return {
    id: "cand-purpose",
    caseId: "case-1",
    caseVersion: 2,
    documentVersionId: "dv-1",
    fieldKey: "purpose",
    proposedValue: "Bổ sung vốn lưu động",
    confidence: 0.72,
    source: { page: 2, x: 0.2, y: 0.3, width: 0.25, height: 0.08 },
  };
}

function twoCandidateReview(): DocumentReviewDto {
  return {
    documentId: "doc-1",
    caseId: "case-1",
    documentVersionId: "dv-1",
    documentVersion: 3,
    stage: "READY_FOR_OFFICER_REVIEW",
    fileName: "ho-so-tong-hop.pdf",
    pageCount: 2,
    candidates: [amountCandidate(), purposeCandidate()],
  };
}

function oneCandidateReview(): DocumentReviewDto {
  return { ...twoCandidateReview(), candidates: [amountCandidate()] };
}

describe("DocumentReview", () => {
  it("requires one disposition for every candidate before confirmation", async () => {
    render(<DocumentReview review={twoCandidateReview()} />);

    await userEvent.click(screen.getByLabelText("Chấp nhận Số tiền đề nghị"));

    expect(
      screen.getByRole("button", { name: "Xác nhận tài liệu" }),
    ).toBeDisabled();
  });

  it("enables confirmation only once every candidate is resolved", async () => {
    render(<DocumentReview review={twoCandidateReview()} canConfirm />);
    const confirm = screen.getByRole("button", { name: "Xác nhận tài liệu" });

    expect(confirm).toBeDisabled();

    await userEvent.click(screen.getByLabelText("Chấp nhận Số tiền đề nghị"));
    // The second candidate is still unresolved, so it keeps the button disabled.
    expect(confirm).toBeDisabled();

    await userEvent.click(
      screen.getByLabelText("Không có trong tài liệu Mục đích vay vốn"),
    );
    expect(confirm).toBeEnabled();
  });

  it("does not resolve a CORRECTED candidate until value and rationale are filled", async () => {
    render(<DocumentReview review={oneCandidateReview()} canConfirm />);
    const confirm = screen.getByRole("button", { name: "Xác nhận tài liệu" });

    await userEvent.click(screen.getByLabelText("Chỉnh sửa Số tiền đề nghị"));
    expect(confirm).toBeDisabled();

    await userEvent.type(
      screen.getByLabelText("Giá trị đã chỉnh sửa"),
      "4000000000",
    );
    expect(confirm).toBeDisabled();

    await userEvent.type(
      screen.getByLabelText("Lý do chỉnh sửa"),
      "Sai lệch so với hợp đồng",
    );
    expect(confirm).toBeEnabled();
  });

  it("submits the exact confirmation payload with corrected fields", async () => {
    const confirmDocument = vi.fn().mockResolvedValue(undefined);
    render(
      <DocumentReview
        review={twoCandidateReview()}
        canConfirm
        api={{ confirmDocument }}
      />,
    );

    await userEvent.click(screen.getByLabelText("Chấp nhận Số tiền đề nghị"));
    await userEvent.click(screen.getByLabelText("Chỉnh sửa Mục đích vay vốn"));
    await userEvent.type(
      screen.getByLabelText("Giá trị đã chỉnh sửa"),
      "Đầu tư thiết bị",
    );
    await userEvent.type(
      screen.getByLabelText("Lý do chỉnh sửa"),
      "Điều chỉnh theo hợp đồng",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Xác nhận tài liệu" }),
    );

    await waitFor(() => expect(confirmDocument).toHaveBeenCalledTimes(1));
    expect(confirmDocument).toHaveBeenCalledWith("doc-1", {
      expectedDocumentVersion: 3,
      dispositions: [
        { candidateId: "cand-amount", disposition: "ACCEPTED" },
        {
          candidateId: "cand-purpose",
          disposition: "CORRECTED",
          correctedValue: "Đầu tư thiết bị",
          rationale: "Điều chỉnh theo hợp đồng",
        },
      ],
    });
  });

  it("preserves the draft and shows the stale-version alert on a 409 conflict", async () => {
    const confirmDocument = vi
      .fn()
      .mockRejectedValue(
        new ApiClientError(
          409,
          "STALE_DOCUMENT_VERSION",
          "Phiên bản tài liệu đã thay đổi.",
          false,
        ),
      );
    render(
      <DocumentReview
        review={oneCandidateReview()}
        canConfirm
        api={{ confirmDocument }}
      />,
    );

    await userEvent.click(screen.getByLabelText("Chấp nhận Số tiền đề nghị"));
    await userEvent.click(
      screen.getByRole("button", { name: "Xác nhận tài liệu" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Phiên bản tài liệu đã thay đổi");
    // Draft is preserved: the previously chosen radio is still checked.
    expect(screen.getByLabelText("Chấp nhận Số tiền đề nghị")).toBeChecked();
    expect(
      screen.getByRole("button", { name: "Tải lại phiên bản mới" }),
    ).toBeVisible();
    // No auto-retry.
    expect(confirmDocument).toHaveBeenCalledTimes(1);
  });

  it("highlights the source region of the candidate whose region button is clicked", async () => {
    render(<DocumentReview review={twoCandidateReview()} />);

    const regionButtons = screen.getAllByRole("button", {
      name: "Xem vùng nguồn",
    });
    await userEvent.click(regionButtons[1]);

    expect(
      screen.getByLabelText("Vùng nguồn Mục đích vay vốn, trang 2"),
    ).toHaveAttribute("data-selected", "true");
  });

  it("auto-focuses the first unresolved candidate fieldset on mount", () => {
    render(<DocumentReview review={twoCandidateReview()} canConfirm />);

    const fieldset = screen.getByText("Số tiền đề nghị").closest("fieldset");
    expect(fieldset).toHaveFocus();
  });

  it("fails closed when the officer lacks confirm capability", () => {
    render(<DocumentReview review={twoCandidateReview()} canConfirm={false} />);

    expect(screen.getByLabelText("Chấp nhận Số tiền đề nghị")).toBeDisabled();
    expect(
      screen.getByText("Bạn không có quyền xác nhận tài liệu này."),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Xác nhận tài liệu" }),
    ).toBeDisabled();
  });

  it("explains an empty candidate set and keeps confirmation disabled", () => {
    render(
      <DocumentReview
        review={{ ...twoCandidateReview(), candidates: [] }}
        canConfirm
      />,
    );

    expect(
      screen.getByText("Chưa có dữ liệu trích xuất cho tài liệu này."),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Xác nhận tài liệu" }),
    ).toBeDisabled();
  });

  it("renders boolean and numeric proposed values in Vietnamese", () => {
    const booleanCandidate: CandidateFactDto = {
      ...amountCandidate(),
      id: "cand-flag",
      fieldKey: "has_collateral_flag",
      proposedValue: true,
    };
    const numericCandidate: CandidateFactDto = {
      ...purposeCandidate(),
      id: "cand-number",
      fieldKey: "employee_count",
      proposedValue: 1250,
    };

    render(
      <DocumentReview
        review={{
          ...twoCandidateReview(),
          candidates: [booleanCandidate, numericCandidate],
        }}
      />,
    );

    expect(screen.getByText("Có")).toBeVisible();
    expect(screen.getByText("1.250")).toBeVisible();
    expect(screen.queryByText("true")).not.toBeInTheDocument();
  });
});
