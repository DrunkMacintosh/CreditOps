// Self-contained API bindings for the stage-14 settlement (14A) + recovery-
// preparation (14B) screen ("Tất toán và xử lý nợ"). Mirrors lib/api/client.ts
// conventions but shares no mutable state with it.
//
// Backend truth mirrored here: services/api/src/creditops/api/settlement_recovery.py
// and services/.../domain/settlement_recovery.py.
//   GET  /api/v1/cases/{caseId}/settlement                         -> SettlementView (checks + receipts + confirmable)
//   POST /api/v1/cases/{caseId}/settlement/check                   -> 201 SettlementCheck (409 SETTLEMENT_NOT_ELIGIBLE)
//   POST /api/v1/cases/{caseId}/settlement/confirm                 -> SettlementConfirmation (mock receipts + gate)
//   GET  /api/v1/cases/{caseId}/recovery                           -> RecoveryCases
//   POST /api/v1/cases/{caseId}/recovery                           -> 201 RecoveryCase (409 RECOVERY_NOT_TRIGGERED)
//   POST /api/v1/cases/{caseId}/recovery/{id}/approve-strategy     -> RecoveryApproval (409 SAME_ACTOR_FORBIDDEN)
//
// Two mutually exclusive post-repayment branches; both fail closed. All money
// figures are the server's exact strings and are rendered VERBATIM.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/settlement_recovery.py) ---

export type SettlementReceiptKind = "MOCK_CLOSURE" | "MOCK_RELEASE";

export type RecoveryStatus = "PREPARING" | "STRATEGY_APPROVED";

// --- Response shapes ---

export interface SettlementCheck {
  id: string;
  caseId: string;
  caseVersion: number;
  outstandingPrincipal: string;
  outstandingInterest: string;
  outstandingFees: string;
  openExceptionCount: number;
  zeroBalanceConfirmed: boolean;
  createdAt: string;
}

export interface SettlementReceipt {
  id: string;
  settlementCheckId: string;
  kind: SettlementReceiptKind | string;
  note: string | null;
  createdAt: string;
}

export interface SettlementView {
  checks: SettlementCheck[];
  receipts: SettlementReceipt[];
  caseVersion: number;
  confirmable: boolean;
}

export interface SettlementConfirmation {
  gateType: string;
  status: string;
  caseVersion: number;
  dispositionRef: string;
  receipts: SettlementReceipt[];
}

export interface RecoveryOption {
  label: string;
  description: string;
  consequences: string;
  dependencies: string | null;
}

export interface RecoveryCase {
  id: string;
  caseId: string;
  caseVersion: number;
  triggerSummary: string;
  escalatedBy: string;
  escalationRationale: string;
  status: RecoveryStatus | string;
  evidenceRefs: string[];
  options: RecoveryOption[];
  approvedBy: string | null;
  createdAt: string;
}

export interface RecoveryCases {
  recoveryCases: RecoveryCase[];
  caseVersion: number;
}

export interface RecoveryApproval {
  gateType: string;
  status: string;
  caseVersion: number;
  dispositionRef: string;
  recoveryCase: RecoveryCase;
}

// --- Request inputs ---

export interface SettlementCheckInput {
  outstandingPrincipal: string;
  outstandingInterest: string;
  outstandingFees: string;
  openExceptionCount: number;
}

export interface RecoveryOptionInput {
  label: string;
  description: string;
  consequences: string;
  dependencies?: string;
}

export interface OpenRecoveryInput {
  outstandingTotal: string;
  periodsInShortfall: number;
  triggerSummary: string;
  escalationRationale: string;
  evidenceRefs: string[];
  options: RecoveryOptionInput[];
}

// --- Derived 409 detail shapes ---

export interface SettlementIneligibleDetails {
  zeroBalance: boolean;
  outstandingPrincipal: string;
  outstandingInterest: string;
  outstandingFees: string;
  openExceptionCount: number;
}

