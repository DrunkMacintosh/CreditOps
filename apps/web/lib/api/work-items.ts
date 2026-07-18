// Self-contained API bindings for the work-queue ("Hàng việc của tôi") surface
// at /cong-viec — the spec's default entry (master design section 17.1). Kept
// separate from lib/api/client.ts on purpose; it mirrors that client's
// conventions — BFF base "/api/creditops", the credentials-included session
// cookie, the "__Host-creditops-csrf" cookie surfaced as the "x-creditops-csrf"
// header on any future mutation, and ApiClientError-style typed failures — so
// the two never share mutable state.
//
// Read-only by construction: exactly ONE GET, no mutation, no polling. Reading
// the queue grants no authority; an item only says WHERE an authorized human
// action is waiting — it can neither perform that action nor widen who may.
//
// Backend truth mirrored here: services/api/src/creditops/api/work_items.py.
//   GET /api/v1/work-items?limit=  -> { items: WorkItemResponse[] }
//   WorkItemResponse serialization aliases (camelCase): caseId, caseVersion,
//   kind, titleVi, reasonVi, severity, primaryRoute, createdAt.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../application/ports/work_items.py) ---

// PROPOSED synthetic triage levels (design section 2 labels them PROPOSED); NOT
// an official SHB prioritisation. BLOCKING = a human must act before the case
// can progress; ATTENTION = an authorized human action is pending; INFO = the
// system will proceed on its own (surfaced only for visibility).
export type Severity = "BLOCKING" | "ATTENTION" | "INFO";

const KNOWN_SEVERITIES: ReadonlySet<string> = new Set<Severity>([
  "BLOCKING",
  "ATTENTION",
  "INFO",
]);

// The closed set of synthetic item kinds the queue can render meaning for
// (mirror services/.../infrastructure/postgres/work_items.py). An item whose
// kind falls outside this set is KEPT but flagged unsupported — never guessed.
const KNOWN_KINDS: ReadonlySet<string> = new Set<string>([
  "INTAKE_INCOMPLETE",
  "GAP_BATCH_PENDING",
  "RISK_DISPOSITION_PENDING",
  "OPS_AUTHORIZATION_PENDING",
  "MANUAL_REVIEW",
  "RETRY_WAIT",
]);

export type ChipVariant = "risk" | "amber" | "info" | "muted";

// --- Response shapes ---

export interface WorkItem {
  caseId: string;
  caseVersion: number;
  kind: string;
  titleVi: string;
  reasonVi: string;
  severity: string;
  primaryRoute: string;
  createdAt: string;
  // Fail closed: false when the kind OR the severity is outside the known
  // vocabulary. Such items are still listed — with their server-supplied text
  // and route — but render a neutral "unsupported" chip instead of a guess.
  supported: boolean;
}

export interface WorkItemList {
  items: WorkItem[];
}

// --- Defensive parsing (the payload crosses a proxy; never trust its shape) ---

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === "string"
    ? value
    : value === undefined || value === null
      ? ""
      : String(value);
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function parseItem(value: unknown): WorkItem {
  const raw = asRecord(value);
  const kind = str(raw.kind);
  const severity = str(raw.severity);
  return {
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    kind,
    titleVi: str(raw.titleVi),
    reasonVi: str(raw.reasonVi),
    severity,
    primaryRoute: str(raw.primaryRoute),
    createdAt: str(raw.createdAt),
    supported: KNOWN_KINDS.has(kind) && KNOWN_SEVERITIES.has(severity),
  };
}

export function parseWorkItemList(value: unknown): WorkItemList {
  const raw = asRecord(value);
  return {
    items: Array.isArray(raw.items) ? raw.items.map(parseItem) : [],
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

export class WorkItemsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // Lists the actor's pending work items. The session travels via the HttpOnly
  // "__Host" cookie (credentials: "include"); the query grants no authority.
  // `limit` bounds the page size (backend accepts 1..200).
  async listWorkItems(limit?: number): Promise<WorkItemList> {
    const query = new URLSearchParams();
    if (limit !== undefined) query.set("limit", String(limit));
    const suffix = query.toString();
    return parseWorkItemList(
      await this.request(`/api/v1/work-items${suffix ? `?${suffix}` : ""}`),
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

export const workItemsApi = new WorkItemsApiClient();

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

// Names what failed and how to recover. Prefers the server's own specific
// Vietnamese message; otherwise maps by status.
export function getWorkItemsError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò tham gia hồ sơ để xem hàng việc.";
      case 404:
        return "Không tìm thấy hàng việc. Vui lòng tải lại.";
      case 503:
        return "Dịch vụ hàng việc chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels (Vietnamese) + fail-closed severity chip mapping ---

export const SEVERITY_LABELS: Record<Severity, string> = {
  BLOCKING: "Chặn xử lý",
  ATTENTION: "Cần xử lý",
  INFO: "Thông tin",
};

const SEVERITY_VARIANTS: Record<Severity, ChipVariant> = {
  BLOCKING: "risk",
  ATTENTION: "amber",
  INFO: "info",
};

// Fail closed on any enum the UI does not recognize: render a neutral
// "unsupported" label rather than leak a raw backend token or crash.
export const UNSUPPORTED_ENUM_LABEL = "Trạng thái chưa được hỗ trợ";

function isKnownSeverity(value: string): value is Severity {
  return KNOWN_SEVERITIES.has(value);
}

export interface SeverityChip {
  label: string;
  variant: ChipVariant;
}

// The single source of truth for what chip an item shows. Unsupported items
// (unknown kind OR unknown severity) never get a guessed color or urgency —
// they collapse to the neutral "unsupported" chip.
export function severityChip(item: WorkItem): SeverityChip {
  if (!item.supported || !isKnownSeverity(item.severity)) {
    return { label: UNSUPPORTED_ENUM_LABEL, variant: "muted" };
  }
  return { label: SEVERITY_LABELS[item.severity], variant: SEVERITY_VARIANTS[item.severity] };
}

// Blocking-first grouping never elevates an item we do not fully understand:
// only a supported item whose severity is exactly BLOCKING leads the queue.
export function isBlocking(item: WorkItem): boolean {
  return item.supported && item.severity === "BLOCKING";
}

export function shortCaseId(value: string): string {
  if (!value) return "—";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}
