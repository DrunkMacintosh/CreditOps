// Self-contained API bindings for the Credit Operations ("Tổng hợp") screen —
// the dossier's final assembly bench. Kept separate from lib/api/client.ts on
// purpose; it mirrors that client's conventions — BFF base "/api/creditops",
// the "__Host-creditops-csrf" cookie surfaced as the "x-creditops-csrf" header
// on mutations, and ApiClientError-style typed failures.
//
// Backend truth mirrored here: services/api/src/creditops/api/credit_ops.py.
//   GET  /api/v1/cases/{caseId}/credit-ops                                     -> CreditOpsStatus
//   POST /api/v1/cases/{caseId}/credit-ops/actions/{actionId}/authorize        -> 201 ActionAuthorization
//   POST /api/v1/cases/{caseId}/credit-ops/document-requests/{requestId}/approve -> 201 DocumentRequestApproval
//
// The Credit Operations Agent NEVER approves or rejects credit, never sends a
// customer-facing request, and never executes a proposed action. Both POSTs
// only RECORD append-only human authority; nothing anywhere in this codebase
// executes on them. This screen prepares and consolidates evidence; humans
// decide.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/credit_ops.py + orchestration) ---

export type GateStatus = "OPEN" | "SATISFIED";

export type UpstreamArtifactKind =
  | "INTAKE_HANDOFF"
  | "UNDERWRITING_ASSESSMENT"
  | "LEGAL_ASSESSMENT"
  | "RISK_REVIEW_ASSESSMENT";

export type ChecklistItemStatus = "PRESENT" | "MISSING";

export type GapBlockingLevel = "BLOCKING" | "CONDITIONAL" | "CLARIFICATION";

export type DocumentRequestApprovalStatus = "PENDING_APPROVAL" | "APPROVED";

export type ProposedActionType =
  | "PREPARE_DOCUMENT_REQUEST"
  | "SCHEDULE_MOCK_LOS_ENTRY"
  | "PREPARE_HANDOFF_PACKAGE";

// The memo section keys, in dossier order (services/.../domain/credit_ops.py
// DraftCreditMemo). thach_thuc_checker additionally carries a deterministic
// disposition status string.
export const MEMO_SECTION_KEYS = [
  "tom_tat_nhu_cau",
  "phan_tich_maker",
  "ra_soat_phap_ly_tsbd",
  "thach_thuc_checker",
  "dieu_kien_de_xuat",
  "phu_luc_bang_chung",
] as const;

export type MemoSectionKey = (typeof MEMO_SECTION_KEYS)[number];

// --- Response shapes ---

export interface Handoff {
  handoffId: string;
  state: string;
  createdAt: string;
}

export interface ChecklistItem {
  artifact: UpstreamArtifactKind | string;
  status: ChecklistItemStatus | string;
  detailVi: string;
  referenceId: string | null;
}

export interface PackageCompleteness {
  artifacts: ChecklistItem[];
  dispositionsStateVi: string;
  unresolvedChallengeCount: number;
  openBlockingGapCount: number;
  allRequiredPresent: boolean;
}

export interface ProvenanceEntry {
  artifact: UpstreamArtifactKind | string;
  assessmentId: string | null;
  executionId: string | null;
  handoffId: string | null;
  citationCount: number;
}

export interface EvidenceConsolidation {
  entries: ProvenanceEntry[];
  distinctCitationCount: number;
}

export interface MemoSectionSummary {
  key: MemoSectionKey;
  statementCount: number;
  citationCount: number;
}

export interface DraftMemoSummary {
  present: boolean;
  syntheticDisclaimerVi: string;
  dispositionStatusVi: string;
  sections: MemoSectionSummary[];
}

export interface ActionAuthorization {
  id: string;
  actionId: string;
  actorId: string;
  actorRole: string;
  rationale: string;
  createdAt: string;
}

export interface DocumentRequestApproval {
  id: string;
  requestId: string;
  actorId: string;
  actorRole: string;
  rationale: string;
  createdAt: string;
}

export interface DocumentRequest {
  id: string;
  originatingGapId: string;
  requestText: string;
  blockingLevel: GapBlockingLevel | string;
  approvalStatus: DocumentRequestApprovalStatus | string;
  approvals: DocumentRequestApproval[];
}

export interface ProposedAction {
  id: string;
  actionType: ProposedActionType | string;
  description: string;
  executionStatus: string;
  relatedDocumentRequestId: string | null;
  authorized: boolean;
  authorizations: ActionAuthorization[];
}

export interface CreditOpsStatus {
  packageId: string;
  caseId: string;
  caseVersion: number;
  agentRole: string;
  executionId: string;
  promptVersion: string;
  createdAt: string;
  handoff: Handoff | null;
  packageCompleteness: PackageCompleteness;
  evidenceConsolidation: EvidenceConsolidation;
  draftMemo: DraftMemoSummary;
  documentRequests: DocumentRequest[];
  proposedActions: ProposedAction[];
  g2GateStatus: GateStatus | string;
  g4GateStatus: GateStatus | string;
}

