// Self-contained API bindings for the read-only underwriting (Thẩm định)
// screen. This module deliberately does not touch the shared client/contracts/
// schemas modules: it mirrors their conventions (BFF base "/api/creditops",
// ApiClientError-style typed errors, cache: "no-store", credentials: "include")
// while owning the underwriting response shape end to end.
//
// The backend response (services/api/src/creditops/api/underwriting.py) wraps a
// snake_case `assessment` object — the persisted `UnderwritingAssessment`
// dumped with pydantic `model_dump(mode="json")`, so every Decimal arrives as a
// string. We parse those strings into numbers for display while keeping the raw
// text as an exact fallback. The maker output carries NO decision field by
// construction; nothing here invents one.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";

/** The upstream 404 raised when no assessment has been produced yet. */
export const UNDERWRITING_NOT_AVAILABLE = "UNDERWRITING_NOT_AVAILABLE";

export type CalculatorOutcome =
  | { readonly status: "COMPUTED"; readonly value: number; readonly raw: string }
  | { readonly status: "NOT_COMPUTABLE"; readonly reason: string };

export type FactRefKind = "CONFIRMED_FACT" | "DOCUMENT_REGION";

export interface FactRef {
  readonly kind: FactRefKind;
  readonly refId: string;
}

export interface CalculatorInputValue {
  readonly name: string;
  readonly value: number | null;
  readonly raw: string | null;
  readonly factRefs: readonly FactRef[];
}

export interface CalculatorResult {
  readonly resultId: string;
  readonly calculator: string;
  readonly inputs: readonly CalculatorInputValue[];
  readonly outcome: CalculatorOutcome;
}

export interface TrendPoint {
  readonly period: string;
  readonly value: number | null;
  readonly raw: string | null;
  readonly factRefs: readonly FactRef[];
}

export interface TrendStep {
  readonly fromPeriod: string;
  readonly toPeriod: string;
  readonly delta: CalculatorOutcome;
  readonly growthRate: CalculatorOutcome;
}

export interface TrendResult {
  readonly resultId: string;
  readonly metric: string;
  readonly points: readonly TrendPoint[];
  readonly steps: readonly TrendStep[];
}

export interface ScenarioAdjustment {
  readonly metric: string;
  readonly relativeChange: number | null;
  readonly absoluteChange: number | null;
}

export interface ScenarioMetricOutcome {
  readonly metric: string;
  readonly base: CalculatorOutcome;
  readonly adjusted: CalculatorOutcome;
}

export interface ScenarioResult {
  readonly resultId: string;
  readonly scenarioName: string;
  readonly adjustments: readonly ScenarioAdjustment[];
  readonly metrics: readonly ScenarioMetricOutcome[];
}

export type CitationKind =
  | "CONFIRMED_FACT"
  | "CALCULATOR_RESULT"
  | "DOCUMENT_REGION";

export interface Citation {
  readonly kind: CitationKind;
  readonly confirmedFactId?: string;
  readonly resultId?: string;
  readonly documentVersionId?: string;
  readonly region?: string;
}

export type ConfidenceLevel = "HIGH" | "MEDIUM" | "LOW";

export interface Finding {
  readonly statementVi: string;
  readonly citations: readonly Citation[];
  readonly confidence: ConfidenceLevel;
  readonly uncertaintyVi: string;
}

export interface AssessmentSection {
  readonly findings: readonly Finding[];
}

export interface RepaymentSourceSection {
  readonly findings: readonly Finding[];
  readonly downsideScenarios: readonly Finding[];
}

export interface ProposedStructureSection {
  readonly instrumentVi: string;
  readonly proposedAmountVnd: number | null;
  readonly proposedAmountRaw: string | null;
  readonly tenorMonths: number | null;
  readonly findings: readonly Finding[];
}

export interface RiskItem {
  readonly riskId: string;
  readonly descriptionVi: string;
  readonly citations: readonly Citation[];
  readonly confidence: ConfidenceLevel;
  readonly uncertaintyVi: string;
}

export interface AssumptionItem {
  readonly statementVi: string;
  readonly rationaleVi: string;
  readonly basisCitations: readonly Citation[];
}

