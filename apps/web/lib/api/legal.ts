// Self-contained read-only bindings for the "Pháp chế" (legal, compliance and
// collateral) screen. Mirrors the shared client's conventions — BFF base
// "/api/creditops", typed ApiClientError, cookie-based session — but keeps its
// own faithful types + tolerant parser so the deep legal assessment payload
// never blanks the page on a single malformed field. GET only: this surface
// has no write path (the assessment store is append-only, written by workers).
//
// Wire shape (upstream services/api/src/creditops/api/legal.py):
//   LegalAssessmentResponse — camelCase envelope (serialization aliases) whose
//   `assessment` is `LegalComplianceAssessment.model_dump(mode="json")`, i.e.
//   snake_case throughout (the domain model carries no serialization aliases).
// The parser below reads the envelope in camelCase and the nested assessment in
// snake_case exactly as they arrive.

import { ApiClientError, getVietnameseApiError } from "./client";

const BFF_BASE_URL = "/api/creditops";

export type LegalConfidence = "HIGH" | "MEDIUM" | "LOW";
export type ControlledCheckType = "KYC" | "AML_WATCHLIST" | "RELATED_PARTY";
export type ControlledCheckStatus = "CLEAR" | "HIT" | "INCONCLUSIVE";
export type CollateralDocumentStatus = "PRESENT" | "MISSING" | "EXPIRED";
export type ExceptionCategory = "POLICY" | "LEGAL" | "COLLATERAL";
export type GapBlockingLevel = "BLOCKING" | "CONDITIONAL" | "CLARIFICATION";
export type SubjectType = "ENTITY" | "INDIVIDUAL";

export type EvidenceCitation =
  | { kind: "CONFIRMED_FACT"; confirmedFactId: string }
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
  | { kind: "UNKNOWN"; label: string };

export interface LegalFinding {
  statementVi: string;
  citations: EvidenceCitation[];
  confidence: LegalConfidence | null;
  uncertaintyVi: string;
}

export interface PolicyFinding {
  possibleIssueVi: string;
  citations: EvidenceCitation[];
  confidence: LegalConfidence | null;
  uncertaintyVi: string;
}

export interface ControlledCheckInterpretation {
  invocationId: string;
  statementVi: string;
  confidence: LegalConfidence | null;
  uncertaintyVi: string;
}

export interface OwnershipInconsistency {
  descriptionVi: string;
  citations: EvidenceCitation[];
  confidence: LegalConfidence | null;
}

export interface CollateralDocumentItem {
  documentTypeKey: string;
  labelVi: string;
  status: CollateralDocumentStatus | null;
  citations: EvidenceCitation[];
  expiryDate: string | null;
  notesVi: string;
}

export interface ExceptionItem {
  category: ExceptionCategory | null;
  possibleIssueVi: string;
  citations: EvidenceCitation[];
  confidence: LegalConfidence | null;
  uncertaintyVi: string;
}

export interface AssumptionItem {
  statementVi: string;
  rationaleVi: string;
  basisCitations: EvidenceCitation[];
}

export interface EvidenceGapItem {
  missingInformationVi: string;
  whyNeededVi: string;
  blockingLevel: GapBlockingLevel | null;
  suggestedEvidenceVi: string[];
}

export interface ControlledCheckResult {
  invocationId: string;
  checkType: ControlledCheckType | null;
  providerId: string;
  toolName: string;
  toolVersion: string;
  subjectType: SubjectType | null;
  subjectRefVi: string;
  status: ControlledCheckStatus | null;
  resultSummaryVi: string;
  invokedAt: string | null;
  isMock: boolean;
}

export interface PolicyHitRecord {
  corpusId: string;
  corpusVersion: string;
  documentId: string;
  clauseId: string;
  quotedTextVi: string;
}

export interface PolicyCorpusRef {
  corpusId: string;
  version: string;
  checksumSha256: string;
  isSynthetic: boolean;
}

export interface LegalAssessmentProvenance {
  caseId: string;
  caseVersion: number;
  agentRole: string;
  executionId: string;
  taskId: string;
  promptVersion: string;
  modelId: string;
  endpointId: string;
  evidenceViewBuiltAt: string | null;
  createdAt: string | null;
}