export interface RecoveryNotTriggeredDetails {
  outstandingTotal: string;
  periodsInShortfall: number;
  thresholdPeriods: number;
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

function parseCheck(value: unknown): SettlementCheck {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    outstandingPrincipal: str(raw.outstandingPrincipal),
    outstandingInterest: str(raw.outstandingInterest),
    outstandingFees: str(raw.outstandingFees),
    openExceptionCount: num(raw.openExceptionCount),
    zeroBalanceConfirmed: bool(raw.zeroBalanceConfirmed),
    createdAt: str(raw.createdAt),
  };
}

function parseReceipt(value: unknown): SettlementReceipt {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    settlementCheckId: str(raw.settlementCheckId),
    kind: str(raw.kind),
    note: strOrNull(raw.note),
    createdAt: str(raw.createdAt),
  };
}

export function parseSettlementView(value: unknown): SettlementView {
  const raw = asRecord(value);
  return {
    checks: Array.isArray(raw.checks) ? raw.checks.map(parseCheck) : [],
    receipts: Array.isArray(raw.receipts) ? raw.receipts.map(parseReceipt) : [],
    caseVersion: num(raw.caseVersion),
    confirmable: bool(raw.confirmable),
  };
}

export function parseSettlementConfirmation(value: unknown): SettlementConfirmation {
  const raw = asRecord(value);
  return {
    gateType: str(raw.gateType),
    status: str(raw.status),
    caseVersion: num(raw.caseVersion),
    dispositionRef: str(raw.dispositionRef),
    receipts: Array.isArray(raw.receipts) ? raw.receipts.map(parseReceipt) : [],
  };
}

function parseOption(value: unknown): RecoveryOption {
  const raw = asRecord(value);
  return {
    label: str(raw.label),
    description: str(raw.description),
    consequences: str(raw.consequences),
    dependencies: strOrNull(raw.dependencies),
  };
}

function parseRecoveryCase(value: unknown): RecoveryCase {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    triggerSummary: str(raw.triggerSummary),
    escalatedBy: str(raw.escalatedBy),
    escalationRationale: str(raw.escalationRationale),
    status: str(raw.status),
    evidenceRefs: strArray(raw.evidenceRefs),
    options: Array.isArray(raw.options) ? raw.options.map(parseOption) : [],
    approvedBy: strOrNull(raw.approvedBy),
    createdAt: str(raw.createdAt),
  };
}

export function parseRecoveryCases(value: unknown): RecoveryCases {
  const raw = asRecord(value);
  return {
    recoveryCases: Array.isArray(raw.recoveryCases)
      ? raw.recoveryCases.map(parseRecoveryCase)
      : [],
    caseVersion: num(raw.caseVersion),
  };
}

export function parseRecoveryApproval(value: unknown): RecoveryApproval {
  const raw = asRecord(value);
  return {
    gateType: str(raw.gateType),
    status: str(raw.status),
    caseVersion: num(raw.caseVersion),
    dispositionRef: str(raw.dispositionRef),
    recoveryCase: parseRecoveryCase(raw.recoveryCase),
  };
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

function parseErrorDetails(body: unknown): Record<string, unknown> | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  const details = (body as Record<string, unknown>).details;
  if (typeof details !== "object" || details === null || Array.isArray(details)) return null;
  return details as Record<string, unknown>;
}

