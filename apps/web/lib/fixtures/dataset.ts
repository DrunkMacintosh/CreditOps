// -----------------------------------------------------------------------------
// Synthetic dataset builders — one FixtureStore per scenario.
//
// A single coherent base "clean complete case" is defined once; each scenario
// is a small, legible delta on it. All values are synthetic. DTO shapes match
// lib/api/contracts.ts exactly so the real client stack parses them unchanged.
// -----------------------------------------------------------------------------

import type {
  CandidateFactDto,
  ConflictDto,
  ConfirmedFactDto,
  CreditCaseDto,
  DocumentReviewDto,
  HandoffDto,
} from "../api/contracts";
import { FixtureStore, type ScenarioFlags } from "./store";
import type { ScenarioId } from "./types";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const OFFICER_ID = "of-tran-bich-ngoc";
const DOC_ID = "doc-bctc-2025";
const DOC_VERSION_ID = "dv-bctc-2025-1";

function region(page: number): CandidateFactDto["source"] {
  return { page, x: 0.12, y: 0.34, width: 0.4, height: 0.05 };
}

function baseCase(overrides: Partial<CreditCaseDto> = {}): CreditCaseDto {
  return {
    id: CASE_ID,
    version: 3,
    assignedOfficerId: OFFICER_ID,
    requestedAmount: "5000000000",
    purpose: "Bổ sung vốn lưu động phục vụ hợp đồng cung cấp thiết bị",
    workflowState: "INTAKE_IN_PROGRESS",
    updatedAt: "2026-07-18T02:15:00.000Z",
    capabilities: { canUpload: true, canConfirm: true, canCompleteIntake: true },
    ...overrides,
  };
}

function baseCandidates(caseVersion: number): CandidateFactDto[] {
  return [
    {
      id: "cf-doanh-thu",
      caseId: CASE_ID,
      caseVersion,
      documentVersionId: DOC_VERSION_ID,
      fieldKey: "revenue_2025",
      proposedValue: "48200000000",
      confidence: 0.94,
      source: region(4),
    },
    {
      id: "cf-loi-nhuan",
      caseId: CASE_ID,
      caseVersion,
      documentVersionId: DOC_VERSION_ID,
      fieldKey: "net_profit_2025",
      proposedValue: "3100000000",
      confidence: 0.9,
      source: region(5),
    },
    {
      id: "cf-tong-tai-san",
      caseId: CASE_ID,
      caseVersion,
      documentVersionId: DOC_VERSION_ID,
      fieldKey: "total_assets_2025",
      proposedValue: "26500000000",
      confidence: 0.88,
      source: region(6),
    },
  ];
}

function baseDocument(
  caseVersion: number,
  overrides: Partial<DocumentReviewDto> = {},
): DocumentReviewDto {
  return {
    documentId: DOC_ID,
    caseId: CASE_ID,
    documentVersionId: DOC_VERSION_ID,
    documentVersion: 1,
    stage: "READY_FOR_OFFICER_REVIEW",
    fileName: "bao-cao-tai-chinh-2025.pdf",
    pageCount: 12,
    candidates: baseCandidates(caseVersion),
    ...overrides,
  };
}

function confirmedFact(
  candidateId: string,
  fieldKey: string,
  value: string,
  caseVersion: number,
  stale = false,
): ConfirmedFactDto {
  return {
    id: `ev-${candidateId}`,
    caseId: CASE_ID,
    caseVersion,
    candidateId,
    confirmationId: `cfm-${candidateId}`,
    documentVersionId: DOC_VERSION_ID,
    fieldKey,
    value,
    candidateValue: value,
    source: region(4),
    confirmedAt: "2026-07-18T02:20:00.000Z",
    stale,
  };
}

function handoff(caseVersion: number): HandoffDto {
  return {
    handoffId: `ho-${CASE_ID}`,
    // The state the ban-giao (handoff-summary) UI recognises for a completed
    // intake; any other value fail-closes to "Trạng thái chưa được hỗ trợ".
    state: "READY_FOR_SPECIALIST_REVIEW",
    caseVersion,
    createdAt: "2026-07-18T02:30:00.000Z",
  };
}

