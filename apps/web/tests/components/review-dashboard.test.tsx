import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { AuditTimeline, AuditWorkspace } from "../../components/audit/audit-timeline";
import type { AuditEventView } from "../../components/audit/audit-timeline";
import { ConflictList } from "../../components/evidence/conflict-list";
import { EvidenceDashboard, FactLedger } from "../../components/evidence/fact-ledger";
import {
  GapList,
  GapWorkspace,
  type GapRequestItemView,
} from "../../components/gaps/gap-list";
import { IntakeCompletionDialog } from "../../components/gaps/intake-completion-dialog";
import { HandoffSummary, HandoffWorkspace } from "../../components/handoff/handoff-summary";
import type { HandoffView } from "../../components/handoff/handoff-summary";
import { ApiClientError } from "../../lib/api/client";
import type {
  AuditEventDto,
  ConfirmedFactDto,
  ConflictDto,
  CreditCaseDto,
  IntakeCompletionResultDto,
} from "../../lib/api/contracts";
import type { GapRequestBatchStatus } from "../../lib/api/gap-requests";

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
  const ITEM_A = "11111111-1111-4111-8111-111111111111";
  const ITEM_B = "22222222-2222-4222-8222-222222222222";
  const GAP_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const HASH = "a".repeat(64);

  function buildItem(overrides: Partial<GapRequestItemView> = {}): GapRequestItemView {
    return {
      id: ITEM_A,
      gapId: GAP_A,
      requestText: "Bổ sung báo cáo tài chính đã kiểm toán năm 2025",
      blockingLevel: "BLOCKING",
      ...overrides,
    };
  }

  function buildBatchStatus(
    overrides: Partial<GapRequestBatchStatus> = {},
  ): GapRequestBatchStatus {
    return {
      batch: {
        batchId: "batch-1",
        caseId: "case-1",
        caseVersion: 5,
        openGapSnapshotHash: HASH,
        items: [buildItem()],
      },
      stale: false,
      currentOpenGapHash: HASH,
      dispositions: [],
      gateStatus: "OPEN",
      ...overrides,
    };
  }

  function buildCase(canCompleteIntake = false): CreditCaseDto {
    return {
      id: "case-1",
      version: 5,
      assignedOfficerId: "officer-synthetic",
      requestedAmount: "1000000000",
      purpose: "Bổ sung vốn lưu động",
      workflowState: "INTAKE",
      updatedAt: "2026-07-17T08:00:00Z",
      capabilities: { canUpload: true, canConfirm: true, canCompleteIntake },
    };
  }

  describe("GapList", () => {
    it("renders each drafted request with its blocking-level badge and text; no approve/remove control", () => {
      const items: GapRequestItemView[] = [
        buildItem(),
        buildItem({ id: ITEM_B, blockingLevel: "CONDITIONAL", requestText: "Bổ sung hợp đồng thuê xưởng" }),
        buildItem({ id: "33333333-3333-4333-8333-333333333333", blockingLevel: "CLARIFICATION", requestText: "Làm rõ mục đích vay" }),
      ];

      render(<GapList items={items} />);

      expect(
        screen.getByRole("heading", { name: "Danh sách yêu cầu bổ sung bằng chứng" }),
      ).toBeVisible();
      expect(screen.getByText("Chặn")).toBeVisible();
      expect(screen.getByText("Có điều kiện")).toBeVisible();
      expect(screen.getByText("Cần làm rõ")).toBeVisible();
      expect(screen.getByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025")).toBeVisible();
      expect(screen.getByText("Bổ sung hợp đồng thuê xưởng")).toBeVisible();
      // Read-only: a request is only ever dispositioned through the batch form.
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("fails closed on an unknown blocking level", () => {
      render(<GapList items={[buildItem({ blockingLevel: "SOMETHING_NEW" })]} />);
      expect(screen.getByText("Trạng thái chưa được hỗ trợ")).toBeVisible();
    });

    it("shows the empty state when there are no drafted requests", () => {
      render(<GapList items={[]} />);
      expect(
        screen.getByText(
          "Không có yêu cầu bổ sung nào: hiện không còn khoảng trống chứng cứ đang mở.",
        ),
      ).toBeVisible();
    });
  });

  describe("GapWorkspace — batch load and assemble", () => {
    it("does not assemble a batch on render — it only GETs the current batch", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(buildCase()),
        completeIntake: vi.fn(),
      };
      const gapApi = {
        getBatch: vi.fn().mockResolvedValue(buildBatchStatus()),
        assembleBatch: vi.fn(),
        recordDisposition: vi.fn(),
      };

      render(<GapWorkspace api={api} caseId="case-1" gapApi={gapApi} />);

      expect(
        await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025"),
      ).toBeVisible();
      expect(gapApi.getBatch).toHaveBeenCalledTimes(1);
      // Never auto-mutate on render.
      expect(gapApi.assembleBatch).not.toHaveBeenCalled();
    });

    it("shows the assemble empty state on 404 and only assembles on the explicit action", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(buildCase()),
        completeIntake: vi.fn(),
      };
      const gapApi = {
        getBatch: vi
          .fn()
          .mockRejectedValueOnce(
            new ApiClientError(404, "GAP_REQUEST_BATCH_NOT_AVAILABLE", "", false),
          )
          .mockResolvedValueOnce(buildBatchStatus()),
        assembleBatch: vi.fn().mockResolvedValue(buildBatchStatus().batch),
        recordDisposition: vi.fn(),
      };

      render(<GapWorkspace api={api} caseId="case-1" gapApi={gapApi} />);

      const assembleButton = await screen.findByRole("button", {
        name: "Tạo/tải danh sách yêu cầu bổ sung",
      });
      expect(
        screen.getByText("Chưa có danh sách yêu cầu bổ sung cho phiên bản hồ sơ này."),
      ).toBeVisible();
      expect(gapApi.assembleBatch).not.toHaveBeenCalled();

      fireEvent.click(assembleButton);

      await waitFor(() => expect(gapApi.assembleBatch).toHaveBeenCalledTimes(1));
      expect(
        await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025"),
      ).toBeVisible();
    });

    it("shows the stale banner and a reassemble button, hiding the disposition form while stale", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(buildCase()),
        completeIntake: vi.fn(),
      };
      const gapApi = {
        getBatch: vi
          .fn()
          .mockResolvedValueOnce(buildBatchStatus({ stale: true, currentOpenGapHash: "b".repeat(64) }))
          .mockResolvedValueOnce(buildBatchStatus()),
        assembleBatch: vi.fn().mockResolvedValue(buildBatchStatus().batch),
        recordDisposition: vi.fn(),
      };

      render(<GapWorkspace api={api} caseId="case-1" gapApi={gapApi} />);

      expect(
        await screen.findByText("Danh sách đã cũ so với khoảng trống hiện tại."),
      ).toBeVisible();
      // The disposition submit is not offered on a stale batch.
      expect(
        screen.queryByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      ).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Tạo lại danh sách" }));
      await waitFor(() => expect(gapApi.assembleBatch).toHaveBeenCalledTimes(1));
    });
  });

  describe("GapWorkspace — batch disposition form", () => {
    function renderWithBatch(status: GapRequestBatchStatus) {
      const api = {
        getCase: vi.fn().mockResolvedValue(buildCase()),
        completeIntake: vi.fn(),
      };
      const gapApi = {
        getBatch: vi.fn().mockResolvedValue(status),
        assembleBatch: vi.fn(),
        recordDisposition: vi.fn().mockResolvedValue({
          disposition: {
            id: "disp-1",
            batchId: status.batch.batchId,
            dispositionType: "APPROVED_ALL",
            itemDispositions: {},
            editedTexts: {},
            actorId: "officer-1",
            actorRole: "INTAKE_OFFICER",
            rationale: "ok",
            createdAt: "2026-07-18T09:00:00Z",
          },
          stale: false,
          gateStatus: "SATISFIED",
        }),
      };
      render(<GapWorkspace api={api} caseId="case-1" gapApi={gapApi} />);
      return { api, gapApi };
    }

    it("never preselects a disposition type and blocks submit without one", async () => {
      const { gapApi } = renderWithBatch(buildBatchStatus());

      await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025");

      const radios = screen.getAllByRole("radio") as HTMLInputElement[];
      expect(radios.length).toBeGreaterThan(0);
      for (const radio of radios) expect(radio.checked).toBe(false);

      await userEvent.type(
        screen.getByLabelText(/Lý do quyết định/),
        "Đã rà soát toàn bộ yêu cầu.",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );

      expect(screen.getByText("Chọn một loại quyết định trước khi ghi.")).toBeVisible();
      expect(gapApi.recordDisposition).not.toHaveBeenCalled();
    });

    it("requires a rationale even after a type is chosen", async () => {
      const { gapApi } = renderWithBatch(buildBatchStatus());

      await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025");
      await userEvent.click(screen.getByLabelText("Duyệt toàn bộ yêu cầu"));
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );

      expect(
        screen.getByText("Nhập lý do cho quyết định; đây là trường bắt buộc."),
      ).toBeVisible();
      expect(gapApi.recordDisposition).not.toHaveBeenCalled();
    });

    it("records APPROVED_ALL with rationale and then refetches", async () => {
      const { gapApi } = renderWithBatch(buildBatchStatus());

      await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025");
      await userEvent.click(screen.getByLabelText("Duyệt toàn bộ yêu cầu"));
      await userEvent.type(
        screen.getByLabelText(/Lý do quyết định/),
        "Tất cả yêu cầu hợp lệ, phê duyệt gửi bổ sung.",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );

      await waitFor(() =>
        expect(gapApi.recordDisposition).toHaveBeenCalledWith("case-1", "batch-1", {
          dispositionType: "APPROVED_ALL",
          rationale: "Tất cả yêu cầu hợp lệ, phê duyệt gửi bổ sung.",
        }),
      );
      await waitFor(() => expect(gapApi.getBatch).toHaveBeenCalledTimes(2));
    });

    it("offers NO_OUTBOUND_REQUESTS only when the batch has zero drafted items", async () => {
      const { gapApi } = renderWithBatch(
        buildBatchStatus({
          batch: {
            batchId: "batch-empty",
            caseId: "case-1",
            caseVersion: 5,
            openGapSnapshotHash: HASH,
            items: [],
          },
        }),
      );

      await screen.findByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" });
      // Empty batch: NO_OUTBOUND_REQUESTS is offered, APPROVED_ALL is not.
      expect(screen.getByLabelText("Không phát sinh yêu cầu gửi ra")).toBeVisible();
      expect(screen.queryByLabelText("Duyệt toàn bộ yêu cầu")).not.toBeInTheDocument();

      await userEvent.click(screen.getByLabelText("Không phát sinh yêu cầu gửi ra"));
      await userEvent.type(
        screen.getByLabelText(/Lý do quyết định/),
        "Không còn khoảng trống, không cần gửi yêu cầu.",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );

      await waitFor(() =>
        expect(gapApi.recordDisposition).toHaveBeenCalledWith("case-1", "batch-empty", {
          dispositionType: "NO_OUTBOUND_REQUESTS",
          rationale: "Không còn khoảng trống, không cần gửi yêu cầu.",
        }),
      );
    });

    it("does not offer NO_OUTBOUND_REQUESTS when the batch has drafted items", async () => {
      renderWithBatch(buildBatchStatus());
      await screen.findByLabelText("Duyệt toàn bộ yêu cầu");
      expect(screen.queryByLabelText("Không phát sinh yêu cầu gửi ra")).not.toBeInTheDocument();
    });

    it("requires a per-item choice for APPROVED_WITH_CHANGES, then records the map", async () => {
      const { gapApi } = renderWithBatch(buildBatchStatus());

      await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025");
      await userEvent.click(screen.getByLabelText("Duyệt kèm chỉnh sửa từng mục"));
      await userEvent.type(
        screen.getByLabelText(/Lý do quyết định/),
        "Bỏ một yêu cầu không còn cần thiết.",
      );
      // Submitting before choosing per-item is blocked.
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );
      expect(
        screen.getByText("Chọn cách xử lý cho từng mục yêu cầu bổ sung."),
      ).toBeVisible();
      expect(gapApi.recordDisposition).not.toHaveBeenCalled();

      await userEvent.click(screen.getByLabelText("Loại bỏ"));
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );

      await waitFor(() =>
        expect(gapApi.recordDisposition).toHaveBeenCalledWith("case-1", "batch-1", {
          dispositionType: "APPROVED_WITH_CHANGES",
          rationale: "Bỏ một yêu cầu không còn cần thiết.",
          itemDispositions: { [ITEM_A]: "REMOVED" },
          editedTexts: {},
        }),
      );
    });

    it("requires replacement text for an EDITED item and records it", async () => {
      const { gapApi } = renderWithBatch(buildBatchStatus());

      await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025");
      await userEvent.click(screen.getByLabelText("Duyệt kèm chỉnh sửa từng mục"));
      await userEvent.type(
        screen.getByLabelText(/Lý do quyết định/),
        "Chỉnh lại nội dung yêu cầu cho rõ.",
      );
      await userEvent.click(screen.getByLabelText("Chỉnh sửa nội dung"));

      // EDITED requires replacement text.
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );
      expect(
        screen.getByText("Nhập nội dung chỉnh sửa cho mỗi mục được đánh dấu chỉnh sửa."),
      ).toBeVisible();
      expect(gapApi.recordDisposition).not.toHaveBeenCalled();

      await userEvent.type(
        screen.getByLabelText(/Nội dung chỉnh sửa/),
        "Bổ sung báo cáo tài chính năm 2025 kèm thuyết minh.",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );

      await waitFor(() =>
        expect(gapApi.recordDisposition).toHaveBeenCalledWith("case-1", "batch-1", {
          dispositionType: "APPROVED_WITH_CHANGES",
          rationale: "Chỉnh lại nội dung yêu cầu cho rõ.",
          itemDispositions: { [ITEM_A]: "EDITED" },
          editedTexts: { [ITEM_A]: "Bổ sung báo cáo tài chính năm 2025 kèm thuyết minh." },
        }),
      );
    });

    it("keeps the draft and prompts a reload on a 409", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(buildCase()),
        completeIntake: vi.fn(),
      };
      const gapApi = {
        getBatch: vi.fn().mockResolvedValue(buildBatchStatus()),
        assembleBatch: vi.fn(),
        recordDisposition: vi
          .fn()
          .mockRejectedValue(new ApiClientError(409, "STALE_BATCH", "", false)),
      };
      render(<GapWorkspace api={api} caseId="case-1" gapApi={gapApi} />);

      await screen.findByText("Bổ sung báo cáo tài chính đã kiểm toán năm 2025");
      await userEvent.click(screen.getByLabelText("Duyệt toàn bộ yêu cầu"));
      await userEvent.type(
        screen.getByLabelText(/Lý do quyết định/),
        "Phê duyệt toàn bộ yêu cầu.",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Duyệt nội dung yêu cầu bổ sung" }),
      );

      // The reload prompt appears and the draft rationale is preserved.
      expect(
        await screen.findByRole("button", { name: "Tải lại danh sách" }),
      ).toBeVisible();
      expect(
        screen.getByText("Dữ liệu đã thay đổi. Vui lòng tải lại để xem bản mới nhất."),
      ).toBeVisible();
      expect((screen.getByLabelText(/Lý do quyết định/) as HTMLTextAreaElement).value).toBe(
        "Phê duyệt toàn bộ yêu cầu.",
      );

      fireEvent.click(screen.getByRole("button", { name: "Tải lại danh sách" }));
      await waitFor(() => expect(gapApi.getBatch).toHaveBeenCalledTimes(2));
    });

    it("shows the completion trigger when the officer may complete intake", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(buildCase(true)),
        completeIntake: vi.fn(),
      };
      const gapApi = {
        getBatch: vi.fn().mockResolvedValue(buildBatchStatus()),
        assembleBatch: vi.fn(),
        recordDisposition: vi.fn(),
      };
      render(<GapWorkspace api={api} caseId="case-1" gapApi={gapApi} />);

      expect(
        await screen.findByRole("button", { name: "Hoàn tất tiếp nhận…" }),
      ).toBeVisible();
    });
  });

  describe("IntakeCompletionDialog", () => {
    const baseProps = {
      onClose: vi.fn(),
      onComplete: vi.fn(),
      caseId: "case-1",
      openGapCount: 0,
      caseVersion: 3,
      canCompleteIntake: true,
    };

    it("is not in the document when closed", () => {
      render(<IntakeCompletionDialog {...baseProps} open={false} />);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("gates confirm on the checkbox, then calls onComplete once and shows the handoff", async () => {
      const user = userEvent.setup();
      const result: IntakeCompletionResultDto = {
        handoffId: "handoff-77",
        caseVersion: 3,
        state: "READY_FOR_SPECIALIST_REVIEW",
        created: true,
      };
      const onComplete = vi.fn().mockResolvedValue(result);

      render(<IntakeCompletionDialog {...baseProps} onComplete={onComplete} open />);

      expect(
        screen.getByRole("heading", { name: "Hoàn tất bộ hồ sơ tiếp nhận", level: 2 }),
      ).toBeVisible();
      expect(screen.getByText(/Đây không phải quyết định tín dụng\./)).toBeVisible();

      const confirmButton = screen.getByRole("button", { name: "Hoàn tất tiếp nhận" });
      expect(confirmButton).toBeDisabled();

      await user.click(
        screen.getByLabelText(
          "Tôi xác nhận đã rà soát toàn bộ tài liệu và khoảng trống chứng cứ.",
        ),
      );
      expect(confirmButton).toBeEnabled();

      await user.click(confirmButton);

      expect(onComplete).toHaveBeenCalledTimes(1);
      expect(await screen.findByText(/handoff-77/)).toBeVisible();
      expect(screen.getByText(/Sẵn sàng cho chuyên viên thẩm định/)).toBeVisible();
      const link = screen.getByRole("link", { name: "Mở bàn giao" });
      expect(link).toHaveAttribute("href", "/ho-so/case-1/ban-giao");
    });

    it("renders the unresolved reasons on a 409 INTAKE_INCOMPLETE", async () => {
      const user = userEvent.setup();
      const onComplete = vi.fn().mockRejectedValue(
        new ApiClientError(409, "INTAKE_INCOMPLETE", "", false, null, {
          reasons: [
            "Còn 2 dữ kiện chưa được xử lý",
            "Còn 1 mâu thuẫn chưa giải quyết",
          ],
          unresolvedCount: 3,
        }),
      );

      render(<IntakeCompletionDialog {...baseProps} onComplete={onComplete} open />);

      await user.click(
        screen.getByLabelText(
          "Tôi xác nhận đã rà soát toàn bộ tài liệu và khoảng trống chứng cứ.",
        ),
      );
      await user.click(screen.getByRole("button", { name: "Hoàn tất tiếp nhận" }));

      expect(
        await screen.findByText("Hồ sơ tiếp nhận chưa hoàn tất; các mục chưa xử lý:"),
      ).toBeVisible();
      expect(screen.getByText("Còn 2 dữ kiện chưa được xử lý")).toBeVisible();
      expect(screen.getByText("Còn 1 mâu thuẫn chưa giải quyết")).toBeVisible();
      // No optimistic completion: the confirm button is still present, no handoff.
      expect(screen.queryByRole("link", { name: "Mở bàn giao" })).not.toBeInTheDocument();
    });

    it("shows the open-gap warning panel when openGapCount > 0", () => {
      render(<IntakeCompletionDialog {...baseProps} open openGapCount={4} />);
      expect(
        screen.getByText("Còn 4 khoảng trống chứng cứ chưa giải quyết."),
      ).toBeVisible();
    });

    it("hides confirm and shows the permission note when canCompleteIntake is false", () => {
      render(<IntakeCompletionDialog {...baseProps} canCompleteIntake={false} open />);
      expect(
        screen.queryByRole("button", { name: "Hoàn tất tiếp nhận" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText("Bạn không có quyền hoàn tất tiếp nhận hồ sơ này."),
      ).toBeVisible();
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
      await user.click(checkbox);
      const confirm = screen.getByRole("button", { name: "Hoàn tất tiếp nhận" });

      confirm.focus();
      expect(confirm).toHaveFocus();
      await user.tab();
      expect(outside).not.toHaveFocus();

      await user.tab({ shift: true });
      expect(outside).not.toHaveFocus();
    });
  });
});

describe("Handoff and audit", () => {
  function readyHandoff(overrides: Partial<HandoffView> = {}): HandoffView {
    return {
      handoffId: "handoff-1",
      state: "READY_FOR_SPECIALIST_REVIEW",
      caseVersion: 3,
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
      eventData: {},
      createdAt: "2026-07-17T08:00:00Z",
      ...overrides,
    };
  }

  function auditEvent(overrides: Partial<AuditEventDto> = {}): AuditEventDto {
    return { ...makeEvent(), ...overrides };
  }

  describe("HandoffSummary", () => {
    it("labels the handoff as not a credit decision", () => {
      render(<HandoffSummary handoff={readyHandoff()} />);
      expect(screen.getByText("Không phải quyết định tín dụng")).toBeVisible();
    });

    it("shows the version line, state label, and handoff id", () => {
      render(<HandoffSummary handoff={readyHandoff({ caseVersion: 7, handoffId: "handoff-77" })} />);

      expect(screen.getByText("Phiên bản hồ sơ: 7")).toBeVisible();
      expect(screen.getByText("Sẵn sàng cho chuyên viên thẩm định")).toBeVisible();
      expect(screen.getByText(/handoff-77/)).toBeVisible();
    });

    it("fails closed on an unknown handoff state", () => {
      render(<HandoffSummary handoff={readyHandoff({ state: "SOMETHING_NEW" })} />);
      expect(screen.getByText("Trạng thái chưa được hỗ trợ")).toBeVisible();
    });
  });

  describe("HandoffWorkspace (ban-giao loader)", () => {
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

    it("loads the case and renders the current handoff", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(creditCase),
        getHandoff: vi.fn().mockResolvedValue({
          handoffId: "handoff-9",
          state: "READY_FOR_SPECIALIST_REVIEW",
          caseVersion: 5,
          createdAt: "2026-07-17T08:00:00Z",
        }),
      };

      render(<HandoffWorkspace api={api} caseId="case-1" />);

      expect(
        await screen.findByRole("heading", { name: "Gói bàn giao chuyên viên" }),
      ).toBeVisible();
      expect(screen.getByText(/handoff-9/)).toBeVisible();
      expect(screen.getByText("Hồ sơ · phiên bản 5")).toBeVisible();
    });

    it("shows the honest empty state on a 404 HANDOFF_NOT_AVAILABLE", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(creditCase),
        getHandoff: vi
          .fn()
          .mockRejectedValue(new ApiClientError(404, "HANDOFF_NOT_AVAILABLE", "", false)),
      };

      render(<HandoffWorkspace api={api} caseId="case-1" />);

      expect(
        await screen.findByText(/Chưa có gói bàn giao cho hồ sơ này\./),
      ).toBeVisible();
      expect(screen.getByText("Không phải quyết định tín dụng")).toBeVisible();
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

    it("renders eventData as escaped plain text, never as HTML", () => {
      render(
        <AuditTimeline
          events={[makeEvent({ eventData: { note: "<b>tiêm</b>" } })]}
          nextCursor={null}
        />,
      );

      const item = screen.getByRole("listitem");
      expect(item).toHaveTextContent("Chi tiết: note: <b>tiêm</b>");
      // The angle-bracket content is text, not a real <b> element.
      expect(item.querySelector("b")).toBeNull();
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

  describe("AuditWorkspace (nhat-ky loader)", () => {
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

    it("loads the first page and appends the next page without duplicating events", async () => {
      const api = {
        getCase: vi.fn().mockResolvedValue(creditCase),
        listAuditEvents: vi
          .fn()
          .mockResolvedValueOnce({
            events: [
              auditEvent({ id: "evt-1", eventType: "CASE_CREATED" }),
              auditEvent({ id: "evt-2", eventType: "DOCUMENT_REGISTERED" }),
            ],
            nextCursor: "cursor-2",
          })
          .mockResolvedValueOnce({
            events: [
              // The window overlaps: evt-2 reappears and must not be duplicated.
              auditEvent({ id: "evt-2", eventType: "DOCUMENT_REGISTERED" }),
              auditEvent({ id: "evt-3", eventType: "DOCUMENT_CONFIRMED" }),
            ],
            nextCursor: null,
          }),
      };

      render(<AuditWorkspace api={api} caseId="case-1" />);

      expect(await screen.findByText("CASE_CREATED")).toBeVisible();
      // Scope to the timeline list — CaseNav renders its own nav listitems.
      const timeline = screen.getByRole("list", { name: "Nhật ký hồ sơ" });
      expect(within(timeline).getAllByRole("listitem")).toHaveLength(2);

      fireEvent.click(screen.getByRole("button", { name: "Tải thêm sự kiện" }));

      await waitFor(() =>
        expect(within(timeline).getAllByRole("listitem")).toHaveLength(3),
      );
      expect(screen.getByText("DOCUMENT_CONFIRMED")).toBeVisible();
      // De-duplicated: exactly one DOCUMENT_REGISTERED entry despite the overlap.
      expect(screen.getAllByText("DOCUMENT_REGISTERED")).toHaveLength(1);
      // Last page reached — the load-more control is gone.
      expect(
        screen.queryByRole("button", { name: "Tải thêm sự kiện" }),
      ).not.toBeInTheDocument();
      expect(api.listAuditEvents).toHaveBeenCalledTimes(2);
      expect(api.listAuditEvents).toHaveBeenNthCalledWith(2, "case-1", "cursor-2", 50);
    });
  });
});