export class SettlementRecoveryApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getSettlement(caseId: string): Promise<SettlementView> {
    return parseSettlementView(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/settlement`),
    );
  }

  // Records ONE settlement ledger check. 409 SETTLEMENT_NOT_ELIGIBLE (with the
  // derived details) when the snapshot still shows a balance or open exceptions.
  async createSettlementCheck(
    caseId: string,
    input: SettlementCheckInput,
  ): Promise<SettlementCheck> {
    return parseCheck(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/settlement/check`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    );
  }

  // Confirms settlement (empty body): writes the labelled MOCK receipts and
  // satisfies HG_SETTLEMENT_CONFIRMED. 409 SETTLEMENT_NOT_ELIGIBLE unless an
  // eligible zero-balance check exists for the version.
  async confirmSettlement(caseId: string): Promise<SettlementConfirmation> {
    return parseSettlementConfirmation(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/settlement/confirm`, {
        method: "POST",
        body: "{}",
      }),
    );
  }

  async getRecovery(caseId: string): Promise<RecoveryCases> {
    return parseRecoveryCases(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/recovery`),
    );
  }

  // Opens ONE recovery case: requires the deterministic trigger (else 409
  // RECOVERY_NOT_TRIGGERED with details) AND a mandatory human escalation
  // rationale. Never opened from a model score.
  async openRecovery(caseId: string, input: OpenRecoveryInput): Promise<RecoveryCase> {
    return parseRecoveryCase(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/recovery`, {
        method: "POST",
        body: JSON.stringify(compact(input)),
      }),
    );
  }

  // A DIFFERENT human authority approves the recovery strategy (empty body).
  // 409 SAME_ACTOR_FORBIDDEN when the approver escalated the case; 409
  // RECOVERY_ALREADY_APPROVED when it was already approved.
  async approveStrategy(caseId: string, recoveryId: string): Promise<RecoveryApproval> {
    return parseRecoveryApproval(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/recovery/${encodeURIComponent(
          recoveryId,
        )}/approve-strategy`,
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
        null,
        parseErrorDetails(body),
      );
    }
    return body;
  }
}

function compact(input: OpenRecoveryInput): Record<string, unknown> {
  const options = input.options.map((option) => {
    const cleaned: Record<string, unknown> = {
      label: option.label,
      description: option.description,
      consequences: option.consequences,
    };
    if (option.dependencies !== undefined) cleaned.dependencies = option.dependencies;
    return cleaned;
  });
  return {
    outstandingTotal: input.outstandingTotal,
    periodsInShortfall: input.periodsInShortfall,
    triggerSummary: input.triggerSummary,
    escalationRationale: input.escalationRationale,
    evidenceRefs: input.evidenceRefs,
    options,
  };
}

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

export const settlementRecoveryApi = new SettlementRecoveryApiClient();

// --- Typed 409 detail extractors ---

export function getSettlementIneligibleDetails(
  error: unknown,
): SettlementIneligibleDetails | null {
  if (!(error instanceof ApiClientError) || error.code !== "SETTLEMENT_NOT_ELIGIBLE") {
    return null;
  }
  const details = error.details;
  if (!details) return null;
  return {
    zeroBalance: details.zeroBalance === true,
    outstandingPrincipal: str(details.outstandingPrincipal),
    outstandingInterest: str(details.outstandingInterest),
    outstandingFees: str(details.outstandingFees),
    openExceptionCount: num(details.openExceptionCount),
  };
}

export function getRecoveryNotTriggeredDetails(
  error: unknown,
): RecoveryNotTriggeredDetails | null {
  if (!(error instanceof ApiClientError) || error.code !== "RECOVERY_NOT_TRIGGERED") {
    return null;
  }
  const details = error.details;
  if (!details) return null;
  return {
    outstandingTotal: str(details.outstandingTotal),
    periodsInShortfall: num(details.periodsInShortfall),
    thresholdPeriods: num(details.thresholdPeriods),
  };
}

export function isSameActorForbidden(error: unknown): boolean {
  return error instanceof ApiClientError && error.code === "SAME_ACTOR_FORBIDDEN";
}

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

export function getSettlementError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò được yêu cầu cho thao tác tất toán / xử lý nợ.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập. Vui lòng tải lại.";
      case 409:
        return "Không thể hoàn tất: chưa đủ điều kiện hoặc trạng thái đã thay đổi. Vui lòng tải lại.";
      case 422:
        return "Dữ liệu chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ tất toán / xử lý nợ chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels (fail closed on unknown enums) ---

export const RECEIPT_KIND_LABELS: Record<SettlementReceiptKind, string> = {
  MOCK_CLOSURE: "Tất toán khoản vay (mô phỏng)",
  MOCK_RELEASE: "Giải chấp bảo đảm (mô phỏng)",
};

export const RECOVERY_STATUS_LABELS: Record<RecoveryStatus, string> = {
  PREPARING: "Đang chuẩn bị phương án",
  STRATEGY_APPROVED: "Đã duyệt phương án",
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