interface ScenarioSeed {
  flags?: ScenarioFlags;
  caseOverrides?: Partial<CreditCaseDto>;
  documents?: DocumentReviewDto[];
  evidence?: ConfirmedFactDto[];
  conflicts?: ConflictDto[];
  handoff?: HandoffDto | null;
  intakeComplete?: boolean;
}

const SEEDS: Record<ScenarioId, () => ScenarioSeed> = {
  "clean-complete": () => {
    const v = 3;
    return {
      documents: [baseDocument(v)],
      evidence: [
        confirmedFact("cf-doanh-thu", "revenue_2025", "48200000000", v),
        confirmedFact("cf-loi-nhuan", "net_profit_2025", "3100000000", v),
        confirmedFact("cf-tong-tai-san", "total_assets_2025", "26500000000", v),
      ],
      conflicts: [],
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "READY_FOR_UNDERWRITING" },
    };
  },

  "missing-documents": () => ({
    flags: {
      intakeIncompleteReasons: [
        "Thiếu sao kê ngân hàng 6 tháng gần nhất",
        "Thiếu hợp đồng đầu ra chứng minh nguồn trả nợ",
      ],
    },
    // Financial statement present; two required documents absent → open gaps.
    documents: [baseDocument(3)],
    evidence: [confirmedFact("cf-doanh-thu", "revenue_2025", "48200000000", 3)],
    conflicts: [],
    handoff: null,
    intakeComplete: false,
    caseOverrides: {
      workflowState: "INTAKE_IN_PROGRESS",
      capabilities: { canUpload: true, canConfirm: true, canCompleteIntake: false },
    },
  }),

  "conflicting-facts": () => {
    const v = 3;
    return {
      flags: {
        intakeIncompleteReasons: ["Còn mâu thuẫn doanh thu 2025 chưa được xử lý"],
      },
      documents: [baseDocument(v)],
      evidence: [confirmedFact("cf-loi-nhuan", "net_profit_2025", "3100000000", v)],
      conflicts: [
        {
          id: "conf-doanh-thu",
          caseId: CASE_ID,
          caseVersion: v,
          fieldKey: "revenue_2025",
          sources: [
            { documentVersionId: DOC_VERSION_ID, value: "48200000000", source: region(4) },
            { documentVersionId: "dv-to-khai-thue-1", value: "51900000000", source: region(2) },
          ],
          detectedAt: "2026-07-18T02:22:00.000Z",
          stale: false,
        },
      ],
      handoff: null,
      intakeComplete: false,
      caseOverrides: {
        capabilities: { canUpload: true, canConfirm: true, canCompleteIntake: false },
      },
    };
  },

  "document-quality": () => ({
    // A duplicate re-upload, an expired statement, and an unreadable scan.
    documents: [
      baseDocument(3),
      baseDocument(3, {
        documentId: "doc-scan-mo",
        documentVersionId: "dv-scan-mo-1",
        fileName: "sao-ke-ngan-hang-scan.pdf",
        stage: "PARSED",
        pageCount: null,
        candidates: [],
      }),
    ],
    evidence: [],
    conflicts: [],
    handoff: null,
    intakeComplete: false,
    caseOverrides: {
      capabilities: { canUpload: true, canConfirm: true, canCompleteIntake: false },
    },
  }),

  "risk-challenge": () => {
    const v = 4;
    return {
      documents: [baseDocument(v)],
      evidence: baseCandidates(v).map((c) =>
        confirmedFact(c.id, c.fieldKey, String(c.proposedValue), v),
      ),
      conflicts: [],
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "RISK_REVIEW", version: v },
    };
  },

  "downstream-stale": () => {
    const v = 5;
    return {
      documents: [baseDocument(v)],
      // New evidence arrived at v5; a fact confirmed at v4 is now stale.
      evidence: [
        confirmedFact("cf-doanh-thu", "revenue_2025", "48200000000", 4, true),
        confirmedFact("cf-loi-nhuan", "net_profit_2025", "3100000000", v),
      ],
      conflicts: [],
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "UNDERWRITING", version: v },
    };
  },

  "policy-unavailable": () => {
    const v = 4;
    return {
      flags: { policyUnavailable: true },
      documents: [baseDocument(v)],
      evidence: baseCandidates(v).map((c) =>
        confirmedFact(c.id, c.fieldKey, String(c.proposedValue), v),
      ),
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "LEGAL_REVIEW", version: v },
    };
  },

  "unauthorized-access": () => ({
    flags: { unauthorized: true },
    documents: [baseDocument(3)],
    evidence: [],
    conflicts: [],
    handoff: null,
    intakeComplete: false,
    caseOverrides: {
      assignedOfficerId: "of-nguoi-khac",
      capabilities: { canUpload: false, canConfirm: false, canCompleteIntake: false },
    },
  }),

  "pending-condition": () => {
    const v = 6;
    return {
      documents: [baseDocument(v)],
      evidence: baseCandidates(v).map((c) =>
        confirmedFact(c.id, c.fieldKey, String(c.proposedValue), v),
      ),
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "DISBURSEMENT_CONDITIONS", version: v },
    };
  },

  "execution-unknown": () => {
    const v = 7;
    return {
      documents: [baseDocument(v)],
      evidence: baseCandidates(v).map((c) =>
        confirmedFact(c.id, c.fieldKey, String(c.proposedValue), v),
      ),
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "DISBURSEMENT", version: v },
    };
  },

  "repayment-anomaly": () => {
    const v = 8;
    return {
      documents: [baseDocument(v)],
      evidence: baseCandidates(v).map((c) =>
        confirmedFact(c.id, c.fieldKey, String(c.proposedValue), v),
      ),
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "MONITORING", version: v },
    };
  },

  "settlement-recovery": () => {
    const v = 9;
    return {
      documents: [baseDocument(v)],
      evidence: baseCandidates(v).map((c) =>
        confirmedFact(c.id, c.fieldKey, String(c.proposedValue), v),
      ),
      handoff: handoff(v),
      intakeComplete: true,
      caseOverrides: { workflowState: "COLLECTIONS", version: v },
    };
  },
};

