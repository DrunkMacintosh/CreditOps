// Pure presentation helpers for the orchestration control room: Vietnamese
// labels, chip tones, and the readiness-reason translator that turns the
// engine's English derivation strings into plain Vietnamese. Kept free of JSX
// so it can be unit-tested in isolation.

export type ChipTone = "ok" | "amber" | "info" | "risk" | "muted";

// --- Task types -------------------------------------------------------------

const TASK_TYPE_LABELS: Record<string, string> = {
  DOCUMENT_INGESTION: "Nạp tài liệu",
  ORCHESTRATOR_PLAN: "Lập kế hoạch điều phối",
  CREDIT_UNDERWRITING: "Thẩm định tín dụng",
  LEGAL_COMPLIANCE_COLLATERAL: "Pháp chế & tài sản bảo đảm",
  INDEPENDENT_RISK_REVIEW: "Rà soát rủi ro độc lập",
  CREDIT_OPERATIONS: "Vận hành tín dụng",
};

export function taskTypeLabel(taskType: string): string {
  return TASK_TYPE_LABELS[taskType] ?? taskType;
}

// --- Gate types -------------------------------------------------------------

const GATE_TYPE_LABELS: Record<string, string> = {
  G1_INTAKE_COMPLETE: "Hoàn tất tiếp nhận",
  G2_GAP_REQUEST_APPROVAL: "Duyệt yêu cầu bổ sung",
  G3_RISK_DISPOSITION: "Kết luận rủi ro",
  G4_OPS_AUTHORIZATION: "Uỷ quyền vận hành",
};

// Short gate code (e.g. "G2") for the mono reference chip.
export function gateCode(gateType: string): string {
  const match = /^G\d+/.exec(gateType);
  return match ? match[0] : gateType;
}

export function gateTypeLabel(gateType: string): string {
  const name = GATE_TYPE_LABELS[gateType];
  return name ? `Cổng ${gateCode(gateType)} · ${name}` : gateType;
}

// --- Readiness --------------------------------------------------------------

interface Descriptor {
  label: string;
  tone: ChipTone;
}

const READINESS: Record<string, Descriptor> = {
  BLOCKED: { label: "Chưa đủ điều kiện", tone: "risk" },
  READY: { label: "Sẵn sàng", tone: "info" },
  IN_PROGRESS: { label: "Đang xử lý", tone: "info" },
  COMPLETE: { label: "Đạt", tone: "ok" },
  SUPERSEDED: { label: "Đã thay thế", tone: "muted" },
  FAILED: { label: "Cần rà soát thủ công", tone: "risk" },
};

export function readinessDescriptor(readiness: string): Descriptor {
  return READINESS[readiness] ?? { label: readiness, tone: "muted" };
}

export function isReadinessInProgress(readiness: string): boolean {
  return readiness === "IN_PROGRESS";
}

// --- Gate status ------------------------------------------------------------

const GATE_STATUS: Record<string, Descriptor> = {
  OPEN: { label: "Đang chờ", tone: "amber" },
  SATISFIED: { label: "Đạt", tone: "ok" },
};

export function gateStatusDescriptor(status: string): Descriptor {
  return GATE_STATUS[status] ?? { label: status, tone: "muted" };
}

// --- Task status (processing) ----------------------------------------------

const TASK_STATUS: Record<string, Descriptor> = {
  PENDING: { label: "Đang chờ", tone: "amber" },
  RUNNING: { label: "Đang xử lý", tone: "info" },
  RETRY_WAIT: { label: "Chờ thử lại", tone: "amber" },
  SUCCEEDED: { label: "Đạt", tone: "ok" },
  FAILED_MANUAL_REVIEW: { label: "Cần rà soát thủ công", tone: "risk" },
  SUPERSEDED: { label: "Đã thay thế", tone: "muted" },
};

export function taskStatusDescriptor(status: string): Descriptor {
  return TASK_STATUS[status] ?? { label: status, tone: "muted" };
}

const IN_FLIGHT_TASK_STATUSES = new Set(["PENDING", "RUNNING", "RETRY_WAIT"]);

export function isTaskInFlight(status: string): boolean {
  return IN_FLIGHT_TASK_STATUSES.has(status);
}

// --- Plan source ------------------------------------------------------------

export function planSourceLabel(planSource: string): string {
  if (planSource === "DEFAULT") return "Thứ tự chuẩn theo đồ thị phụ thuộc";
  if (planSource === "LLM_PROPOSED") {
    return "Ưu tiên do trợ lý điều phối đề xuất · đã kiểm chứng";
  }
  return planSource;
}

// The provenance reference shown on the plan-source evidence chip.
export function planSourceRef(planSource: string): string {
  if (planSource === "DEFAULT") return "quy-tắc-điều-phối";
  if (planSource === "LLM_PROPOSED") return "đề-xuất-đã-kiểm-chứng";
  return planSource.toLowerCase();
}

// --- Readiness reason translator -------------------------------------------
//
// The engine reports readiness reasons as fixed English strings
// (services/api/src/creditops/application/orchestration/readiness.py). These
// reasons ARE the evidence chain for a stage — the WHY behind its state — so we
// translate them to plain Vietnamese, falling back to the raw text for any
// value we do not recognise (forward-compatible, never hides information).

export function translateReason(reason: string): string {
  const exact: Record<string, string> = {
    "task succeeded": "Tác vụ đã hoàn tất.",
    "task in progress": "Tác vụ đang chạy.",
    "task in manual review": "Tác vụ cần rà soát thủ công.",
    "blocking evidence gap on required inputs":
      "Có khoảng trống chứng cứ đang chặn ở dữ liệu đầu vào bắt buộc.",
    "dependencies met": "Đã đủ điều kiện phụ thuộc.",
    "stale task fenced; fresh work is ready":
      "Bản chạy cũ đã được khoá lại; sẵn sàng chạy bản mới.",
  };
  if (reason in exact) return exact[reason];

  const gateMatch = /^human gate (\S+) is not satisfied$/.exec(reason);
  if (gateMatch) {
    return `Chưa đạt ${gateTypeLabel(gateMatch[1]).toLowerCase()}.`;
  }

  const predecessorMatch = /^waiting for predecessor: (.+)$/.exec(reason);
  if (predecessorMatch) {
    const labels = predecessorMatch[1]
      .split(",")
      .map((entry) => taskTypeLabel(entry.trim()))
      .join(", ");
    return `Đang chờ bước trước: ${labels}.`;
  }

  return reason;
}
