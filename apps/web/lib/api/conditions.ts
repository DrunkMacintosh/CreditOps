// Self-contained API bindings for the stage-10 Disbursement Condition ledger
// screen ("Điều kiện giải ngân"). Mirrors lib/api/client.ts conventions but
// shares no mutable state with it.
//
// Backend truth mirrored here: services/api/src/creditops/api/conditions.py and
// services/.../domain/conditions.py.
//   GET  /api/v1/cases/{caseId}/conditions                        -> Conditions (list + confirmable)
//   POST /api/v1/cases/{caseId}/conditions                        -> 201 Condition (bound to permitting decision)
//   POST /api/v1/cases/{caseId}/conditions/{conditionId}/transition -> Condition (validated edge)
//   POST /api/v1/cases/{caseId}/conditions/confirm                -> Confirmation (HG_DISBURSEMENT_CONDITIONS_CONFIRMED)
//
// A human-only ledger: agents never write a status. Verification / waiver /
// not-applicable are independent-checker acts; a waiver or not-applicable ruling
// requires an authority rationale. Confirmation is fail-closed and never
// satisfied by an empty ledger.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/conditions.py) ---

export type GateStatus = "OPEN" | "SATISFIED";

export type ConditionStatus =
  | "PENDING"
  | "EVIDENCE_SUBMITTED"
  | "VERIFIED"
  | "FAILED"
  | "WAIVER_REQUESTED"
  | "WAIVED_BY_HUMAN"
  | "SUPERSEDED"
  | "NOT_APPLICABLE_BY_HUMAN";

// The exact 8-status closed set, in display order.
export const CONDITION_STATUSES: readonly ConditionStatus[] = [
  "PENDING",
  "EVIDENCE_SUBMITTED",
  "VERIFIED",
  "FAILED",
  "WAIVER_REQUESTED",
  "WAIVED_BY_HUMAN",
  "SUPERSEDED",
  "NOT_APPLICABLE_BY_HUMAN",
];

// The deterministic transition map (mirrors ALLOWED_TRANSITIONS). The UI derives
// each condition's target choices from its current status, so it never offers a
// forbidden edge; the server re-validates regardless.
export const ALLOWED_TRANSITIONS: Record<ConditionStatus, readonly ConditionStatus[]> = {
  PENDING: ["EVIDENCE_SUBMITTED", "WAIVER_REQUESTED", "NOT_APPLICABLE_BY_HUMAN", "SUPERSEDED"],
  EVIDENCE_SUBMITTED: ["VERIFIED", "FAILED", "SUPERSEDED"],
  FAILED: ["EVIDENCE_SUBMITTED", "WAIVER_REQUESTED", "SUPERSEDED"],
  WAIVER_REQUESTED: ["WAIVED_BY_HUMAN", "FAILED", "SUPERSEDED"],
  VERIFIED: ["SUPERSEDED"],
  WAIVED_BY_HUMAN: ["SUPERSEDED"],
  NOT_APPLICABLE_BY_HUMAN: ["SUPERSEDED"],
  SUPERSEDED: [],
};

// Targets reserved to the independent OPS checker authority (mirrors
// CHECKER_AUTHORITY_TARGETS): verification and the human waiver / not-applicable
// rulings. Every other move is an ordinary ops-officer workflow step.
export const CHECKER_AUTHORITY_TARGETS: readonly ConditionStatus[] = [
  "VERIFIED",
  "WAIVED_BY_HUMAN",
  "NOT_APPLICABLE_BY_HUMAN",
];

// Targets whose ruling IS the authority record and therefore REQUIRE a rationale
// (mirrors RATIONALE_REQUIRED_TARGETS).
export const RATIONALE_REQUIRED_TARGETS: readonly ConditionStatus[] = [
  "WAIVED_BY_HUMAN",
  "NOT_APPLICABLE_BY_HUMAN",
];

// The satisfied terminals a ledger may be confirmed on (mirrors
// CONFIRMABLE_STATUSES).
export const CONFIRMABLE_STATUSES: readonly ConditionStatus[] = [
  "VERIFIED",
  "WAIVED_BY_HUMAN",
  "NOT_APPLICABLE_BY_HUMAN",
];

// --- Response shapes ---

export interface DisbursementCondition {
  id: string;
  caseId: string;
  caseVersion: number;
  decisionId: string;
  conditionText: string;
  owner: string | null;
  dueDate: string | null;
  status: ConditionStatus | string;
  evidenceRefs: string[];
  createdAt: string;
}

export interface ConditionLedger {
  conditions: DisbursementCondition[];
  caseVersion: number;
  confirmable: boolean;
}

export interface ConditionConfirmation {
  gateType: string;
  status: GateStatus | string;
  caseVersion: number;
  dispositionRef: string;
}

export interface CreateConditionInput {
  conditionText: string;
  owner?: string;
  dueDate?: string;
}

export interface TransitionConditionInput {
  toStatus: ConditionStatus;
  rationale?: string;
  evidenceRefs?: string[];
}

// --- Defensive parsing ---

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function bool(value: unknown): boolean {
  return value === true;
}

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(str) : [];
}

function parseCondition(value: unknown): DisbursementCondition {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    decisionId: str(raw.decisionId),
    conditionText: str(raw.conditionText),
    owner: strOrNull(raw.owner),
    dueDate: strOrNull(raw.dueDate),
    status: str(raw.status),
    evidenceRefs: strArray(raw.evidenceRefs),
    createdAt: str(raw.createdAt),
  };
}

