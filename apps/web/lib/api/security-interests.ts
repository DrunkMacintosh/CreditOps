// Self-contained API bindings for the stage-9 Security Perfection ledger screen
// ("Hoàn thiện biện pháp bảo đảm"). Mirrors lib/api/client.ts conventions but
// shares no mutable state with it.
//
// Backend truth mirrored here: services/api/src/creditops/api/security_interests.py
// and services/.../domain/security_interests.py.
//   GET  /api/v1/cases/{caseId}/security-interests                              -> Ledger
//   POST /api/v1/cases/{caseId}/security-interests                              -> 201 Interest
//   POST /api/v1/cases/{caseId}/security-interests/{interestId}/items           -> 201 Item (starts PENDING)
//   POST /api/v1/cases/{caseId}/security-interests/items/{itemId}/transition    -> Item (closed status graph)
//   POST /api/v1/cases/{caseId}/security-interests/confirm                      -> Confirmation (HG_SECURITY_PERFECTION_CONFIRMED)
//
// ONE interest per asset, each carrying MANY perfection requirements tracked
// individually. There is NO valuation and NO priority ranking. Confirmation is
// fail-closed: every interest must carry >=1 requirement and every requirement
// must be terminally satisfied.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/security_interests.py) ---

export type GateStatus = "OPEN" | "SATISFIED";

export type SecurityAssetKind =
  | "REAL_ESTATE"
  | "VEHICLE"
  | "DEPOSIT"
  | "RECEIVABLE"
  | "OTHER";

export type PerfectionStatus =
  | "PENDING"
  | "EVIDENCE_ATTACHED"
  | "COMPLETED"
  | "NOT_REQUIRED_BY_HUMAN"
  | "EXPIRED";

export const SECURITY_ASSET_KINDS: readonly SecurityAssetKind[] = [
  "REAL_ESTATE",
  "VEHICLE",
  "DEPOSIT",
  "RECEIVABLE",
  "OTHER",
];

// The ONLY permitted per-requirement transitions (mirrors ALLOWED_ITEM_TRANSITIONS
// and the SQL trigger). A terminal status maps to an empty set; the UI derives
// the choices from the current status so it can never OFFER a forbidden move.
export const ALLOWED_ITEM_TRANSITIONS: Record<PerfectionStatus, readonly PerfectionStatus[]> = {
  PENDING: ["EVIDENCE_ATTACHED", "NOT_REQUIRED_BY_HUMAN"],
  EVIDENCE_ATTACHED: ["COMPLETED"],
  COMPLETED: ["EXPIRED"],
  NOT_REQUIRED_BY_HUMAN: [],
  EXPIRED: [],
};

// The two terminal states in which a requirement counts as satisfied for the
// confirmation gate (EXPIRED is deliberately excluded).
export const TERMINAL_SATISFIED_STATUSES: readonly PerfectionStatus[] = [
  "COMPLETED",
  "NOT_REQUIRED_BY_HUMAN",
];

// A COMPLETED requirement MUST carry at least one evidence reference (mirrors the
// domain model validator + SQL CHECK); the transition form enforces this before
// it ever calls the API.
export const COMPLETED_REQUIRES_EVIDENCE = true;

// --- Response shapes ---

export interface PerfectionItem {
  id: string;
  interestId: string;
  requirement: string;
  status: PerfectionStatus | string;
  evidenceRefs: string[];
  filingReference: string | null;
  effectiveDate: string | null;
  expiryDate: string | null;
  completedBy: string | null;
  completedAt: string | null;
  createdAt: string;
}

export interface SecurityInterest {
  id: string;
  caseId: string;
  caseVersion: number;
  assetDescription: string;
  assetKind: SecurityAssetKind | string;
  ownerName: string | null;
  valuationReference: string | null;
  notes: string | null;
  createdBy: string;
  createdAt: string;
}

export interface InterestWithItems {
  interest: SecurityInterest;
  items: PerfectionItem[];
}

export interface SecurityLedger {
  interests: InterestWithItems[];
}

export interface SecurityConfirmation {
  gateType: string;
  status: GateStatus | string;
  dispositionRef: string;
}

export interface CreateInterestInput {
  assetDescription: string;
  assetKind: SecurityAssetKind;
  ownerName?: string;
  valuationReference?: string;
  notes?: string;
}

export interface AddItemInput {
  requirement: string;
  evidenceRefs?: string[];
  filingReference?: string;
  effectiveDate?: string;
  expiryDate?: string;
}

export interface TransitionItemInput {
  toStatus: PerfectionStatus;
  rationale?: string;
  evidenceRefs?: string[];
  filingReference?: string;
  effectiveDate?: string;
  expiryDate?: string;
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

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(str) : [];
}

function parseItem(value: unknown): PerfectionItem {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    interestId: str(raw.interestId),
    requirement: str(raw.requirement),
    status: str(raw.status),
    evidenceRefs: strArray(raw.evidenceRefs),
    filingReference: strOrNull(raw.filingReference),
    effectiveDate: strOrNull(raw.effectiveDate),
    expiryDate: strOrNull(raw.expiryDate),
    completedBy: strOrNull(raw.completedBy),
    completedAt: strOrNull(raw.completedAt),
    createdAt: str(raw.createdAt),
  };
}

function parseInterest(value: unknown): SecurityInterest {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    assetDescription: str(raw.assetDescription),
    assetKind: str(raw.assetKind),
    ownerName: strOrNull(raw.ownerName),
    valuationReference: strOrNull(raw.valuationReference),
    notes: strOrNull(raw.notes),
    createdBy: str(raw.createdBy),
    createdAt: str(raw.createdAt),
  };
}

