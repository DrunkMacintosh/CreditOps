export interface CaseCapabilities {
  canUpload: boolean;
  canConfirm: boolean;
  canCompleteIntake: boolean;
}

export interface CreditCaseDto {
  id: string;
  version: number;
  assignedOfficerId: string;
  requestedAmount: string | null;
  purpose: string | null;
  workflowState: string | null;
  updatedAt: string | null;
  capabilities: CaseCapabilities;
}

export interface CaseCollectionCapabilities {
  canCreateCase: boolean;
}

export interface CreditCaseListDto {
  items: CreditCaseDto[];
  nextCursor: string | null;
  capabilities: CaseCollectionCapabilities;
}

export interface CreateCaseRequestDto {
  requestedAmount: string;
  purpose: string;
}

export interface CreateUploadIntentRequestDto {
  fileName: string;
  contentType: string;
  sizeBytes: number;
}

interface UploadIntentBaseDto {
  intentId: string;
  expiresAt: string;
  uploadUrl: string;
  headers: Readonly<Record<string, string>>;
}

export interface SignedUploadIntentDto extends UploadIntentBaseDto {
  mode: "SIGNED";
  method: "POST" | "PUT";
}

export interface ResumableUploadIntentDto extends UploadIntentBaseDto {
  mode: "RESUMABLE";
}

export type UploadIntentDto = SignedUploadIntentDto | ResumableUploadIntentDto;

export type TaskStatus =
  | "PENDING"
  | "RUNNING"
  | "RETRY_WAIT"
  | "SUCCEEDED"
  | "FAILED_MANUAL_REVIEW"
  | "SUPERSEDED";

export interface TaskStatusDto {
  id: string;
  status: TaskStatus;
}

export interface DuplicateUploadResponseDto {
  outcome: "DUPLICATE";
  duplicateOfDocumentId: string;
}

export interface RegisteredUploadResponseDto {
  outcome: "REGISTERED";
  documentId: string;
  documentVersionId: string;
  task: TaskStatusDto;
}

export type CompleteUploadResponseDto =
  | DuplicateUploadResponseDto
  | RegisteredUploadResponseDto;

export interface ApiErrorDto {
  code: string;
  messageVi: string;
  correlationId: string | null;
  retryable: boolean;
}

// ---------------------------------------------------------------------------
// Officer review workspace (document review, evidence, conflicts)
//
// The four endpoint paths below are CANONICAL per plan Task 8; the backend that
// serves them is pending (plan Tasks 8–9), so the UI fails closed at runtime.
// The `rationale` field on a corrected disposition and the exact conflict wire
// shape are PROPOSED pending the canonical OpenAPI (plan Tasks 8–9); this
// compatibility boundary normalizes them here so downstream UI does not.
// ---------------------------------------------------------------------------

export type FactDisposition = "ACCEPTED" | "CORRECTED" | "ABSENT" | "UNREADABLE";

export type DocumentStage =
  | "REGISTERED"
  | "SECURITY_VALIDATED"
  | "PARSED"
  | "CLASSIFIED"
  | "EXTRACTED"
  | "INDEXED"
  | "READY_FOR_OFFICER_REVIEW";

export interface PageRegionDto {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CandidateFactDto {
  id: string;
  caseId: string;
  caseVersion: number;
  documentVersionId: string;
  fieldKey: string;
  proposedValue: string | number | boolean;
  confidence: number;
  source: PageRegionDto;
}

export interface DocumentReviewDto {
  documentId: string;
  caseId: string;
  documentVersionId: string;
  documentVersion: number; // used as expectedDocumentVersion on confirm
  stage: DocumentStage;
  fileName: string | null; // optional metadata, null when absent
  pageCount: number | null;
  candidates: CandidateFactDto[];
}

export interface CandidateDispositionDto {
  candidateId: string;
  disposition: FactDisposition;
  correctedValue?: string; // REQUIRED iff disposition === "CORRECTED"
  rationale?: string; // REQUIRED iff CORRECTED (PROPOSED wire field)
}

export interface ConfirmDocumentRequestDto {
  expectedDocumentVersion: number;
  dispositions: CandidateDispositionDto[];
}

export interface ConfirmedFactDto {
  id: string;
  caseId: string;
  caseVersion: number;
  candidateId: string;
  confirmationId: string;
  documentVersionId: string;
  fieldKey: string;
  value: string | number | boolean;
  candidateValue: string | number | boolean;
  source: PageRegionDto;
  confirmedAt: string;
  stale: boolean; // defaults false when absent
}

export interface ConflictSourceDto {
  documentVersionId: string;
  value: string | number | boolean;
  source: PageRegionDto | null; // null when the wire omits it
}

export interface ConflictDto {
  id: string;
  caseId: string;
  caseVersion: number;
  fieldKey: string;
  sources: ConflictSourceDto[]; // a conflict preserves every source (>= 2)
  detectedAt: string | null;
  stale: boolean;
}

export interface EvidenceListDto {
  items: ConfirmedFactDto[];
}

export interface ConflictListDto {
  items: ConflictDto[];
}

export interface CaseApi {
  listCases(): Promise<CreditCaseListDto>;
  getCase(caseId: string): Promise<CreditCaseDto>;
  createCase(request: CreateCaseRequestDto): Promise<CreditCaseDto>;
}

export interface UploadApi {
  createUploadIntent(
    caseId: string,
    request: CreateUploadIntentRequestDto,
  ): Promise<UploadIntentDto>;
  completeUploadIntent(
    intentId: string,
    idempotencyKey: string,
  ): Promise<CompleteUploadResponseDto>;
}

export interface ReviewApi {
  getDocumentReview(documentId: string): Promise<DocumentReviewDto>;
  confirmDocument(
    documentId: string,
    request: ConfirmDocumentRequestDto,
  ): Promise<void>;
  listEvidence(caseId: string): Promise<EvidenceListDto>;
  listConflicts(caseId: string): Promise<ConflictListDto>;
}

export type CreditOpsApi = CaseApi & UploadApi & ReviewApi;
