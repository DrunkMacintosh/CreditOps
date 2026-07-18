// Self-contained API bindings for the stage-7 Credit Notification screen
// ("Thông báo tín dụng"). Kept separate from lib/api/client.ts on purpose; it
// mirrors that client's conventions — BFF base "/api/creditops", the
// "__Host-creditops-csrf" cookie surfaced as the "x-creditops-csrf" header on
// mutations, and ApiClientError-style typed failures — so the two never share
// mutable state.
//
// Backend truth mirrored here: services/api/src/creditops/api/notifications.py.
//   GET  /api/v1/cases/{caseId}/notifications          -> NotificationStatus
//   POST /api/v1/cases/{caseId}/notifications          -> 201/200 NotificationDraft
//   POST /api/v1/cases/{caseId}/notifications/approve  -> 200 GateWrite (HG_CREDIT_NOTIFICATION_APPROVED)
//   POST /api/v1/cases/{caseId}/notifications/deliver  -> 201 CommunicationReceipt (LABELLED MOCK)
//
// A credit notification is NOT a disbursement confirmation. The draft derives
// only from a permitting human decision; approval is a human gate write;
// delivery records a labelled mock receipt by a DIFFERENT actor. Nothing is
// ever sent — this client only records and reads.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums ---

export type GateStatus = "OPEN" | "SATISFIED";

// --- Response shapes ---

export interface NotificationDraft {
  id: string;
  caseId: string;
  caseVersion: number;
  decisionId: string;
  content: string;
  contentHash: string;
  createdBy: string;
  createdAt: string;
}

export interface CommunicationReceipt {
  id: string;
  draftId: string;
  deliveredVia: string;
  contentHash: string;
  receiptNote: string | null;
  recordedBy: string;
  createdAt: string;
}

export interface NotificationStatus {
  draft: NotificationDraft | null;
  receipt: CommunicationReceipt | null;
  approvalGateStatus: GateStatus | string;
}

export interface NotificationGateWrite {
  gateType: string;
  status: GateStatus | string;
  draftId: string;
  dispositionRef: string;
}

export interface ApproveNotificationInput {
  draftId: string;
  rationale: string;
}

export interface DeliverNotificationInput {
  receiptNote?: string;
}

// --- Defensive parsing (the payload crosses a proxy; never trust its shape) ---

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

function parseDraft(value: unknown): NotificationDraft {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    decisionId: str(raw.decisionId),
    content: str(raw.content),
    contentHash: str(raw.contentHash),
    createdBy: str(raw.createdBy),
    createdAt: str(raw.createdAt),
  };
}

function parseReceipt(value: unknown): CommunicationReceipt {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    draftId: str(raw.draftId),
    deliveredVia: str(raw.deliveredVia),
    contentHash: str(raw.contentHash),
    receiptNote: strOrNull(raw.receiptNote),
    recordedBy: str(raw.recordedBy),
    createdAt: str(raw.createdAt),
  };
}

export function parseNotificationStatus(value: unknown): NotificationStatus {
  const raw = asRecord(value);
  return {
    draft: raw.draft == null ? null : parseDraft(raw.draft),
    receipt: raw.receipt == null ? null : parseReceipt(raw.receipt),
    approvalGateStatus: str(raw.approvalGateStatus),
  };
}

export function parseNotificationDraft(value: unknown): NotificationDraft {
  return parseDraft(value);
}

export function parseCommunicationReceipt(value: unknown): CommunicationReceipt {
  return parseReceipt(value);
}

export function parseNotificationGateWrite(value: unknown): NotificationGateWrite {
  const raw = asRecord(value);
  return {
    gateType: str(raw.gateType),
    status: str(raw.status),
    draftId: str(raw.draftId),
    dispositionRef: str(raw.dispositionRef),
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

export class NotificationsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // Reads the current draft, its mock receipt (if any), and the gate status. A
  // null draft is the empty state (no permitting decision yet), never an error.
  async getStatus(caseId: string): Promise<NotificationStatus> {
    return parseNotificationStatus(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/notifications`),
    );
  }

  // Idempotently creates the deterministic draft. 409
  // DECISION_DOES_NOT_PERMIT_NOTIFICATION when no permitting decision exists.
  async createDraft(caseId: string): Promise<NotificationDraft> {
    return parseNotificationDraft(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/notifications`, {
        method: "POST",
        body: "{}",
      }),
    );
  }

  // Records the human gate write against the EXACT draft id. 409
  // STALE_NOTIFICATION_DRAFT when the draft has moved on.
  async approve(
    caseId: string,
    input: ApproveNotificationInput,
  ): Promise<NotificationGateWrite> {
    return parseNotificationGateWrite(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/notifications/approve`,
        { method: "POST", body: JSON.stringify(input) },
      ),
    );
  }

  // Records the LABELLED MOCK delivery. Must be a DIFFERENT actor than the draft
  // creator (409 SAME_ACTOR_FORBIDDEN) with the approval gate satisfied.
  async deliver(
    caseId: string,
    input: DeliverNotificationInput = {},
  ): Promise<CommunicationReceipt> {
    const body = input.receiptNote ? { receiptNote: input.receiptNote } : {};
    return parseCommunicationReceipt(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/notifications/deliver`,
        { method: "POST", body: JSON.stringify(body) },
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

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

export const notificationsApi = new NotificationsApiClient();

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

// Names what failed and how to recover. Prefers the server's own specific
// Vietnamese message; otherwise maps by status.
export function getNotificationError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò vận hành tín dụng để thao tác thông báo.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc bản nháp thông báo. Vui lòng tải lại.";
      case 409:
        return "Dữ liệu đã thay đổi hoặc điều kiện chưa thoả mãn. Vui lòng tải lại.";
      case 422:
        return "Thông tin chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ thông báo tín dụng chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels (Vietnamese, sentence case, plain verbs) ---

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  OPEN: "Đang chờ",
  SATISFIED: "Đạt",
};

// The mandatory disclaimer the notification screen must always display.
export const NOTIFICATION_NOT_DISBURSEMENT_VI =
  "Thông báo tín dụng không phải xác nhận giải ngân.";

// Fail closed on any enum value the UI does not recognize: render a neutral
// "unsupported" label rather than leak a raw backend token or crash.
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