export type GapBlockingLevel = "BLOCKING" | "CONDITIONAL" | "CLARIFICATION";

export interface EvidenceGapItem {
  readonly missingInformationVi: string;
  readonly whyNeededVi: string;
  readonly blockingLevel: GapBlockingLevel;
  readonly suggestedEvidenceVi: readonly string[];
}

export interface AssessmentProvenance {
  readonly caseId: string;
  readonly caseVersion: number | null;
  readonly agentRole: string;
  readonly executionId: string;
  readonly taskId: string;
  readonly promptVersion: string;
  readonly modelId: string;
  readonly endpointId: string;
  readonly evidenceViewBuiltAt: string;
  readonly createdAt: string;
}

export interface Assessment {
  readonly id: string;
  readonly provenance: AssessmentProvenance | null;
  readonly business: AssessmentSection;
  readonly financial: AssessmentSection;
  readonly cashFlow: AssessmentSection;
  readonly repaymentSource: RepaymentSourceSection;
  readonly proposedStructure: ProposedStructureSection | null;
  readonly risks: readonly RiskItem[];
  readonly mitigants: readonly RiskItem[];
  readonly assumptions: readonly AssumptionItem[];
  readonly evidenceGaps: readonly EvidenceGapItem[];
  readonly calculatorResults: readonly CalculatorResult[];
  readonly trendResults: readonly TrendResult[];
  readonly scenarioResults: readonly ScenarioResult[];
}

export interface HandoffStatus {
  readonly handoffId: string;
  readonly state: string;
  readonly createdAt: string;
}

export interface UnderwritingAssessmentView {
  readonly assessmentId: string;
  readonly caseId: string;
  readonly caseVersion: number;
  readonly agentRole: string;
  readonly executionId: string;
  readonly promptVersion: string;
  readonly createdAt: string;
  readonly assessment: Assessment;
  readonly handoff: HandoffStatus | null;
}

interface ApiErrorBody {
  readonly code?: unknown;
  readonly messageVi?: unknown;
  readonly retryable?: unknown;
}