export interface RecordRationaleInput {
  rationale: string;
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

function optionalStr(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function bool(value: unknown): boolean {
  return value === true;
}

function parseHandoff(value: unknown): Handoff | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = asRecord(value);
  return {
    handoffId: str(raw.handoffId),
    state: str(raw.state),
    createdAt: str(raw.createdAt),
  };
}

// package_completeness passes through the proxy as the stored package dict:
// snake_case keys (services/.../api/credit_ops.py returns it as a raw dict).
function parseChecklistItem(value: unknown): ChecklistItem {
  const raw = asRecord(value);
  return {
    artifact: str(raw.artifact),
    status: str(raw.status),
    detailVi: str(raw.detail_vi),
    referenceId: optionalStr(raw.reference_id),
  };
}

function parsePackageCompleteness(value: unknown): PackageCompleteness {
  const raw = asRecord(value);
  return {
    artifacts: Array.isArray(raw.artifacts) ? raw.artifacts.map(parseChecklistItem) : [],
    dispositionsStateVi: str(raw.dispositions_state_vi),
    unresolvedChallengeCount: num(raw.unresolved_challenge_count),
    openBlockingGapCount: num(raw.open_blocking_gap_count),
    allRequiredPresent: bool(raw.all_required_present),
  };
}

function parseProvenanceEntry(value: unknown): ProvenanceEntry {
  const raw = asRecord(value);
  return {
    artifact: str(raw.artifact),
    assessmentId: optionalStr(raw.assessment_id),
    executionId: optionalStr(raw.execution_id),
    handoffId: optionalStr(raw.handoff_id),
    citationCount: num(raw.citation_count),
  };
}

function parseEvidenceConsolidation(value: unknown): EvidenceConsolidation {
  const raw = asRecord(value);
  return {
    entries: Array.isArray(raw.entries) ? raw.entries.map(parseProvenanceEntry) : [],
    distinctCitationCount: num(raw.distinct_citation_count),
  };
}

function countCitations(statements: unknown): number {
  if (!Array.isArray(statements)) return 0;
  let total = 0;
  for (const statement of statements) {
    const citations = asRecord(statement).citations;
    if (Array.isArray(citations)) total += citations.length;
  }
  return total;
}

// draft_memo also passes through as the stored snake_case dict. We summarise it
// (per-section statement/citation counts) rather than re-render the narrative;
// the full memo is a downstream artifact for the human decision-maker.
function parseDraftMemo(value: unknown): DraftMemoSummary {
  const raw = asRecord(value);
  const present = Object.keys(raw).length > 0;
  const challenge = asRecord(raw.thach_thuc_checker);
  const sections: MemoSectionSummary[] = MEMO_SECTION_KEYS.map((key) => {
    const section = asRecord(raw[key]);
    const statements = section.statements;
    return {
      key,
      statementCount: Array.isArray(statements) ? statements.length : 0,
      citationCount: countCitations(statements),
    };
  });
  return {
    present,
    syntheticDisclaimerVi: str(raw.synthetic_disclaimer_vi),
    dispositionStatusVi: str(challenge.disposition_status_vi),
    sections,
  };
}

function parseActionAuthorization(value: unknown): ActionAuthorization {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    actionId: str(raw.actionId),
    actorId: str(raw.actorId),
    actorRole: str(raw.actorRole),
    rationale: str(raw.rationale),
    createdAt: str(raw.createdAt),
  };
}

function parseDocumentRequestApproval(value: unknown): DocumentRequestApproval {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    requestId: str(raw.requestId),
    actorId: str(raw.actorId),
    actorRole: str(raw.actorRole),
    rationale: str(raw.rationale),
    createdAt: str(raw.createdAt),
  };
}

function parseDocumentRequest(value: unknown): DocumentRequest {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    originatingGapId: str(raw.originatingGapId),
    requestText: str(raw.requestText),
    blockingLevel: str(raw.blockingLevel),
    approvalStatus: str(raw.approvalStatus),
    approvals: Array.isArray(raw.approvals)
      ? raw.approvals.map(parseDocumentRequestApproval)
      : [],
  };
}

function parseProposedAction(value: unknown): ProposedAction {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    actionType: str(raw.actionType),
    description: str(raw.description),
    executionStatus: str(raw.executionStatus),
    relatedDocumentRequestId: optionalStr(raw.relatedDocumentRequestId),
    authorized: bool(raw.authorized),
    authorizations: Array.isArray(raw.authorizations)
      ? raw.authorizations.map(parseActionAuthorization)
      : [],
  };
}

