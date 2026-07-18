import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { AuditTimeline, AuditWorkspace } from "../../components/audit/audit-timeline";
import type { AuditEventView } from "../../components/audit/audit-timeline";
import { ConflictList } from "../../components/evidence/conflict-list";
import { EvidenceDashboard, FactLedger } from "../../components/evidence/fact-ledger";
import { GapList, GapWorkspace, type GapView } from "../../components/gaps/gap-list";
import { IntakeCompletionDialog } from "../../components/gaps/intake-completion-dialog";
import { HandoffSummary, HandoffWorkspace } from "../../components/handoff/handoff-summary";
import type { HandoffView } from "../../components/handoff/handoff-summary";
import type {
  ConfirmedFactDto,
  ConflictDto,
  CreditCaseDto,
} from "../../lib/api/contracts";

// Consolidated review-dashboard suite (plan Task 11 deliverable). Merges the
// former evidence-dashboard, gap-workspace, and handoff-audit component tests
// into one file, one describe block per review area, preserving every case.

describe("Evidence dashboard", () => {
  const creditCase: CreditCaseDto = {
    id: "case-evidence",
    version: 3,
    assignedOfficerId: "officer-synthetic",
    requestedAmount: "5000000000",
    purpose: "Bổ sung vốn lưu động",
    workflowState: "INTAKE",
    updatedAt: "2026-07-17T08:00:00Z",
    capabilities: { canUpload: true, canConfirm: true, canCompleteIntake: false },
  };

  function buildFact(overrides: Partial<ConfirmedFactDto> = {}): ConfirmedFactDto {
    return {
      id: "fact-1",
      caseId: "case-evidence",
      caseVersion: 3,
      candidateId: "candidate-1",
      confirmationId: "confirmation-1",
      documentVersionId: "docver-1",
      fieldKey: "requested_amount",
      value: "5000000000",
      candidateValue: "5000000000",
      source: { page: 2, x: 0.1, y: 0.1, width: 0.5, height: 0.1 },
      confirmedAt: "2026-07-17T09:00:00Z",
      stale: false,
      ...overrides,
    };
  }

  function buildConflict(overrides: Partial<ConflictDto> = {}): ConflictDto {
    return {
      id: "conflict-1",
      caseId: "case-evidence",
      caseVersion: 3,
      fieldKey: "purpose",
      sources: [
        {
          documentVersionId: "docver-a",
          value: "Bổ sung vốn lưu động",
          source: { page: 1, x: 0, y: 0, width: 0.4, height: 0.1 },
        },
        {
          documentVersionId: "docver-b",
          value: "Mua nguyên vật liệu",
          source: { page: 3, x: 0, y: 0, width: 0.4, height: 0.1 },
        },
      ],
      detectedAt: "2026-07-17T09:30:00Z",
      stale: false,
      ...overrides,
    };
  }

  describe("FactLedger", () => {
    it("shows the confirmed value and the original candidate value for a corrected fact, with its source page", () => {
      const untouched = buildFact();
      const corrected = buildFact({
        id: "fact-corrected",
        fieldKey: "purpose",
        value: "Bổ sung vốn lưu động",
        candidateValue: "Mua nguyên vật liệu",
        source: { page: 4, x: 0, y: 0, width: 0.3, height: 0.1 },
      });

      render(<FactLedger facts={[untouched, corrected]} />);

      expect(screen.getByText("Sổ cái dữ kiện đã xác nhận")).toBeVisible();
      expect(
        screen.getByRole("columnheader", { name: "Giá trị trích xuất gốc" }),
      ).toBeVisible();
      // Confirmed (corrected) value and the original candidate value are both visible.
      expect(screen.getByText("Bổ sung vốn lưu động")).toBeVisible();
      expect(screen.getByText("Mua nguyên vật liệu")).toBeVisible();
      expect(screen.getByText("Trang 4")).toBeVisible();
      expect(screen.getByText("Trang 2")).toBeVisible();
    });

    it("keeps a stale fact listed and visibly marked, never hidden", () => {
      const stale = buildFact({
        id: "fact-stale",
        fieldKey: "purpose",
        value: "Mua thiết bị",
        candidateValue: "Mua thiết bị",
        stale: true,
      });

      render(<FactLedger facts={[stale]} />);

      expect(screen.getByText("Đã lỗi thời")).toBeVisible();
      expect(screen.getByText("Mua thiết bị")).toBeVisible();
    });

    it("shows the empty state when no facts are confirmed yet", () => {
      render(<FactLedger facts={[]} />);

      expect(
        screen.getByText("Chưa có dữ kiện nào được xác nhận."),
      ).toBeVisible();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
    });
  });

  describe("ConflictList", () => {
    it("shows every source and no control for choosing a winner", () => {
      const conflict = buildConflict({
        sources: [
          {
            documentVersionId: "docver-a",
            value: "Giá trị A",
            source: { page: 1, x: 0, y: 0, width: 0.2, height: 0.1 },
          },
          {
            documentVersionId: "docver-b",
            value: "Giá trị B",
            source: { page: 2, x: 0, y: 0, width: 0.2, height: 0.1 },
          },
          {
            documentVersionId: "docver-c",
            value: "Giá trị C",
            source: null,
          },
        ],
      });

      render(<ConflictList conflicts={[conflict]} />);

      expect(screen.getByText("Mâu thuẫn chứng cứ")).toBeVisible();
      const item = screen.getByRole("listitem");
      expect(within(item).getByText("Giá trị A")).toBeVisible();
      expect(within(item).getByText("Giá trị B")).toBeVisible();
      expect(within(item).getByText("Giá trị C")).toBeVisible();
      expect(within(item).getByText("Trang 1")).toBeVisible();
      expect(within(item).getByText("Trang 2")).toBeVisible();
      expect(within(item).queryByRole("button")).not.toBeInTheDocument();
      expect(within(item).queryByRole("radio")).not.toBeInTheDocument();
      expect(
        within(item).getByText(
          "Hệ thống không tự chọn giá trị đúng. Mâu thuẫn chờ cán bộ xử lý.",
        ),
      ).toBeVisible();
    });

    it("shows the stale badge on a stale conflict", () => {
      render(<ConflictList conflicts={[buildConflict({ stale: true })]} />);

      expect(screen.getByText("Đã lỗi thời")).toBeVisible();
    });

    it("shows the empty state when no conflicts are detected", () => {
      render(<ConflictList conflicts={[]} />);

      expect(
        screen.getByText("Không phát hiện mâu thuẫn giữa các tài liệu."),
      ).toBeVisible();
      expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    });
  });

  describe("EvidenceDashboard", () => {
    it("shows the loaded ledger and an inline retry panel when only conflicts fail; retry refetches only conflicts", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(creditCase),
        listEvidence: vi.fn().mockResolvedValue({ items: [buildFact()] }),
        listConflicts: vi
          .fn()
          .mockRejectedValueOnce(new Error("offline"))
          .mockResolvedValueOnce({ items: [buildConflict()] }),
      };

      render(<EvidenceDashboard api={api} caseId="case-evidence" />);

      expect(screen.getByLabelText("Đang tải đối chiếu chứng cứ")).toBeVisible();

      expect(
        await screen.findByRole("heading", { name: "Đối chiếu chứng cứ" }),
      ).toBeVisible();
      expect(screen.getByText("Sổ cái dữ kiện đã xác nhận")).toBeVisible();
      expect(screen.getByRole("alert")).toBeVisible();
      expect(screen.queryByText("Mâu thuẫn chứng cứ")).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));

      await waitFor(() =>
        expect(screen.getByText("Mâu thuẫn chứng cứ")).toBeVisible(),
      );
      expect(api.listConflicts).toHaveBeenCalledTimes(2);
      expect(api.getCase).toHaveBeenCalledTimes(1);
      expect(api.listEvidence).toHaveBeenCalledTimes(1);
    });
  });
});