export interface LegalAssessmentBody {
  id: string;
  provenance: LegalAssessmentProvenance | null;
  legalEntityReview: LegalFinding[];
  authoritySignatoryReview: LegalFinding[];
  ownershipConsistency: {
    findings: LegalFinding[];
    inconsistencies: OwnershipInconsistency[];
  };
  policyReview: PolicyFinding[];
  controlledCheckInterpretations: ControlledCheckInterpretation[];
  collateralReview: {
    documentItems: CollateralDocumentItem[];
    ownershipEvidenceFindings: LegalFinding[];
  };
  exceptions: ExceptionItem[];
  assumptions: AssumptionItem[];
  evidenceGaps: EvidenceGapItem[];
  policyHits: PolicyHitRecord[];
  policyCorpusRef: PolicyCorpusRef | null;
  controlledCheckResults: ControlledCheckResult[];
}

export interface LegalHandoff {
  handoffId: string;
  state: string;
  createdAt: string | null;
}

export interface LegalAssessment {
  assessmentId: string;
  caseId: string;
  caseVersion: number;
  agentRole: string;
  executionId: string;
  promptVersion: string;
  createdAt: string | null;
  assessment: LegalAssessmentBody;
  handoff: LegalHandoff | null;
}

// --- tolerant field readers ---------------------------------------------------

function rec(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function optStr(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : null;
}

const CONFIDENCE: readonly LegalConfidence[] = ["HIGH", "MEDIUM", "LOW"];

function parseCitation(raw: unknown): EvidenceCitation | null {
  const node = rec(raw);
  if (node === null) return null;
  switch (node.kind) {
    case "CONFIRMED_FACT":
      return { kind: "CONFIRMED_FACT", confirmedFactId: str(node.confirmed_fact_id) };
    case "DOCUMENT_REGION":
      return {
        kind: "DOCUMENT_REGION",
        documentVersionId: str(node.document_version_id),
        region: str(node.region),
      };
    case "POLICY_CITATION":
      return {
        kind: "POLICY_CITATION",
        corpusId: str(node.corpus_id),
        corpusVersion: str(node.corpus_version),
        documentId: str(node.document_id),
        clauseId: str(node.clause_id),
        quotedTextVi: str(node.quoted_text_vi),
      };
    case "CONTROLLED_CHECK":
      return { kind: "CONTROLLED_CHECK", invocationId: str(node.invocation_id) };
    default:
      return { kind: "UNKNOWN", label: str(node.kind, "không xác định") };
  }
}

function parseCitations(raw: unknown): EvidenceCitation[] {
  const out: EvidenceCitation[] = [];
  for (const item of arr(raw)) {
    const citation = parseCitation(item);
    if (citation) out.push(citation);
  }
  return out;
}

function parseFinding(raw: unknown): LegalFinding | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    statementVi: str(node.statement_vi),
    citations: parseCitations(node.citations),
    confidence: oneOf(node.confidence, CONFIDENCE),
    uncertaintyVi: str(node.uncertainty_vi),
  };
}

function parseFindings(raw: unknown): LegalFinding[] {
  const section = rec(raw);
  const list = section ? section.findings : raw;
  const out: LegalFinding[] = [];
  for (const item of arr(list)) {
    const finding = parseFinding(item);
    if (finding) out.push(finding);
  }
  return out;
}

function parseProvenance(raw: unknown): LegalAssessmentProvenance | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    caseId: str(node.case_id),
    caseVersion: num(node.case_version),
    agentRole: str(node.agent_role),
    executionId: str(node.execution_id),
    taskId: str(node.task_id),
    promptVersion: str(node.prompt_version),
    modelId: str(node.model_id),
    endpointId: str(node.endpoint_id),
    evidenceViewBuiltAt: optStr(node.evidence_view_built_at),
    createdAt: optStr(node.created_at),
  };
}