export const FIXTURE_CASE_ID = CASE_ID;
export const FIXTURE_DOCUMENT_ID = DOC_ID;

export function buildStore(scenarioId: ScenarioId): FixtureStore {
  const seed = SEEDS[scenarioId]();
  const caseDto = baseCase(seed.caseOverrides);
  const store = new FixtureStore({
    scenarioId,
    flags: seed.flags,
    case: caseDto,
    documents: seed.documents ?? [baseDocument(caseDto.version)],
    evidence: seed.evidence ?? [],
    conflicts: seed.conflicts ?? [],
    handoff: seed.handoff ?? null,
    intakeComplete: seed.intakeComplete ?? false,
  });
  // Seed a couple of audit events so the timeline is never empty on load.
  store.recordAudit({
    eventType: "CASE_CREATED",
    actorType: "HUMAN",
    actorId: OFFICER_ID,
    artifactType: "CASE",
    artifactId: CASE_ID,
    eventData: { synthetic: true },
  });
  store.recordAudit({
    eventType: "DOCUMENT_INGESTED",
    actorType: "AGENT",
    actorId: "agent-intake",
    artifactType: "DOCUMENT",
    artifactId: DOC_ID,
    eventData: { stage: "READY_FOR_OFFICER_REVIEW" },
  });
  // Pre-set stage slices so a focus-stage assertion reflects the safe invariant
  // on load (before the tester runs a Test control).
  if (scenarioId === "execution-unknown") {
    store.setSlice("disbursement", { state: "EXECUTION_UNKNOWN" });
  }
  return store;
}
