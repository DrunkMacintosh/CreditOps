// Self-contained API bindings for the Independent Risk Review ("Rủi ro")
// screen. Kept separate from lib/api/client.ts (the shared client) on purpose;
// it mirrors that client's conventions — BFF base "/api/creditops", the
// "__Host-creditops-csrf" cookie surfaced as the "x-creditops-csrf" header on
// mutations, and ApiClientError-style typed failures — so the two never need
// to share mutable state.
//
// Backend truth mirrored here: services/api/src/creditops/api/risk_review.py.
//   GET  /api/v1/cases/{caseId}/risk-review                                 -> RiskReviewStatus
//   POST /api/v1/cases/{caseId}/risk-review/challenges/{challengeId}/disposition -> 201 Disposition
//   POST /api/v1/cases/{caseId}/risk-review/disposition                     -> 201 Disposition (NOTED only)
//
// The reviewer (checker) NEVER approves or rejects credit and never resolves
// its own challenge. This screen only lets an authorized human RECORD a
// disposition; the system prepares and reviews evidence, humans decide.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/risk_review.py + orchestration) ---

export type ChallengeSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type ConfidenceLevel = "HIGH" | "MEDIUM" | "LOW";
export type ChallengeType =
  | "UNSUPPORTED_ASSUMPTION"
  | "OMITTED_RISK"
  | "INADEQUATE_MITIGANT"
  | "GAP_VISIBILITY"
  | "EXCEPTION_VISIBILITY"
  | "OTHER_CONCERN";
export type MakerSource = "CREDIT_UNDERWRITING" | "LEGAL_COMPLIANCE_COLLATERAL";
export type RaisedBy = "DETERMINISTIC" | "LLM";
export type DispositionType = "ACCEPTED_RISK" | "MAKER_MUST_REVISE" | "ESCALATED" | "NOTED";
export type GateStatus = "OPEN" | "SATISFIED";

// A challenge at or above this severity requires its own human disposition
// before the G3 risk-disposition gate may derive SATISFIED. Mirrors
// application/orchestration/gates.py::G3_SEVERITY_THRESHOLD.
export const SEVERE_SEVERITIES: readonly ChallengeSeverity[] = ["HIGH", "CRITICAL"];

export function isSevere(severity: string): boolean {
  return severity === "HIGH" || severity === "CRITICAL";
}

// --- Response shapes ---

export interface MakerFindingRef {
  makerSource: MakerSource | string;
  makerAssessmentId: string;
  sectionPath: string;
}

export type EvidenceCitation =
  | { kind: "CONFIRMED_FACT"; confirmedFactId: string }
  | { kind: "CALCULATOR_RESULT"; resultId: string }
  | { kind: "DOCUMENT_REGION"; documentVersionId: string; region: string }
  | {
      kind: "POLICY_CITATION";
      corpusId: string;
      corpusVersion: string;
      documentId: string;
      clauseId: string;
      quotedTextVi: string;
    }
  | { kind: "CONTROLLED_CHECK"; invocationId: string }
  | { kind: "MAKER_FINDING"; ref: MakerFindingRef }
  | { kind: "UNKNOWN"; label: string };

export interface Disposition {
  id: string;
  dispositionType: DispositionType | string;
  rationale: string;
  actorId: string;
  actorRole: string;
  createdAt: string;
}

export interface Challenge {
  id: string;
  target: MakerFindingRef;
  challengeType: ChallengeType | string;
  statement: string;
  citations: EvidenceCitation[];
  severity: ChallengeSeverity | string;
  confidence: ConfidenceLevel | string;
  raisedBy: RaisedBy | string;
  dispositions: Disposition[];
}

export interface Handoff {
  handoffId: string;
  state: string;
  createdAt: string;
}

export interface RiskReviewStatus {
  assessmentId: string;
  caseId: string;
  caseVersion: number;
  agentRole: string;
  executionId: string;
  promptVersion: string;
  createdAt: string;
  handoff: Handoff | null;
  challenges: Challenge[];
  assessmentLevelDispositions: Disposition[];
  unresolvedChallengeCount: number;
  gateStatus: GateStatus | string;
}

export interface RecordDispositionInput {
  dispositionType: DispositionType;
  rationale: string;
}

// --- Defensive parsing (the payload crosses a proxy; never trust its shape) ---

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function parseTarget(value: unknown): MakerFindingRef {
  const raw = asRecord(value);
  return {
    makerSource: str(raw.maker_source),
    makerAssessmentId: str(raw.maker_assessment_id),
    sectionPath: str(raw.section_path),
  };
}

function parseCitation(value: unknown): EvidenceCitation {
  const raw = asRecord(value);
  switch (raw.kind) {
    case "CONFIRMED_FACT":
      return { kind: "CONFIRMED_FACT", confirmedFactId: str(raw.confirmed_fact_id) };
    case "CALCULATOR_RESULT":
      return { kind: "CALCULATOR_RESULT", resultId: str(raw.result_id) };
    case "DOCUMENT_REGION":
      return {
        kind: "DOCUMENT_REGION",
        documentVersionId: str(raw.document_version_id),
        region: str(raw.region),
      };
    case "POLICY_CITATION":
      return {
        kind: "POLICY_CITATION",
        corpusId: str(raw.corpus_id),
        corpusVersion: str(raw.corpus_version),
        documentId: str(raw.document_id),
        clauseId: str(raw.clause_id),
        quotedTextVi: str(raw.quoted_text_vi),
      };
    case "CONTROLLED_CHECK":
      return { kind: "CONTROLLED_CHECK", invocationId: str(raw.invocation_id) };
    case "MAKER_FINDING":
      return { kind: "MAKER_FINDING", ref: parseTarget(raw.ref) };
    default:
      return { kind: "UNKNOWN", label: str(raw.kind) || "không rõ" };
  }
}

