// Self-contained BFF bindings for the orchestration control room (Quy trình).
//
// This module intentionally does NOT touch the shared client (client.ts /
// contracts.ts / schemas.ts). It mirrors their style — BFF base
// "/api/creditops", CSRF cookie "__Host-creditops-csrf" -> header
// "x-creditops-csrf" on mutations, ApiClientError-style typed failures — and
// reuses ApiClientError + getVietnameseApiError from the shared client so error
// copy stays consistent across the workspace.
//
// Upstream mapping (confirmed in app/api/creditops/[...path]/route.ts, which
// forwards every segment verbatim to the FastAPI service):
//   GET  /api/creditops/api/v1/cases/{caseId}/orchestration          -> OrchestrationStatusResponse
//   POST /api/creditops/api/v1/cases/{caseId}/orchestration/advance  -> 202 AdvanceAcceptedResponse
//   GET  /api/creditops/api/v1/tasks/{taskId}                        -> TaskResponse
//
// The response types below mirror services/api/src/creditops/api/orchestration.py
// and tasks.py faithfully (serialization aliases -> camelCase field names).

import { ApiClientError, getVietnameseApiError } from "./client";

export { ApiClientError, getVietnameseApiError };

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// ---------------------------------------------------------------------------
// Wire types (camelCase, exactly as serialized by the FastAPI response models).
// Enum-ish fields are kept as `string` so a new backend value renders as a
// neutral chip instead of crashing the screen; the render layer maps known
// values to Vietnamese labels and tones.
// ---------------------------------------------------------------------------

export interface OrchestrationPlanStepDto {
  taskType: string;
  priority: number;
}

export interface OrchestrationReadinessDto {
  taskType: string;
  readiness: string;
  reason: string;
}

export interface OrchestrationTaskDto {
  taskId: string;
  taskType: string;
  caseVersion: number;
  status: string;
}

export interface OrchestrationGateDto {
  gateType: string;
  status: string;
  dispositionRef: string | null;
  satisfiedAt: string | null;
}

export interface OrchestrationDeadlockDto {
  reasons: string[];
}

export interface OrchestrationStatusDto {
  caseId: string;
  caseVersion: number;
  hasIntakeHandoff: boolean;
  planSource: string;
  plan: OrchestrationPlanStepDto[];
  readiness: OrchestrationReadinessDto[];
  tasks: OrchestrationTaskDto[];
  gates: OrchestrationGateDto[];
  supersededTaskIds: string[];
  deadlock: OrchestrationDeadlockDto | null;
}

export interface AdvanceAcceptedDto {
  taskId: string;
  caseVersion: number;
  status: string;
  created: boolean;
}

export interface TaskCheckpointDto {
  sequenceNo: number;
  checkpointType: string;
  checkpointSchemaVersion: string;
  createdAt: string;
}

export interface TaskDetailDto {
  id: string;
  caseId: string;
  caseVersion: number;
  taskType: string;
  documentVersionId: string | null;
  status: string;
  attemptCount: number;
  maxAttempts: number;
  availableAt: string;
  checkpoint: TaskCheckpointDto | null;
}

// ---------------------------------------------------------------------------
// Defensive parsers (mirror the throw-Vietnamese style of lib/api/schemas.ts).
// ---------------------------------------------------------------------------

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`Phản hồi ${label} không đúng định dạng.`);
  }
  return value as Record<string, unknown>;
}