export function parseCreditOpsStatus(value: unknown): CreditOpsStatus {
  const raw = asRecord(value);
  return {
    packageId: str(raw.packageId),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    agentRole: str(raw.agentRole),
    executionId: str(raw.executionId),
    promptVersion: str(raw.promptVersion),
    createdAt: str(raw.createdAt),
    handoff: parseHandoff(raw.handoff),
    packageCompleteness: parsePackageCompleteness(raw.packageCompleteness),
    evidenceConsolidation: parseEvidenceConsolidation(raw.evidenceConsolidation),
    draftMemo: parseDraftMemo(raw.draftMemo),
    documentRequests: Array.isArray(raw.documentRequests)
      ? raw.documentRequests.map(parseDocumentRequest)
      : [],
    proposedActions: Array.isArray(raw.proposedActions)
      ? raw.proposedActions.map(parseProposedAction)
      : [],
    g2GateStatus: str(raw.g2GateStatus),
    g4GateStatus: str(raw.g4GateStatus),
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

export class CreditOpsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = fetch,
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getCreditOps(caseId: string): Promise<CreditOpsStatus> {
    return parseCreditOpsStatus(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/credit-ops`),
    );
  }

  // Records ONE append-only human authorization for ONE proposed action.
  // Only records authority (the G4 gate); nothing is executed here or anywhere.
  async authorizeAction(
    caseId: string,
    actionId: string,
    input: RecordRationaleInput,
  ): Promise<ActionAuthorization> {
    return parseActionAuthorization(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/credit-ops/actions/${encodeURIComponent(
          actionId,
        )}/authorize`,
        { method: "POST", body: JSON.stringify(input) },
      ),
    );
  }

  // Records ONE append-only human approval for ONE drafted document request.
  // Flips only the derived approval view (the G2 gate); never sends anything.
  async approveDocumentRequest(
    caseId: string,
    requestId: string,
    input: RecordRationaleInput,
  ): Promise<DocumentRequestApproval> {
    return parseDocumentRequestApproval(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/credit-ops/document-requests/${encodeURIComponent(
          requestId,
        )}/approve`,
        { method: "POST", body: JSON.stringify(input) },
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

export const creditOpsApi = new CreditOpsApiClient();

// The GET returns 404 with this code when no credit-ops package exists yet for
// the case version — an empty state, not an error. The package is assembled by
// the worker only after intake, thẩm định, pháp chế AND rủi ro are all present.
export function isCreditOpsNotAvailable(error: unknown): boolean {
  return error instanceof ApiClientError && error.code === "CREDIT_OPS_NOT_AVAILABLE";
}

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

// Names what failed and how to recover. Prefers the server's own specific
// Vietnamese message; otherwise maps by status.
export function getCreditOpsError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò vận hành tín dụng để ghi ủy quyền hoặc phê duyệt.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc gói tổng hợp. Vui lòng tải lại.";
      case 409:
        return "Dữ liệu đã thay đổi. Vui lòng tải lại để xem bản mới nhất.";
      case 422:
        return "Thông tin chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ vận hành tín dụng chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display label maps (Vietnamese, sentence case, plain verbs) ---

export const ARTIFACT_LABELS: Record<UpstreamArtifactKind, string> = {
  INTAKE_HANDOFF: "Bàn giao tiếp nhận",
  UNDERWRITING_ASSESSMENT: "Thẩm định tín dụng",
  LEGAL_ASSESSMENT: "Pháp chế & tài sản bảo đảm",
  RISK_REVIEW_ASSESSMENT: "Rà soát rủi ro độc lập",
};

export const ACTION_TYPE_LABELS: Record<ProposedActionType, string> = {
  PREPARE_DOCUMENT_REQUEST: "Chuẩn bị yêu cầu bổ sung tài liệu",
  SCHEDULE_MOCK_LOS_ENTRY: "Lên lịch nhập liệu LOS mô phỏng",
  PREPARE_HANDOFF_PACKAGE: "Chuẩn bị gói bàn giao",
};

export const BLOCKING_LEVEL_LABELS: Record<GapBlockingLevel, string> = {
  BLOCKING: "Chặn",
  CONDITIONAL: "Có điều kiện",
  CLARIFICATION: "Cần làm rõ",
};

export const MEMO_SECTION_LABELS: Record<MemoSectionKey, string> = {
  tom_tat_nhu_cau: "Tóm tắt nhu cầu",
  phan_tich_maker: "Phân tích thẩm định",
  ra_soat_phap_ly_tsbd: "Rà soát pháp lý & TSBĐ",
  thach_thuc_checker: "Thách thức & trạng thái xử lý",
  dieu_kien_de_xuat: "Điều kiện đề xuất",
  phu_luc_bang_chung: "Phụ lục bằng chứng",
};

// The credit-ops route slug of the screen that owns each upstream assessment,
// so the rollup can link each part to its own desk.
export const ARTIFACT_ROUTE_SLUG: Partial<Record<UpstreamArtifactKind, string>> = {
  INTAKE_HANDOFF: "tiep-nhan",
  UNDERWRITING_ASSESSMENT: "tham-dinh",
  LEGAL_ASSESSMENT: "phap-che",
  RISK_REVIEW_ASSESSMENT: "rui-ro",
};

export function labelFor<K extends string>(map: Record<K, string>, key: string): string {
  return (map as Record<string, string>)[key] ?? key;
}

const COUNT_FORMAT = new Intl.NumberFormat("vi-VN");

export function formatCount(value: number): string {
  return COUNT_FORMAT.format(value);
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
