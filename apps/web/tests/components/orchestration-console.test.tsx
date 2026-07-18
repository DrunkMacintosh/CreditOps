import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { OrchestrationConsole } from "../../components/orchestration/orchestration-console";
import {
  gateTypeLabel,
  readinessDescriptor,
  taskStatusDescriptor,
  translateReason,
} from "../../components/orchestration/labels";
import {
  ApiClientError,
  parseOrchestrationStatus,
  type OrchestrationApi,
  type OrchestrationStatusDto,
  type TaskDetailDto,
} from "../../lib/api/orchestration";

function buildStatus(overrides: Partial<OrchestrationStatusDto> = {}): OrchestrationStatusDto {
  return {
    caseId: "case-1",
    caseVersion: 3,
    hasIntakeHandoff: true,
    planSource: "DEFAULT",
    plan: [{ taskType: "CREDIT_UNDERWRITING", priority: 0 }],
    readiness: [
      { taskType: "CREDIT_UNDERWRITING", readiness: "READY", reason: "dependencies met" },
      {
        taskType: "LEGAL_COMPLIANCE_COLLATERAL",
        readiness: "COMPLETE",
        reason: "task succeeded",
      },
      {
        taskType: "INDEPENDENT_RISK_REVIEW",
        readiness: "BLOCKED",
        reason: "human gate G2_GAP_REQUEST_APPROVAL is not satisfied",
      },
      {
        taskType: "CREDIT_OPERATIONS",
        readiness: "BLOCKED",
        reason: "waiting for predecessor: INDEPENDENT_RISK_REVIEW",
      },
    ],
    tasks: [
      {
        taskId: "task-legal",
        taskType: "LEGAL_COMPLIANCE_COLLATERAL",
        caseVersion: 3,
        status: "SUCCEEDED",
      },
    ],
    gates: [
      {
        gateType: "G1_INTAKE_COMPLETE",
        status: "SATISFIED",
        dispositionRef: "intake-handoff",
        satisfiedAt: "2026-07-18T02:00:00Z",
      },
      { gateType: "G2_GAP_REQUEST_APPROVAL", status: "OPEN", dispositionRef: null, satisfiedAt: null },
      { gateType: "G3_RISK_DISPOSITION", status: "OPEN", dispositionRef: null, satisfiedAt: null },
      { gateType: "G4_OPS_AUTHORIZATION", status: "OPEN", dispositionRef: null, satisfiedAt: null },
    ],
    supersededTaskIds: [],
    deadlock: null,
    ...overrides,
  };
}

function fakeApi(overrides: Partial<OrchestrationApi> = {}): OrchestrationApi {
  return {
    getOrchestration: vi.fn(async () => buildStatus()),
    advanceOrchestration: vi.fn(async () => ({
      taskId: "task-plan",
      caseVersion: 3,
      status: "PENDING",
      created: true,
    })),
    // Best-effort enrichment; refuse so the console renders on status alone.
    getTask: vi.fn(async (): Promise<TaskDetailDto> => {
      throw new ApiClientError(403, "INSUFFICIENT_ROLE", "Không có quyền.", false);
    }),
    ...overrides,
  };
}

describe("orchestration labels", () => {
  it("translates the engine readiness reasons into plain Vietnamese", () => {
    expect(translateReason("task succeeded")).toBe("Tác vụ đã hoàn tất.");
    expect(translateReason("human gate G2_GAP_REQUEST_APPROVAL is not satisfied")).toBe(
      "Chưa đạt cổng g2 · duyệt yêu cầu bổ sung.",
    );
    expect(
      translateReason("waiting for predecessor: CREDIT_UNDERWRITING, INDEPENDENT_RISK_REVIEW"),
    ).toBe("Đang chờ bước trước: Thẩm định tín dụng, Rà soát rủi ro độc lập.");
  });

  it("keeps an unknown reason verbatim rather than hiding it", () => {
    expect(translateReason("some future reason")).toBe("some future reason");
  });

  it("labels gates and maps statuses to the gate language tones", () => {
    expect(gateTypeLabel("G3_RISK_DISPOSITION")).toBe("Cổng G3 · Kết luận rủi ro");
    expect(readinessDescriptor("BLOCKED")).toEqual({
      label: "Chưa đủ điều kiện",
      tone: "risk",
    });
    expect(taskStatusDescriptor("RUNNING")).toEqual({ label: "Đang xử lý", tone: "info" });
  });
});

describe("parseOrchestrationStatus", () => {
  it("parses a well-formed status payload", () => {
    const parsed = parseOrchestrationStatus(buildStatus());
    expect(parsed.caseVersion).toBe(3);
    expect(parsed.plan).toHaveLength(1);
    expect(parsed.gates).toHaveLength(4);
  });

  it("throws a Vietnamese error on a malformed payload", () => {
    expect(() => parseOrchestrationStatus({ caseId: "x" })).toThrow(/Phản hồi/);
  });
});

describe("OrchestrationConsole", () => {
  it("renders the stage rail, planner queue, and gate provenance", async () => {
    render(<OrchestrationConsole api={fakeApi()} caseId="case-1" />);

    expect(await screen.findByRole("heading", { name: "Quy trình xử lý" })).toBeInTheDocument();
    // Stage name also appears in the planner queue, so match >= 1.
    expect(screen.getAllByText("Thẩm định tín dụng").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Rà soát rủi ro độc lập" })).toBeInTheDocument();
    // The WHY line (evidence chain) is translated.
    expect(
      screen.getByText("Đang chờ bước trước: Rà soát rủi ro độc lập."),
    ).toBeInTheDocument();
    // Gate provenance evidence chip names the disposition reference (G1 blocks
    // two stages, so it renders on both).
    expect(screen.getAllByText("intake-handoff").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "Tiến hành bước tiếp theo" }),
    ).toBeInTheDocument();
  });

  it("invites the officer to start when the pipeline has no tasks", async () => {
    const api = fakeApi({
      getOrchestration: vi.fn(async () => buildStatus({ tasks: [], plan: [] })),
    });
    render(<OrchestrationConsole api={api} caseId="case-1" />);

    expect(
      await screen.findByRole("button", { name: "Khởi động quy trình" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Quy trình chưa khởi động/)).toBeInTheDocument();
  });

  it("surfaces the server's reason when advance conflicts (409)", async () => {
    const api = fakeApi({
      advanceOrchestration: vi.fn(async () => {
        throw new ApiClientError(
          409,
          "CASE_VERSION_CONFLICT",
          "Hồ sơ đã thay đổi phiên bản. Vui lòng tải lại.",
          false,
        );
      }),
    });
    render(<OrchestrationConsole api={api} caseId="case-1" />);

    const button = await screen.findByRole("button", { name: "Tiến hành bước tiếp theo" });
    await userEvent.click(button);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Hồ sơ đã thay đổi phiên bản. Vui lòng tải lại.");
  });

  it("shows the deadlock panel with the server reasons", async () => {
    const api = fakeApi({
      getOrchestration: vi.fn(async () =>
        buildStatus({
          deadlock: { reasons: ["human gate G2_GAP_REQUEST_APPROVAL is not satisfied"] },
        }),
      ),
    });
    render(<OrchestrationConsole api={api} caseId="case-1" />);

    expect(await screen.findByText("Quy trình đang tạm dừng")).toBeInTheDocument();
    expect(
      screen.getByText("human gate G2_GAP_REQUEST_APPROVAL is not satisfied"),
    ).toBeInTheDocument();
  });
});