function str(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Phản hồi thiếu ${label}.`);
  }
  return value;
}

function nullableStr(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function integer(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error(`Phản hồi thiếu ${label} hợp lệ.`);
  }
  return value;
}

function bool(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`Phản hồi thiếu ${label} hợp lệ.`);
  }
  return value;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`Phản hồi ${label} không đúng định dạng.`);
  }
  return value;
}

function stringArray(value: unknown, label: string): string[] {
  return array(value, label).map((entry) => str(entry, label));
}

export function parseOrchestrationStatus(value: unknown): OrchestrationStatusDto {
  const root = record(value, "trạng thái điều phối");
  const deadlockRaw = root.deadlock;
  return {
    caseId: str(root.caseId, "mã hồ sơ"),
    caseVersion: integer(root.caseVersion, "phiên bản hồ sơ"),
    hasIntakeHandoff: bool(root.hasIntakeHandoff, "trạng thái bàn giao tiếp nhận"),
    planSource: str(root.planSource, "nguồn kế hoạch"),
    plan: array(root.plan, "kế hoạch").map((entry) => {
      const step = record(entry, "bước kế hoạch");
      return {
        taskType: str(step.taskType, "loại tác vụ"),
        priority: integer(step.priority, "thứ tự ưu tiên"),
      };
    }),
    readiness: array(root.readiness, "điều kiện").map((entry) => {
      const assessment = record(entry, "đánh giá điều kiện");
      return {
        taskType: str(assessment.taskType, "loại tác vụ"),
        readiness: str(assessment.readiness, "mức độ sẵn sàng"),
        reason: str(assessment.reason, "lý do"),
      };
    }),
    tasks: array(root.tasks, "tác vụ").map((entry) => {
      const task = record(entry, "tác vụ");
      return {
        taskId: str(task.taskId, "mã tác vụ"),
        taskType: str(task.taskType, "loại tác vụ"),
        caseVersion: integer(task.caseVersion, "phiên bản hồ sơ"),
        status: str(task.status, "trạng thái tác vụ"),
      };
    }),
    gates: array(root.gates, "cổng phê duyệt").map((entry) => {
      const gate = record(entry, "cổng phê duyệt");
      return {
        gateType: str(gate.gateType, "loại cổng"),
        status: str(gate.status, "trạng thái cổng"),
        dispositionRef: nullableStr(gate.dispositionRef),
        satisfiedAt: nullableStr(gate.satisfiedAt),
      };
    }),
    supersededTaskIds: stringArray(root.supersededTaskIds, "danh sách tác vụ đã thay thế"),
    deadlock:
      deadlockRaw === null || deadlockRaw === undefined
        ? null
        : { reasons: stringArray(record(deadlockRaw, "bế tắc").reasons, "lý do bế tắc") },
  };
}

export function parseAdvanceAccepted(value: unknown): AdvanceAcceptedDto {
  const root = record(value, "kết quả tiến hành");
  return {
    taskId: str(root.taskId, "mã tác vụ"),
    caseVersion: integer(root.caseVersion, "phiên bản hồ sơ"),
    status: str(root.status, "trạng thái"),
    created: bool(root.created, "trạng thái tạo mới"),
  };
}

export function parseTaskDetail(value: unknown): TaskDetailDto {
  const root = record(value, "tác vụ");
  const checkpointRaw = root.checkpoint;
  return {
    id: str(root.id, "mã tác vụ"),
    caseId: str(root.caseId, "mã hồ sơ"),
    caseVersion: integer(root.caseVersion, "phiên bản hồ sơ"),
    taskType: str(root.taskType, "loại tác vụ"),
    documentVersionId: nullableStr(root.documentVersionId),
    status: str(root.status, "trạng thái tác vụ"),
    attemptCount: integer(root.attemptCount, "số lần thử"),
    maxAttempts: integer(root.maxAttempts, "số lần thử tối đa"),
    availableAt: str(root.availableAt, "thời điểm sẵn sàng"),
    checkpoint:
      checkpointRaw === null || checkpointRaw === undefined
        ? null
        : (() => {
            const checkpoint = record(checkpointRaw, "điểm lưu");
            return {
              sequenceNo: integer(checkpoint.sequenceNo, "số thứ tự điểm lưu"),
              checkpointType: str(checkpoint.checkpointType, "loại điểm lưu"),
              checkpointSchemaVersion: str(
                checkpoint.checkpointSchemaVersion,
                "phiên bản lược đồ điểm lưu",
              ),
              createdAt: str(checkpoint.createdAt, "thời điểm tạo điểm lưu"),
            };
          })(),
  };
}

// ---------------------------------------------------------------------------
// Fetch client (mirrors CreditOpsApiClient.request; injectable for tests).
// ---------------------------------------------------------------------------

type Fetcher = typeof fetch;
type CsrfTokenProvider = () => string | null;

export interface OrchestrationApi {
  getOrchestration(caseId: string): Promise<OrchestrationStatusDto>;
  advanceOrchestration(caseId: string): Promise<AdvanceAcceptedDto>;
  getTask(taskId: string): Promise<TaskDetailDto>;
}

export class OrchestrationApiClient implements OrchestrationApi {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getOrchestration(caseId: string): Promise<OrchestrationStatusDto> {
    return parseOrchestrationStatus(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/orchestration`,
      ),
    );
  }

  async advanceOrchestration(caseId: string): Promise<AdvanceAcceptedDto> {
    // The advance endpoint takes no payload; an empty JSON object satisfies the
    // BFF's JSON-body requirement for mutations (mirrors upload-completion).
    return parseAdvanceAccepted(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/orchestration/advance`,
        { method: "POST", body: "{}" },
      ),
    );
  }

  async getTask(taskId: string): Promise<TaskDetailDto> {
    return parseTaskDetail(
      await this.request(`/api/v1/tasks/${encodeURIComponent(taskId)}`),
    );
  }

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    if (isMutation(init.method)) {
      const csrfToken = this.csrfTokenProvider();
      if (csrfToken) headers.set(CSRF_HEADER_NAME, csrfToken);
    }

    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      ...init,
      headers,
      credentials: "include",
      cache: "no-store",
    });
    const body = await parseJson(response);
    if (!response.ok) {
      const apiError = parseApiError(body);
      throw new ApiClientError(
        response.status,
        apiError?.code ?? "REQUEST_FAILED",
        apiError?.messageVi || "Yêu cầu không thành công.",
        apiError?.retryable ?? response.status >= 500,
      );
    }
    return body;
  }
}

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

function readBrowserCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const index = part.indexOf("=");
    if (index < 0 || part.slice(0, index).trim() !== CSRF_COOKIE_NAME) continue;
    try {
      return decodeURIComponent(part.slice(index + 1).trim());
    } catch {
      return null;
    }
  }
  return null;
}

async function parseJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

interface ParsedApiError {
  code: string;
  messageVi: string;
  retryable: boolean;
}

function parseApiError(value: unknown): ParsedApiError | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const code = typeof raw.code === "string" ? raw.code : null;
  if (code === null) return null;
  return {
    code,
    messageVi: typeof raw.messageVi === "string" ? raw.messageVi : "",
    retryable: typeof raw.retryable === "boolean" ? raw.retryable : false,
  };
}

export const orchestrationApi = new OrchestrationApiClient();