function parseControlledCheckResult(raw: unknown): ControlledCheckResult | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    invocationId: str(node.invocation_id),
    checkType: oneOf(node.check_type, ["KYC", "AML_WATCHLIST", "RELATED_PARTY"] as const),
    providerId: str(node.provider_id),
    toolName: str(node.tool_name),
    toolVersion: str(node.tool_version),
    subjectType: oneOf(node.subject_type, ["ENTITY", "INDIVIDUAL"] as const),
    subjectRefVi: str(node.subject_ref_vi),
    status: oneOf(node.status, ["CLEAR", "HIT", "INCONCLUSIVE"] as const),
    resultSummaryVi: str(node.result_summary_vi),
    invokedAt: optStr(node.invoked_at),
    isMock: bool(node.is_mock, true),
  };
}

function parseInterpretation(raw: unknown): ControlledCheckInterpretation | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    invocationId: str(node.invocation_id),
    statementVi: str(node.statement_vi),
    confidence: oneOf(node.confidence, CONFIDENCE),
    uncertaintyVi: str(node.uncertainty_vi),
  };
}

function parseCollateralItem(raw: unknown): CollateralDocumentItem | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    documentTypeKey: str(node.document_type_key),
    labelVi: str(node.label_vi),
    status: oneOf(node.status, ["PRESENT", "MISSING", "EXPIRED"] as const),
    citations: parseCitations(node.citations),
    expiryDate: optStr(node.expiry_date),
    notesVi: str(node.notes_vi),
  };
}

function parsePolicyFinding(raw: unknown): PolicyFinding | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    possibleIssueVi: str(node.possible_issue_vi),
    citations: parseCitations(node.citations),
    confidence: oneOf(node.confidence, CONFIDENCE),
    uncertaintyVi: str(node.uncertainty_vi),
  };
}

function parseException(raw: unknown): ExceptionItem | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    category: oneOf(node.category, ["POLICY", "LEGAL", "COLLATERAL"] as const),
    possibleIssueVi: str(node.possible_issue_vi),
    citations: parseCitations(node.citations),
    confidence: oneOf(node.confidence, CONFIDENCE),
    uncertaintyVi: str(node.uncertainty_vi),
  };
}

function parseAssumption(raw: unknown): AssumptionItem | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    statementVi: str(node.statement_vi),
    rationaleVi: str(node.rationale_vi),
    basisCitations: parseCitations(node.basis_citations),
  };
}

function parseGap(raw: unknown): EvidenceGapItem | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    missingInformationVi: str(node.missing_information_vi),
    whyNeededVi: str(node.why_needed_vi),
    blockingLevel: oneOf(node.blocking_level, [
      "BLOCKING",
      "CONDITIONAL",
      "CLARIFICATION",
    ] as const),
    suggestedEvidenceVi: arr(node.suggested_evidence_vi).map((entry) => str(entry)),
  };
}

function parsePolicyHit(raw: unknown): PolicyHitRecord | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    corpusId: str(node.corpus_id),
    corpusVersion: str(node.corpus_version),
    documentId: str(node.document_id),
    clauseId: str(node.clause_id),
    quotedTextVi: str(node.quoted_text_vi),
  };
}

function parsePolicyCorpusRef(raw: unknown): PolicyCorpusRef | null {
  const node = rec(raw);
  if (node === null) return null;
  return {
    corpusId: str(node.corpus_id),
    version: str(node.version),
    checksumSha256: str(node.checksum_sha256),
    isSynthetic: bool(node.is_synthetic, true),
  };
}

function collect<T>(raw: unknown, parse: (item: unknown) => T | null): T[] {
  const out: T[] = [];
  for (const item of arr(raw)) {
    const parsed = parse(item);
    if (parsed !== null) out.push(parsed);
  }
  return out;
}