export function parseConditionLedger(value: unknown): ConditionLedger {
  const raw = asRecord(value);
  return {
    conditions: Array.isArray(raw.conditions) ? raw.conditions.map(parseCondition) : [],
    caseVersion: num(raw.caseVersion),
    confirmable: bool(raw.confirmable),
  };
}

export function parseDisbursementCondition(value: unknown): DisbursementCondition {
  return parseCondition(value);
}

export function parseConditionConfirmation(value: unknown): ConditionConfirmation {
  const raw = asRecord(value);
  return {
    gateType: str(raw.gateType),
    status: str(raw.status),
    caseVersion: num(raw.caseVersion),
    dispositionRef: str(raw.dispositionRef),
  };
}

export function allowedConditionTransitions(status: string): readonly ConditionStatus[] {
  return ALLOWED_TRANSITIONS[status as ConditionStatus] ?? [];
}

export function isRationaleRequired(target: ConditionStatus): boolean {
  return RATIONALE_REQUIRED_TARGETS.includes(target);
}

// --- Client ---

type Fetcher = typeof fetch;
type CsrfTokenProvider = () => string | null;

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

function parseApiError(
  body: unknown,
): { code: string; messageVi: string; retryable: boolean } | null {
  if (typeof body !== "object" || body === null) return null;
  const raw = body as Record<string, unknown>;
  if (typeof raw.code !== "string") return null;
  return {
    code: raw.code,
    messageVi: typeof raw.messageVi === "string" ? raw.messageVi : "",
    retryable: typeof raw.retryable === "boolean" ? raw.retryable : false,
  };
}

export class ConditionsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getLedger(caseId: string): Promise<ConditionLedger> {
    return parseConditionLedger(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/conditions`),
    );
  }

  // Opens ONE condition bound to a permitting decision. 409
  // CONDITIONS_REQUIRE_APPROVAL_DECISION when no approval exists yet.
  async createCondition(
    caseId: string,
    input: CreateConditionInput,
  ): Promise<DisbursementCondition> {
    return parseDisbursementCondition(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/conditions`, {
        method: "POST",
        body: JSON.stringify(compact(input)),
      }),
    );
  }

  // Moves ONE condition along a validated edge under the correct authority. 422
  // RATIONALE_REQUIRED for a waiver / not-applicable ruling with no rationale.
  async transition(
    caseId: string,
    conditionId: string,
    input: TransitionConditionInput,
  ): Promise<DisbursementCondition> {
    return parseDisbursementCondition(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/conditions/${encodeURIComponent(
          conditionId,
        )}/transition`,
        { method: "POST", body: JSON.stringify(compact(input)) },
      ),
    );
  }

  // Independent-checker confirmation (empty body). 409 CONDITIONS_NOT_SATISFIED
  // with blocking ids, or 409 SAME_ACTOR_FORBIDDEN when the confirmer verified.
  async confirm(caseId: string): Promise<ConditionConfirmation> {
    return parseConditionConfirmation(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/conditions/confirm`,
        { method: "POST", body: "{}" },
      ),
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

// Drops undefined / empty-array optional fields so the BFF's exact-keys check
// never sees a key the officer did not fill in.
function compact(input: object): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    out[key] = value;
  }
  return out;
}

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

export const conditionsApi = new ConditionsApiClient();

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

export function getConditionError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò được yêu cầu cho thao tác điều kiện giải ngân.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc điều kiện giải ngân. Vui lòng tải lại.";
      case 409:
        return "Không thể hoàn tất: trạng thái đã thay đổi hoặc điều kiện chưa đủ. Vui lòng tải lại.";
      case 422:
        return "Dữ liệu chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ điều kiện giải ngân chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels ---

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  OPEN: "Đang chờ",
  SATISFIED: "Đạt",
};

export const CONDITION_STATUS_LABELS: Record<ConditionStatus, string> = {
  PENDING: "Chờ xử lý",
  EVIDENCE_SUBMITTED: "Đã nộp bằng chứng",
  VERIFIED: "Đã xác minh",
  FAILED: "Không đạt",
  WAIVER_REQUESTED: "Đề nghị miễn trừ",
  WAIVED_BY_HUMAN: "Đã miễn trừ (thẩm quyền)",
  SUPERSEDED: "Đã thay thế",
  NOT_APPLICABLE_BY_HUMAN: "Không áp dụng (thẩm quyền)",
};

// Precise per-target action labels (never a generic verb).
export const CONDITION_TRANSITION_LABELS: Record<ConditionStatus, string> = {
  PENDING: "Đưa về chờ xử lý",
  EVIDENCE_SUBMITTED: "Ghi nhận đã nộp bằng chứng",
  VERIFIED: "Xác minh điều kiện giải ngân",
  FAILED: "Ghi nhận không đạt",
  WAIVER_REQUESTED: "Đề nghị miễn trừ điều kiện",
  WAIVED_BY_HUMAN: "Miễn trừ điều kiện (ghi thẩm quyền)",
  SUPERSEDED: "Đánh dấu điều kiện đã thay thế",
  NOT_APPLICABLE_BY_HUMAN: "Xác định không áp dụng (ghi thẩm quyền)",
};

export const UNSUPPORTED_ENUM_LABEL = "Trạng thái chưa được hỗ trợ";

export function labelOrUnsupported<K extends string>(
  map: Record<K, string>,
  key: string,
): string {
  return (map as Record<string, string>)[key] ?? UNSUPPORTED_ENUM_LABEL;
}

export function shortId(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso || "—";
  return date.toLocaleString("vi-VN");
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("vi-VN");
}