export async function fetchUnderwritingAssessment(
  caseId: string,
  fetcher: typeof fetch = fetch,
): Promise<UnderwritingAssessmentView> {
  const response = await fetcher(
    `${BFF_BASE_URL}/api/v1/cases/${encodeURIComponent(caseId)}/underwriting`,
    {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "include",
      cache: "no-store",
    },
  );
  const body = await readJson(response);
  if (!response.ok) {
    const error = body as ApiErrorBody | null;
    throw new ApiClientError(
      response.status,
      typeof error?.code === "string" ? error.code : "REQUEST_FAILED",
      typeof error?.messageVi === "string" && error.messageVi.length > 0
        ? error.messageVi
        : "Không đọc được bản phân tích thẩm định.",
      typeof error?.retryable === "boolean"
        ? error.retryable
        : response.status >= 500,
    );
  }
  return parseUnderwritingAssessment(body);
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

// --- parsing ---------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Phản hồi thẩm định không đúng định dạng.");
  }
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function requiredStr(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Phản hồi thẩm định thiếu ${label}.`);
  }
  return value;
}

function intOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// Decimals arrive as JSON strings; keep the raw text and a parsed number.
function decimal(value: unknown): { value: number | null; raw: string | null } {
  if (typeof value === "number" && Number.isFinite(value)) {
    return { value, raw: String(value) };
  }
  if (typeof value === "string" && value.length > 0) {
    const parsed = Number(value);
    return { value: Number.isFinite(parsed) ? parsed : null, raw: value };
  }
  return { value: null, raw: null };
}

export function parseUnderwritingAssessment(
  body: unknown,
): UnderwritingAssessmentView {
  const root = asRecord(body);
  return {
    assessmentId: requiredStr(root.assessmentId, "mã bản phân tích"),
    caseId: str(root.caseId),
    caseVersion: intOrNull(root.caseVersion) ?? 0,
    agentRole: str(root.agentRole),
    executionId: str(root.executionId),
    promptVersion: str(root.promptVersion),
    createdAt: str(root.createdAt),
    assessment: parseAssessment(root.assessment),
    handoff: parseHandoff(root.handoff),
  };
}

function parseHandoff(value: unknown): HandoffStatus | null {
  if (value === null || value === undefined) return null;
  const record = asRecord(value);
  return {
    handoffId: str(record.handoffId),
    state: str(record.state),
    createdAt: str(record.createdAt),
  };
}

function parseAssessment(value: unknown): Assessment {
  const record = asRecord(value);
  return {
    id: str(record.id),
    provenance: parseProvenance(record.provenance),
    business: parseSection(record.business),
    financial: parseSection(record.financial),
    cashFlow: parseSection(record.cash_flow),
    repaymentSource: parseRepayment(record.repayment_source),
    proposedStructure: parseProposedStructure(record.proposed_structure),
    risks: asArray(record.risks).map(parseRiskItem),
    mitigants: asArray(record.mitigants).map(parseRiskItem),
    assumptions: asArray(record.assumptions).map(parseAssumption),
    evidenceGaps: asArray(record.evidence_gaps).map(parseGap),
    calculatorResults: asArray(record.calculator_results).map(parseCalculatorResult),
    trendResults: asArray(record.trend_results).map(parseTrendResult),
    scenarioResults: asArray(record.scenario_results).map(parseScenarioResult),
  };
}

function parseProvenance(value: unknown): AssessmentProvenance | null {
  if (value === null || value === undefined) return null;
  const record = asRecord(value);
  return {
    caseId: str(record.case_id),
    caseVersion: intOrNull(record.case_version),
    agentRole: str(record.agent_role),
    executionId: str(record.execution_id),
    taskId: str(record.task_id),
    promptVersion: str(record.prompt_version),
    modelId: str(record.model_id),
    endpointId: str(record.endpoint_id),
    evidenceViewBuiltAt: str(record.evidence_view_built_at),
    createdAt: str(record.created_at),
  };
}

function parseOutcome(value: unknown): CalculatorOutcome {
  const record = asRecord(value);
  if (record.status === "COMPUTED") {
    const { value: parsed, raw } = decimal(record.value);
    return { status: "COMPUTED", value: parsed ?? Number.NaN, raw: raw ?? "" };
  }
  return { status: "NOT_COMPUTABLE", reason: str(record.reason) };
}

function parseFactRef(value: unknown): FactRef {
  const record = asRecord(value);
  const kind = record.kind === "DOCUMENT_REGION" ? "DOCUMENT_REGION" : "CONFIRMED_FACT";
  return { kind, refId: str(record.ref_id) };
}

function parseCalculatorInput(value: unknown): CalculatorInputValue {
  const record = asRecord(value);
  const { value: parsed, raw } = decimal(record.value);
  return {
    name: str(record.name),
    value: parsed,
    raw,
    factRefs: asArray(record.fact_refs).map(parseFactRef),
  };
}

function parseCalculatorResult(value: unknown): CalculatorResult {
  const record = asRecord(value);
  return {
    resultId: str(record.result_id),
    calculator: str(record.calculator),
    inputs: asArray(record.inputs).map(parseCalculatorInput),
    outcome: parseOutcome(record.outcome),
  };
}

function parseTrendPoint(value: unknown): TrendPoint {
  const record = asRecord(value);
  const { value: parsed, raw } = decimal(record.value);
  return {
    period: str(record.period),
    value: parsed,
    raw,
    factRefs: asArray(record.fact_refs).map(parseFactRef),
  };
}

function parseTrendStep(value: unknown): TrendStep {
  const record = asRecord(value);
  return {
    fromPeriod: str(record.from_period),
    toPeriod: str(record.to_period),
    delta: parseOutcome(record.delta),
    growthRate: parseOutcome(record.growth_rate),
  };
}

function parseTrendResult(value: unknown): TrendResult {
  const record = asRecord(value);
  return {
    resultId: str(record.result_id),
    metric: str(record.metric),
    points: asArray(record.points).map(parseTrendPoint),
    steps: asArray(record.steps).map(parseTrendStep),
  };
}

function parseScenarioAdjustment(value: unknown): ScenarioAdjustment {
  const record = asRecord(value);
  return {
    metric: str(record.metric),
    relativeChange: decimal(record.relative_change).value,
    absoluteChange: decimal(record.absolute_change).value,
  };
}

function parseScenarioMetric(value: unknown): ScenarioMetricOutcome {
  const record = asRecord(value);
  return {
    metric: str(record.metric),
    base: parseOutcome(record.base),
    adjusted: parseOutcome(record.adjusted),
  };
}

function parseScenarioResult(value: unknown): ScenarioResult {
  const record = asRecord(value);
  return {
    resultId: str(record.result_id),
    scenarioName: str(record.scenario_name),
    adjustments: asArray(record.adjustments).map(parseScenarioAdjustment),
    metrics: asArray(record.metrics).map(parseScenarioMetric),
  };
}

function parseCitation(value: unknown): Citation {
  const record = asRecord(value);
  const kind = record.kind;
  if (kind === "CALCULATOR_RESULT") {
    return { kind, resultId: str(record.result_id) };
  }
  if (kind === "DOCUMENT_REGION") {
    return {
      kind,
      documentVersionId: str(record.document_version_id),
      region: str(record.region),
    };
  }
  return { kind: "CONFIRMED_FACT", confirmedFactId: str(record.confirmed_fact_id) };
}

function parseConfidence(value: unknown): ConfidenceLevel {
  return value === "HIGH" || value === "LOW" ? value : "MEDIUM";
}

function parseFinding(value: unknown): Finding {
  const record = asRecord(value);
  return {
    statementVi: str(record.statement_vi),
    citations: asArray(record.citations).map(parseCitation),
    confidence: parseConfidence(record.confidence),
    uncertaintyVi: str(record.uncertainty_vi),
  };
}

function parseSection(value: unknown): AssessmentSection {
  if (value === null || value === undefined) return { findings: [] };
  const record = asRecord(value);
  return { findings: asArray(record.findings).map(parseFinding) };
}

function parseRepayment(value: unknown): RepaymentSourceSection {
  if (value === null || value === undefined) {
    return { findings: [], downsideScenarios: [] };
  }
  const record = asRecord(value);
  return {
    findings: asArray(record.findings).map(parseFinding),
    downsideScenarios: asArray(record.downside_scenarios).map(parseFinding),
  };
}

function parseProposedStructure(value: unknown): ProposedStructureSection | null {
  if (value === null || value === undefined) return null;
  const record = asRecord(value);
  const amount = decimal(record.proposed_amount_vnd);
  return {
    instrumentVi: str(record.instrument_vi),
    proposedAmountVnd: amount.value,
    proposedAmountRaw: amount.raw,
    tenorMonths: intOrNull(record.tenor_months),
    findings: asArray(record.findings).map(parseFinding),
  };
}

function parseRiskItem(value: unknown): RiskItem {
  const record = asRecord(value);
  return {
    riskId: str(record.risk_id),
    descriptionVi: str(record.description_vi),
    citations: asArray(record.citations).map(parseCitation),
    confidence: parseConfidence(record.confidence),
    uncertaintyVi: str(record.uncertainty_vi),
  };
}

function parseAssumption(value: unknown): AssumptionItem {
  const record = asRecord(value);
  return {
    statementVi: str(record.statement_vi),
    rationaleVi: str(record.rationale_vi),
    basisCitations: asArray(record.basis_citations).map(parseCitation),
  };
}

function parseGapLevel(value: unknown): GapBlockingLevel {
  return value === "BLOCKING" || value === "CLARIFICATION" ? value : "CONDITIONAL";
}

function parseGap(value: unknown): EvidenceGapItem {
  const record = asRecord(value);
  return {
    missingInformationVi: str(record.missing_information_vi),
    whyNeededVi: str(record.why_needed_vi),
    blockingLevel: parseGapLevel(record.blocking_level),
    suggestedEvidenceVi: asArray(record.suggested_evidence_vi).map(str),
  };
}