function parseAssessmentBody(raw: unknown): LegalAssessmentBody {
  const node = rec(raw) ?? {};
  const ownership = rec(node.ownership_consistency) ?? {};
  const collateral = rec(node.collateral_review) ?? {};
  return {
    id: str(node.id),
    provenance: parseProvenance(node.provenance),
    legalEntityReview: parseFindings(node.legal_entity_review),
    authoritySignatoryReview: parseFindings(node.authority_signatory_review),
    ownershipConsistency: {
      findings: parseFindings(ownership.findings),
      inconsistencies: collect(ownership.inconsistencies, (item) => {
        const it = rec(item);
        if (it === null) return null;
        return {
          descriptionVi: str(it.description_vi),
          citations: parseCitations(it.citations),
          confidence: oneOf(it.confidence, CONFIDENCE),
        } satisfies OwnershipInconsistency;
      }),
    },
    policyReview: collect(node.policy_review, parsePolicyFinding),
    controlledCheckInterpretations: collect(
      node.controlled_check_interpretations,
      parseInterpretation,
    ),
    collateralReview: {
      documentItems: collect(collateral.document_items, parseCollateralItem),
      ownershipEvidenceFindings: parseFindings(collateral.ownership_evidence_findings),
    },
    exceptions: collect(node.exceptions, parseException),
    assumptions: collect(node.assumptions, parseAssumption),
    evidenceGaps: collect(node.evidence_gaps, parseGap),
    policyHits: collect(node.policy_hits, parsePolicyHit),
    policyCorpusRef: parsePolicyCorpusRef(node.policy_corpus_ref),
    controlledCheckResults: collect(node.controlled_check_results, parseControlledCheckResult),
  };
}

export class LegalParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LegalParseError";
  }
}

export function parseLegalAssessment(raw: unknown): LegalAssessment {
  const node = rec(raw);
  if (node === null || rec(node.assessment) === null) {
    throw new LegalParseError("Phản hồi rà soát pháp chế không hợp lệ.");
  }
  const handoffNode = rec(node.handoff);
  return {
    assessmentId: str(node.assessmentId),
    caseId: str(node.caseId),
    caseVersion: num(node.caseVersion),
    agentRole: str(node.agentRole),
    executionId: str(node.executionId),
    promptVersion: str(node.promptVersion),
    createdAt: optStr(node.createdAt),
    assessment: parseAssessmentBody(node.assessment),
    handoff: handoffNode
      ? {
          handoffId: str(handoffNode.handoffId),
          state: str(handoffNode.state),
          createdAt: optStr(handoffNode.createdAt),
        }
      : null,
  };
}

// --- fetch --------------------------------------------------------------------

function parseApiError(
  body: unknown,
): { code: string; messageVi: string; retryable: boolean } | null {
  const node = rec(body);
  if (node === null) return null;
  return {
    code: typeof node.code === "string" ? node.code : "REQUEST_FAILED",
    messageVi: typeof node.messageVi === "string" ? node.messageVi : "",
    retryable: typeof node.retryable === "boolean" ? node.retryable : false,
  };
}

async function readJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export interface LegalApi {
  getLegalAssessment(caseId: string): Promise<LegalAssessment>;
}

export class LegalApiClient implements LegalApi {
  private readonly baseUrl: string;

  constructor(
    baseUrl: string = BFF_BASE_URL,
    private readonly fetcher: typeof fetch = fetch,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async getLegalAssessment(caseId: string): Promise<LegalAssessment> {
    const response = await this.fetcher(
      `${this.baseUrl}/api/v1/cases/${encodeURIComponent(caseId)}/legal`,
      {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "include",
        cache: "no-store",
      },
    );
    const body = await readJson(response);
    if (!response.ok) {
      const apiError = parseApiError(body);
      throw new ApiClientError(
        response.status,
        apiError?.code ?? "REQUEST_FAILED",
        apiError?.messageVi || "Yêu cầu không thành công.",
        apiError?.retryable ?? response.status >= 500,
      );
    }
    return parseLegalAssessment(body);
  }
}

export const legalApi: LegalApi = new LegalApiClient();

/** True when the 404 means "no assessment yet" (invite an empty state, not an error). */
export function isLegalAssessmentNotReady(error: unknown): boolean {
  return (
    error instanceof ApiClientError && error.code === "LEGAL_ASSESSMENT_NOT_AVAILABLE"
  );
}

/** Vietnamese message that names what failed and how to recover. */
export function getLegalErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    switch (error.code) {
      case "LEGAL_SERVICE_UNAVAILABLE":
      case "CASE_SERVICE_UNAVAILABLE":
        return "Dịch vụ pháp chế tạm thời chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
      case "CASE_NOT_ACCESSIBLE":
        return "Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập rà soát pháp chế.";
      case "INSUFFICIENT_ROLE":
        return "Bạn không có vai trò tham gia hồ sơ để xem rà soát pháp chế.";
    }
  }
  if (error instanceof LegalParseError) return error.message;
  return getVietnameseApiError(error);
}