function parseDisposition(value: unknown): Disposition {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    dispositionType: str(raw.dispositionType),
    rationale: str(raw.rationale),
    actorId: str(raw.actorId),
    actorRole: str(raw.actorRole),
    createdAt: str(raw.createdAt),
  };
}

function parseChallenge(value: unknown): Challenge {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    target: parseTarget(raw.target),
    challengeType: str(raw.challengeType),
    statement: str(raw.statement),
    citations: Array.isArray(raw.citations) ? raw.citations.map(parseCitation) : [],
    severity: str(raw.severity),
    confidence: str(raw.confidence),
    raisedBy: str(raw.raisedBy),
    dispositions: Array.isArray(raw.dispositions) ? raw.dispositions.map(parseDisposition) : [],
  };
}

export function parseRiskReviewStatus(value: unknown): RiskReviewStatus {
  const raw = asRecord(value);
  const handoffRaw = raw.handoff;
  return {
    assessmentId: str(raw.assessmentId),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    agentRole: str(raw.agentRole),
    executionId: str(raw.executionId),
    promptVersion: str(raw.promptVersion),
    createdAt: str(raw.createdAt),
    handoff:
      handoffRaw && typeof handoffRaw === "object"
        ? {
            handoffId: str(asRecord(handoffRaw).handoffId),
            state: str(asRecord(handoffRaw).state),
            createdAt: str(asRecord(handoffRaw).createdAt),
          }
        : null,
    challenges: Array.isArray(raw.challenges) ? raw.challenges.map(parseChallenge) : [],
    assessmentLevelDispositions: Array.isArray(raw.assessmentLevelDispositions)
      ? raw.assessmentLevelDispositions.map(parseDisposition)
      : [],
    unresolvedChallengeCount: num(raw.unresolvedChallengeCount),
    gateStatus: str(raw.gateStatus),
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

export class RiskReviewApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getRiskReview(caseId: string): Promise<RiskReviewStatus> {
    return parseRiskReviewStatus(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/risk-review`),
    );
  }

  async recordChallengeDisposition(
    caseId: string,
    challengeId: string,
    input: RecordDispositionInput,
  ): Promise<Disposition> {
    return parseDisposition(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/risk-review/challenges/${encodeURIComponent(
          challengeId,
        )}/disposition`,
        { method: "POST", body: JSON.stringify(input) },
      ),
    );
  }

  async recordAssessmentDisposition(
    caseId: string,
    input: RecordDispositionInput,
  ): Promise<Disposition> {
    return parseDisposition(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/risk-review/disposition`,
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

export const riskReviewApi = new RiskReviewApiClient();

// The GET returns 404 with this code when no checker assessment exists yet for
// the case version — an empty state, not an error (silence is never treated as
// satisfaction anywhere in this flow).
export function isRiskReviewNotAvailable(error: unknown): boolean {
  return error instanceof ApiClientError && error.code === "RISK_REVIEW_NOT_AVAILABLE";
}

const GENERIC_MESSAGE = "Không thể hoàn tất yêu cầu.";

// Names what failed and how to recover. Prefers the server's own Vietnamese
// message when it is specific; otherwise maps by status.
export function getRiskReviewError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (
      error.message &&
      error.message !== GENERIC_MESSAGE &&
      error.message !== "Yêu cầu không thành công."
    ) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò rà soát rủi ro độc lập để ghi quyết định.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc bản rà soát rủi ro. Vui lòng tải lại.";
      case 409:
        return "Dữ liệu đã thay đổi. Vui lòng tải lại để xem bản mới nhất.";
      case 422:
        return "Thông tin quyết định chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ rà soát rủi ro chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display label maps (Vietnamese, sentence case, plain verbs) ---

export const CHALLENGE_TYPE_LABELS: Record<ChallengeType, string> = {
  UNSUPPORTED_ASSUMPTION: "Giả định thiếu căn cứ",
  OMITTED_RISK: "Rủi ro bị bỏ sót",
  INADEQUATE_MITIGANT: "Biện pháp giảm thiểu chưa đủ",
  GAP_VISIBILITY: "Khoảng trống chặn không còn hiển thị",
  EXCEPTION_VISIBILITY: "Ngoại lệ không còn hiển thị",
  OTHER_CONCERN: "Quan ngại khác",
};

export const SEVERITY_LABELS: Record<ChallengeSeverity, string> = {
  CRITICAL: "Nghiêm trọng",
  HIGH: "Cao",
  MEDIUM: "Trung bình",
  LOW: "Thấp",
};

export const CONFIDENCE_LABELS: Record<ConfidenceLevel, string> = {
  HIGH: "Cao",
  MEDIUM: "Trung bình",
  LOW: "Thấp",
};

export const MAKER_SOURCE_LABELS: Record<MakerSource, string> = {
  CREDIT_UNDERWRITING: "Thẩm định tín dụng",
  LEGAL_COMPLIANCE_COLLATERAL: "Pháp chế & tài sản bảo đảm",
};

export const RAISED_BY_LABELS: Record<RaisedBy, string> = {
  DETERMINISTIC: "Kiểm tra tất định",
  LLM: "Rà soát AI",
};

export const DISPOSITION_TYPE_LABELS: Record<DispositionType, string> = {
  ACCEPTED_RISK: "Chấp nhận rủi ro",
  MAKER_MUST_REVISE: "Yêu cầu bên lập chỉnh sửa",
  ESCALATED: "Chuyển cấp trên xem xét",
  NOTED: "Ghi nhận",
};

export function labelFor<K extends string>(map: Record<K, string>, key: string): string {
  return (map as Record<string, string>)[key] ?? key;
}