describe("Gaps workspace", () => {
  const allGaps: GapView[] = [
    {
      id: "gap-provisional",
      status: "PROVISIONAL",
      issueVi: "Thiếu báo cáo tài chính năm gần nhất",
      missingInformationVi: "Chưa có báo cáo tài chính đã kiểm toán năm 2025",
      suggestedEvidenceVi: ["Báo cáo tài chính năm 2025", "Biên bản họp cổ đông"],
    },
    {
      id: "gap-formal",
      status: "FORMAL",
      issueVi: "Thiếu hợp đồng thuê nhà xưởng",
      missingInformationVi: "Chưa có bản sao hợp đồng thuê nhà xưởng còn hiệu lực",
      suggestedEvidenceVi: [],
    },
    {
      id: "gap-resolved",
      status: "RESOLVED",
      issueVi: "Thiếu giấy phép kinh doanh",
      missingInformationVi: "Đã bổ sung giấy phép kinh doanh hợp lệ",
      suggestedEvidenceVi: [],
    },
    {
      id: "gap-stale",
      status: "STALE",
      issueVi: "Thiếu bảng lương nhân viên",
      missingInformationVi: "Yêu cầu này không còn áp dụng cho hồ sơ hiện tại",
      suggestedEvidenceVi: [],
    },
  ];

  describe("GapList", () => {
    it("renders all four statuses with exact Vietnamese badges; resolved/stale still listed", () => {
      render(<GapList gaps={allGaps} />);

      expect(screen.getByRole("heading", { name: "Khoảng trống chứng cứ" })).toBeVisible();
      expect(screen.getByText("Tạm thời")).toBeVisible();
      expect(screen.getByText("Chính thức")).toBeVisible();
      expect(screen.getByText("Đã giải quyết")).toBeVisible();
      expect(screen.getByText("Đã lỗi thời")).toBeVisible();

      expect(screen.getByText("Thiếu báo cáo tài chính năm gần nhất")).toBeVisible();
      expect(screen.getByText("Thiếu giấy phép kinh doanh")).toBeVisible();
      expect(screen.getByText("Thiếu bảng lương nhân viên")).toBeVisible();
    });

    it("shows the draft/not-approved suggestion label only when suggestions exist", () => {
      render(<GapList gaps={allGaps} />);

      const labels = screen.getAllByText("Đề xuất tài liệu (bản nháp, chưa được phê duyệt)");
      expect(labels).toHaveLength(1);
      expect(screen.getByText("Báo cáo tài chính năm 2025")).toBeVisible();
      expect(screen.getByText("Biên bản họp cổ đông")).toBeVisible();
    });

    it("renders no close/resolve controls", () => {
      render(<GapList gaps={allGaps} />);

      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("shows the empty state when there are no gaps", () => {
      render(<GapList gaps={[]} />);

      expect(screen.getByText("Chưa ghi nhận khoảng trống chứng cứ.")).toBeVisible();
    });
  });

  describe("IntakeCompletionDialog", () => {
    const baseProps = {
      onClose: vi.fn(),
      onConfirm: vi.fn(),
      openGapCount: 0,
      caseVersion: 3,
      canCompleteIntake: true,
    };

    it("is not in the document when closed", () => {
      render(<IntakeCompletionDialog {...baseProps} open={false} />);

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("shows the heading and mandatory sentence, gates confirm on the checkbox, and calls onConfirm once", async () => {
      const user = userEvent.setup();
      const onConfirm = vi.fn();

      render(
        <IntakeCompletionDialog
          {...baseProps}
          onConfirm={onConfirm}
          open
        />,
      );

      expect(
        screen.getByRole("heading", { name: "Hoàn tất bộ hồ sơ tiếp nhận", level: 2 }),
      ).toBeVisible();
      expect(
        screen.getByText(/Đây không phải quyết định tín dụng\./),
      ).toBeVisible();

      const confirmButton = screen.getByRole("button", { name: "Hoàn tất tiếp nhận" });
      expect(confirmButton).toBeDisabled();

      await user.click(
        screen.getByLabelText(
          "Tôi xác nhận đã rà soát toàn bộ tài liệu và khoảng trống chứng cứ.",
        ),
      );
      expect(confirmButton).toBeEnabled();

      await user.click(confirmButton);
      expect(onConfirm).toHaveBeenCalledTimes(1);
    });

    it("shows the open-gap warning panel when openGapCount > 0", () => {
      render(<IntakeCompletionDialog {...baseProps} open openGapCount={4} />);

      expect(
        screen.getByText("Còn 4 khoảng trống chứng cứ chưa giải quyết."),
      ).toBeVisible();
    });

    it("hides the confirm button and shows the permission note when canCompleteIntake is false", () => {
      render(<IntakeCompletionDialog {...baseProps} canCompleteIntake={false} open />);

      expect(
        screen.queryByRole("button", { name: "Hoàn tất tiếp nhận" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText("Bạn không có quyền hoàn tất tiếp nhận hồ sơ này."),
      ).toBeVisible();
    });

    it("keeps confirm disabled and shows the reason when submitUnavailableReason is set, even after checking", async () => {
      const user = userEvent.setup();

      render(
        <IntakeCompletionDialog
          {...baseProps}
          open
          submitUnavailableReason="Hợp đồng API hoàn tất tiếp nhận chưa được công bố; thao tác sẽ khả dụng khi backend phát hành."
        />,
      );

      expect(
        screen.getByText(
          "Hợp đồng API hoàn tất tiếp nhận chưa được công bố; thao tác sẽ khả dụng khi backend phát hành.",
        ),
      ).toBeVisible();

      const confirmButton = screen.getByRole("button", { name: "Hoàn tất tiếp nhận" });
      expect(confirmButton).toBeDisabled();

      await user.click(
        screen.getByLabelText(
          "Tôi xác nhận đã rà soát toàn bộ tài liệu và khoảng trống chứng cứ.",
        ),
      );
      expect(confirmButton).toBeDisabled();
    });

    it("closes on Escape", () => {
      const onClose = vi.fn();

      render(<IntakeCompletionDialog {...baseProps} onClose={onClose} open />);

      fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("closes when the Hủy button is clicked", () => {
      const onClose = vi.fn();

      render(<IntakeCompletionDialog {...baseProps} onClose={onClose} open />);

      fireEvent.click(screen.getByRole("button", { name: "Hủy" }));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("traps Tab focus inside the dialog and never reaches background controls", async () => {
      const user = userEvent.setup();

      render(
        <div>
          <button type="button">trước hộp thoại</button>
          <IntakeCompletionDialog {...baseProps} open />
        </div>,
      );

      const outside = screen.getByRole("button", { name: "trước hộp thoại" });
      const checkbox = screen.getByLabelText(
        "Tôi xác nhận đã rà soát toàn bộ tài liệu và khoảng trống chứng cứ.",
      );
      // Enable confirm so all three dialog controls participate in the tab cycle.
      await user.click(checkbox);
      const confirm = screen.getByRole("button", { name: "Hoàn tất tiếp nhận" });

      // From the last control, Tab forward must wrap to the first control inside
      // the dialog — not spill onto the background trigger button.
      confirm.focus();
      expect(confirm).toHaveFocus();
      await user.tab();
      expect(checkbox).toHaveFocus();
      expect(outside).not.toHaveFocus();

      // From the first control, Shift+Tab must wrap back to the last control.
      await user.tab({ shift: true });
      expect(confirm).toHaveFocus();
      expect(outside).not.toHaveFocus();
    });
  });

  describe("GapWorkspace (page loader)", () => {
    const creditCase = (canCompleteIntake: boolean): CreditCaseDto => ({
      id: "case-1",
      version: 5,
      assignedOfficerId: "officer-synthetic",
      requestedAmount: "1000000000",
      purpose: "Bổ sung vốn lưu động",
      workflowState: "INTAKE",
      updatedAt: "2026-07-17T08:00:00Z",
      capabilities: { canUpload: true, canConfirm: true, canCompleteIntake },
    });

    it("hides the completion trigger and shows the contract-pending panel when canCompleteIntake is false", async () => {
      const api = { getCase: vi.fn().mockResolvedValue(creditCase(false)) };

      render(<GapWorkspace api={api} caseId="case-1" />);

      expect(await screen.findByRole("heading", { name: "Khoảng trống chứng cứ" })).toBeVisible();
      expect(
        screen.getByText(
          "Danh sách khoảng trống chưa khả dụng: máy chủ chưa công bố hợp đồng API cho khoảng trống chứng cứ (kế hoạch Task 9).",
        ),
      ).toBeVisible();
      expect(
        screen.queryByRole("button", { name: "Hoàn tất tiếp nhận…" }),
      ).not.toBeInTheDocument();
    });

    it("shows the completion trigger and opens the gated dialog when canCompleteIntake is true", async () => {
      const api = { getCase: vi.fn().mockResolvedValue(creditCase(true)) };

      render(<GapWorkspace api={api} caseId="case-1" />);

      const trigger = await screen.findByRole("button", { name: "Hoàn tất tiếp nhận…" });
      expect(trigger).toBeVisible();
      expect(
        screen.getByText(
          "Danh sách khoảng trống chưa khả dụng: máy chủ chưa công bố hợp đồng API cho khoảng trống chứng cứ (kế hoạch Task 9).",
        ),
      ).toBeVisible();

      fireEvent.click(trigger);

      expect(await screen.findByRole("dialog")).toBeVisible();
      expect(
        screen.getByText(
          "Hợp đồng API hoàn tất tiếp nhận chưa được công bố; thao tác sẽ khả dụng khi backend phát hành.",
        ),
      ).toBeVisible();
      expect(screen.getByRole("button", { name: "Hoàn tất tiếp nhận" })).toBeDisabled();
    });

    it("lets the officer retry a failed case request", async () => {
      const api = {
        getCase: vi
          .fn()
          .mockRejectedValueOnce(new Error("offline"))
          .mockResolvedValueOnce(creditCase(false)),
      };

      render(<GapWorkspace api={api} caseId="case-1" />);

      fireEvent.click(await screen.findByRole("button", { name: "Thử tải lại" }));

      await waitFor(() => expect(api.getCase).toHaveBeenCalledTimes(2));
      expect(await screen.findByRole("heading", { name: "Khoảng trống chứng cứ" })).toBeVisible();
    });
  });
});

describe("Handoff and audit", () => {
  function readyHandoff(overrides: Partial<HandoffView> = {}): HandoffView {
    return {
      id: "handoff-1",
      caseVersion: 3,
      state: "READY_FOR_SPECIALIST_REVIEW",
      stale: false,
      confirmedFactCount: 5,
      conflictCount: 2,
      gapCount: 1,
      createdAt: "2026-07-17T08:00:00Z",
      ...overrides,
    };
  }

  function makeEvent(overrides: Partial<AuditEventView> = {}): AuditEventView {
    return {
      id: "evt-1",
      caseVersion: 3,
      eventType: "DOCUMENT_CONFIRMED",
      actorType: "officer",
      actorId: "officer-synthetic-01",
      artifactType: "document",
      artifactId: "doc-abcdef1234567890",
      createdAt: "2026-07-17T08:00:00Z",
      ...overrides,
    };
  }

  describe("HandoffSummary", () => {
    it("labels handoff as not a credit decision", () => {
      render(<HandoffSummary handoff={readyHandoff()} />);
      expect(screen.getByText("Không phải quyết định tín dụng")).toBeVisible();
    });

    it("shows the exact version line and counts for a ready handoff", () => {
      render(<HandoffSummary handoff={readyHandoff({ caseVersion: 7 })} />);

      expect(screen.getByText("Phiên bản hồ sơ: 7")).toBeVisible();
      expect(screen.getByText("Sẵn sàng cho chuyên viên thẩm định")).toBeVisible();
      expect(screen.getByText("5")).toBeVisible();
      expect(screen.getByText("2")).toBeVisible();
      expect(screen.getByText("1")).toBeVisible();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByText("Đã lỗi thời")).not.toBeInTheDocument();
    });

    it("shows the stale warning and badge while still rendering the handoff", () => {
      render(<HandoffSummary handoff={readyHandoff({ stale: true, caseVersion: 3 })} />);

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Gói bàn giao đã lỗi thời do hồ sơ thay đổi. Cần tạo lại sau khi xử lý thay đổi.",
      );
      expect(screen.getByText("Đã lỗi thời")).toBeVisible();
      expect(screen.getByText("Không phải quyết định tín dụng")).toBeVisible();
      expect(screen.getByText("Phiên bản hồ sơ: 3")).toBeVisible();
    });
  });

  describe("AuditTimeline", () => {
    it("renders events in the given order without re-sorting, with actor/artifact/version lines", () => {
      const events = [
        makeEvent({ id: "evt-a", eventType: "CASE_CREATED" }),
        makeEvent({ id: "evt-b", eventType: "DOCUMENT_REGISTERED" }),
      ];
      render(<AuditTimeline events={events} nextCursor={null} />);

      const items = screen.getAllByRole("listitem");
      expect(items).toHaveLength(2);
      expect(items[0]).toHaveTextContent("CASE_CREATED");
      expect(items[1]).toHaveTextContent("DOCUMENT_REGISTERED");
      expect(items[0]).toHaveTextContent("Tác nhân: officer");
      expect(items[0]).toHaveTextContent("Đối tượng: document");
      expect(items[0]).toHaveTextContent("Phiên bản hồ sơ: 3");
    });

    it("shows the load-more button and calls onLoadMore with the cursor", () => {
      const onLoadMore = vi.fn();
      render(<AuditTimeline events={[makeEvent()]} nextCursor="cursor-2" onLoadMore={onLoadMore} />);

      fireEvent.click(screen.getByRole("button", { name: "Tải thêm sự kiện" }));
      expect(onLoadMore).toHaveBeenCalledWith("cursor-2");
    });

    it("disables the load-more button while loading", () => {
      render(
        <AuditTimeline
          events={[makeEvent()]}
          loadingMore
          nextCursor="cursor-2"
          onLoadMore={vi.fn()}
        />,
      );

      const button = screen.getByRole("button", { name: "Tải thêm sự kiện" });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("aria-busy", "true");
    });

    it("does not show a load-more button without a cursor", () => {
      render(<AuditTimeline events={[makeEvent()]} nextCursor={null} onLoadMore={vi.fn()} />);
      expect(screen.queryByRole("button", { name: "Tải thêm sự kiện" })).not.toBeInTheDocument();
    });

    it("shows an empty state when there are no events", () => {
      render(<AuditTimeline events={[]} nextCursor={null} />);
      expect(screen.getByText("Chưa có sự kiện nào được ghi nhận.")).toBeVisible();
    });
  });

  // ban-giao route: the page is a thin server component delegating to the
  // HandoffWorkspace client loader (same page/loader split as the doi-chieu and
  // khoang-trong routes). The loader carries the contract-pending behavior, so
  // it is exercised directly here, exactly like the EvidenceDashboard and
  // GapWorkspace loader tests above.
  describe("HandoffWorkspace (ban-giao loader)", () => {
    it("loads the case and shows the contract-pending panel plus the non-decision note", async () => {
      const creditCase: CreditCaseDto = {
        id: "case-1",
        version: 5,
        assignedOfficerId: "officer-synthetic",
        requestedAmount: "1000000000",
        purpose: "Bổ sung vốn lưu động",
        workflowState: "REVIEW",
        updatedAt: "2026-07-17T08:00:00Z",
        capabilities: { canUpload: true, canConfirm: true, canCompleteIntake: false },
      };
      const api = { getCase: vi.fn().mockResolvedValue(creditCase) };

      render(<HandoffWorkspace api={api} caseId="case-1" />);

      expect(
        await screen.findByText(
          "Gói bàn giao chưa khả dụng: máy chủ chưa công bố hợp đồng API bàn giao (kế hoạch Task 9).",
        ),
      ).toBeVisible();
      expect(screen.getByText("Không phải quyết định tín dụng")).toBeVisible();
      expect(screen.getByText("Hồ sơ · phiên bản 5")).toBeVisible();
      expect(api.getCase).toHaveBeenCalledWith("case-1");
    });
  });

  // nhat-ky route: same page/loader split as ban-giao; the AuditWorkspace client
  // loader carries the contract-pending behavior and is exercised directly here.
  describe("AuditWorkspace (nhat-ky loader)", () => {
    it("loads the case and shows the contract-pending panel", async () => {
      const creditCase: CreditCaseDto = {
        id: "case-1",
        version: 5,
        assignedOfficerId: "officer-synthetic",
        requestedAmount: "1000000000",
        purpose: "Bổ sung vốn lưu động",
        workflowState: "REVIEW",
        updatedAt: "2026-07-17T08:00:00Z",
        capabilities: { canUpload: true, canConfirm: true, canCompleteIntake: false },
      };
      const api = { getCase: vi.fn().mockResolvedValue(creditCase) };

      render(<AuditWorkspace api={api} caseId="case-1" />);

      expect(
        await screen.findByText(
          "Nhật ký chưa khả dụng: máy chủ chưa công bố hợp đồng API nhật ký (kế hoạch Task 9).",
        ),
      ).toBeVisible();
      expect(screen.getByText("Hồ sơ · phiên bản 5")).toBeVisible();
      expect(api.getCase).toHaveBeenCalledWith("case-1");
    });
  });
});