function parseInterestWithItems(value: unknown): InterestWithItems {
  const raw = asRecord(value);
  return {
    interest: parseInterest(raw.interest),
    items: Array.isArray(raw.items) ? raw.items.map(parseItem) : [],
  };
}

export function parseSecurityLedger(value: unknown): SecurityLedger {
  const raw = asRecord(value);
  return {
    interests: Array.isArray(raw.interests)
      ? raw.interests.map(parseInterestWithItems)
      : [],
  };
}

export function parseSecurityInterest(value: unknown): SecurityInterest {
  return parseInterest(value);
}

export function parsePerfectionItem(value: unknown): PerfectionItem {
  return parseItem(value);
}

export function parseSecurityConfirmation(value: unknown): SecurityConfirmation {
  const raw = asRecord(value);
  return {
    gateType: str(raw.gateType),
    status: str(raw.status),
    dispositionRef: str(raw.dispositionRef),
  };
}

// --- Pure derivation (mirrors derive_perfection_blockers) ---

export interface PerfectionBlockers {
  hasInterests: boolean;
  interestsWithoutItems: string[];
  blockingItemIds: string[];
  confirmable: boolean;
}

// Structural client-side hint (the server is authoritative on confirm). No
// vacuous paths: zero interests, an interest with zero items, or any item not
// terminally satisfied is a blocker.
export function derivePerfectionBlockers(
  interests: readonly InterestWithItems[],
): PerfectionBlockers {
  const interestsWithoutItems: string[] = [];
  const blockingItemIds: string[] = [];
  for (const entry of interests) {
    if (entry.items.length === 0) {
      interestsWithoutItems.push(entry.interest.id);
      continue;
    }
    for (const item of entry.items) {
      if (!(TERMINAL_SATISFIED_STATUSES as readonly string[]).includes(item.status)) {
        blockingItemIds.push(item.id);
      }
    }
  }
  const hasInterests = interests.length > 0;
  return {
    hasInterests,
    interestsWithoutItems,
    blockingItemIds,
    confirmable:
      hasInterests && interestsWithoutItems.length === 0 && blockingItemIds.length === 0,
  };
}

export function allowedItemTransitions(status: string): readonly PerfectionStatus[] {
  return ALLOWED_ITEM_TRANSITIONS[status as PerfectionStatus] ?? [];
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

export class SecurityInterestsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getLedger(caseId: string): Promise<SecurityLedger> {
    return parseSecurityLedger(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/security-interests`,
      ),
    );
  }

  async createInterest(
    caseId: string,
    input: CreateInterestInput,
  ): Promise<SecurityInterest> {
    return parseSecurityInterest(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/security-interests`,
        { method: "POST", body: JSON.stringify(compact(input)) },
      ),
    );
  }

  async addItem(
    caseId: string,
    interestId: string,
    input: AddItemInput,
  ): Promise<PerfectionItem> {
    return parsePerfectionItem(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/security-interests/${encodeURIComponent(
          interestId,
        )}/items`,
        { method: "POST", body: JSON.stringify(compact(input)) },
      ),
    );
  }

  async transitionItem(
    caseId: string,
    itemId: string,
    input: TransitionItemInput,
  ): Promise<PerfectionItem> {
    return parsePerfectionItem(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/security-interests/items/${encodeURIComponent(
          itemId,
        )}/transition`,
        { method: "POST", body: JSON.stringify(compact(input)) },
      ),
    );
  }

  async confirm(caseId: string, rationale: string): Promise<SecurityConfirmation> {
    return parseSecurityConfirmation(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/security-interests/confirm`,
        { method: "POST", body: JSON.stringify({ rationale }) },
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

export const securityInterestsApi = new SecurityInterestsApiClient();

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

export function getSecurityError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có thẩm quyền thao tác trên biện pháp bảo đảm.";
      case 404:
        return "Không tìm thấy hồ sơ, biện pháp bảo đảm hoặc yêu cầu. Vui lòng tải lại.";
      case 409:
        return "Không thể hoàn tất: trạng thái đã thay đổi hoặc điều kiện chưa đủ. Vui lòng tải lại.";
      case 422:
        return "Dữ liệu chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ hoàn thiện bảo đảm chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels ---

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  OPEN: "Đang chờ",
  SATISFIED: "Đạt",
};

export const ASSET_KIND_LABELS: Record<SecurityAssetKind, string> = {
  REAL_ESTATE: "Bất động sản",
  VEHICLE: "Phương tiện",
  DEPOSIT: "Tiền gửi",
  RECEIVABLE: "Khoản phải thu",
  OTHER: "Khác",
};

export const PERFECTION_STATUS_LABELS: Record<PerfectionStatus, string> = {
  PENDING: "Chờ xử lý",
  EVIDENCE_ATTACHED: "Đã đính kèm bằng chứng",
  COMPLETED: "Đã hoàn thiện",
  NOT_REQUIRED_BY_HUMAN: "Không yêu cầu (do người phụ trách)",
  EXPIRED: "Đã hết hiệu lực",
};

// Precise per-target action labels (never a generic verb). Each states the exact
// effect of the transition on the requirement.
export const ITEM_TRANSITION_LABELS: Record<PerfectionStatus, string> = {
  PENDING: "Đưa về chờ xử lý",
  EVIDENCE_ATTACHED: "Ghi nhận đã đính kèm bằng chứng",
  COMPLETED: "Ghi nhận hoàn thiện bảo đảm",
  NOT_REQUIRED_BY_HUMAN: "Xác định không yêu cầu",
  EXPIRED: "Ghi nhận hết hiệu lực",
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
