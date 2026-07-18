// -----------------------------------------------------------------------------
// Core fixture handlers: cases, upload, document review + confirmation,
// evidence/conflicts, intake completion, handoff, audit timeline, work queue.
//
// These back the critical path: create case → intake → upload → review →
// accept/correct/reject → gap/conflict → handoff. Responses mirror the exact
// DTO shapes in lib/api/contracts.ts.
// -----------------------------------------------------------------------------

import type {
  CompleteUploadResponseDto,
  ConfirmDocumentRequestDto,
  ConfirmedFactDto,
  DocumentReviewDto,
  UploadIntentDto,
} from "../../api/contracts";
import type { FixtureStore } from "../store";
import { apiError, type FixtureHandler, type FixtureRequest, type FixtureResponse } from "../types";

// segments = ["api","v1", ...]. seg(2) is the resource root.
const seg = (r: FixtureRequest, i: number): string | undefined => r.segments[i];
const ok = (body: unknown, status = 200): FixtureResponse => ({ status, body });

function mutationForbidden(store: FixtureStore): FixtureResponse | null {
  return store.flags.unauthorized
    ? apiError(403, "FORBIDDEN", "Bạn không có quyền thực hiện thao tác này trên hồ sơ.")
    : null;
}

const listCases: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 2) !== "cases" || r.segments.length !== 3) return null;
  return ok({
    items: [store.case],
    nextCursor: null,
    capabilities: { canCreateCase: !store.flags.unauthorized },
  });
};

const createCase: FixtureHandler = (r, store) => {
  if (r.method !== "POST" || seg(r, 2) !== "cases" || r.segments.length !== 3) return null;
  const forbidden = mutationForbidden(store);
  if (forbidden) return forbidden;
  const body = (r.body ?? {}) as { requestedAmount?: string; purpose?: string };
  store.case = {
    ...store.case,
    requestedAmount: body.requestedAmount ?? store.case.requestedAmount,
    purpose: body.purpose ?? store.case.purpose,
  };
  store.recordAudit({
    eventType: "CASE_CREATED",
    actorType: "HUMAN",
    actorId: store.case.assignedOfficerId,
    artifactType: "CASE",
    artifactId: store.case.id,
    eventData: { requestedAmount: store.case.requestedAmount },
  });
  return ok(store.case, 201);
};

const getCase: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 2) !== "cases" || r.segments.length !== 4) return null;
  if (seg(r, 3) !== store.case.id) {
    return apiError(404, "CASE_NOT_FOUND", "Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.");
  }
  return ok(store.case);
};

const createUploadIntent: FixtureHandler = (r, store) => {
  if (r.method !== "POST" || seg(r, 4) !== "upload-intents") return null;
  const forbidden = mutationForbidden(store);
  if (forbidden) return forbidden;
  const intentId = store.nextId("intent");
  const origin = typeof location !== "undefined" ? location.origin : "http://localhost";
  const intent: UploadIntentDto = {
    mode: "SIGNED",
    method: "PUT",
    intentId,
    expiresAt: store.now(),
    // Same-origin fixture storage URL; the interceptor's XHR shim answers it.
    uploadUrl: `${origin}/api/creditops/__fixture_storage/${intentId}`,
    headers: {},
  };
  return ok(intent, 201);
};

const completeUpload: FixtureHandler = (r, store) => {
  if (r.method !== "POST" || seg(r, 2) !== "upload-intents" || seg(r, 4) !== "complete") return null;
  const forbidden = mutationForbidden(store);
  if (forbidden) return forbidden;
  const documentId = store.nextId("doc");
  const documentVersionId = store.nextId("dv");
  const doc: DocumentReviewDto = {
    documentId,
    caseId: store.case.id,
    documentVersionId,
    documentVersion: 1,
    stage: "READY_FOR_OFFICER_REVIEW",
    fileName: "tai-lieu-tai-len.pdf",
    pageCount: 3,
    candidates: [
      {
        id: store.nextId("cf"),
        caseId: store.case.id,
        caseVersion: store.case.version,
        documentVersionId,
        fieldKey: "collateral_value",
        proposedValue: "8000000000",
        confidence: 0.86,
        source: { page: 1, x: 0.1, y: 0.2, width: 0.4, height: 0.05 },
      },
    ],
  };
  store.documents.set(documentId, doc);
  store.recordAudit({
    eventType: "DOCUMENT_INGESTED",
    actorType: "AGENT",
    actorId: "agent-intake",
    artifactType: "DOCUMENT",
    artifactId: documentId,
    eventData: { stage: "READY_FOR_OFFICER_REVIEW" },
  });
  const body: CompleteUploadResponseDto = {
    outcome: "REGISTERED",
    documentId,
    documentVersionId,
    task: { id: store.nextId("task"), status: "SUCCEEDED" },
  };
  return ok(body, 201);
};

const getDocumentReview: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 2) !== "documents" || seg(r, 4) !== "review") return null;
  const doc = store.documents.get(seg(r, 3) ?? "");
  if (!doc) return apiError(404, "DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.");
  return ok(doc);
};

const confirmDocument: FixtureHandler = (r, store) => {
  if (r.method !== "POST" || seg(r, 2) !== "documents" || seg(r, 4) !== "confirmations") return null;
  const forbidden = mutationForbidden(store);
  if (forbidden) return forbidden;
  const doc = store.documents.get(seg(r, 3) ?? "");
  if (!doc) return apiError(404, "DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.");
  const body = (r.body ?? {}) as ConfirmDocumentRequestDto;
  if (store.flags.staleGuard || body.expectedDocumentVersion !== doc.documentVersion) {
    return apiError(409, "STALE_DOCUMENT_VERSION", "Phiên bản tài liệu đã thay đổi. Vui lòng tải lại.", {
      details: { expected: doc.documentVersion, received: body.expectedDocumentVersion },
    });
  }
  for (const disposition of body.dispositions ?? []) {
    if (disposition.disposition !== "ACCEPTED" && disposition.disposition !== "CORRECTED") continue;
    const candidate = doc.candidates.find((c) => c.id === disposition.candidateId);
    if (!candidate) continue;
    const value = disposition.disposition === "CORRECTED" ? disposition.correctedValue ?? "" : String(candidate.proposedValue);
    const fact: ConfirmedFactDto = {
      id: store.nextId("ev"),
      caseId: store.case.id,
      caseVersion: store.case.version,
      candidateId: candidate.id,
      confirmationId: store.nextId("cfm"),
      documentVersionId: candidate.documentVersionId,
      fieldKey: candidate.fieldKey,
      value,
      candidateValue: candidate.proposedValue,
      source: candidate.source,
      confirmedAt: store.now(),
      stale: false,
    };
    if (!store.evidence.some((e) => e.candidateId === candidate.id)) store.evidence.push(fact);
  }
  store.bumpCaseVersion();
  store.recordAudit({
    eventType: "CONFIRMATION_RECORDED",
    actorType: "HUMAN",
    actorId: store.case.assignedOfficerId,
    artifactType: "DOCUMENT",
    artifactId: doc.documentId,
    eventData: { dispositionCount: (body.dispositions ?? []).length },
  });
  return ok(null, 201);
};

const listEvidence: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 4) !== "evidence") return null;
  return ok({ items: store.evidence });
};

const listConflicts: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 4) !== "conflicts") return null;
  return ok({ items: store.conflicts });
};

const completeIntake: FixtureHandler = (r, store) => {
  if (r.method !== "POST" || seg(r, 4) !== "intake-completion") return null;
  const forbidden = mutationForbidden(store);
  if (forbidden) return forbidden;
  const reasons = [
    ...store.conflicts.map((c) => `Mâu thuẫn ở trường ${c.fieldKey} chưa được xử lý`),
    ...(store.flags.intakeIncompleteReasons ?? []),
  ];
  if (reasons.length > 0 && !store.intakeComplete) {
    store.recordAudit({
      eventType: "INTAKE_INCOMPLETE_BLOCKED",
      actorType: "SYSTEM",
      artifactType: "CASE",
      artifactId: store.case.id,
      eventData: { unresolvedCount: reasons.length },
    });
    return apiError(409, "INTAKE_INCOMPLETE", "Chưa thể hoàn tất tiếp nhận: còn mục chưa xử lý.", {
      details: { reasons, unresolvedCount: reasons.length },
    });
  }
  if (!store.handoff) {
    store.handoff = {
      handoffId: store.nextId("ho"),
      state: "READY_FOR_SPECIALIST_REVIEW",
      caseVersion: store.case.version,
      createdAt: store.now(),
    };
  }
  const created = !store.intakeComplete;
  store.intakeComplete = true;
  if (created) {
    store.recordAudit({
      eventType: "INTAKE_COMPLETED",
      actorType: "HUMAN",
      actorId: store.case.assignedOfficerId,
      artifactType: "HANDOFF",
      artifactId: store.handoff.handoffId,
    });
  }
  return ok(
    {
      handoffId: store.handoff.handoffId,
      caseVersion: store.handoff.caseVersion,
      state: store.handoff.state,
      created,
    },
    created ? 201 : 200,
  );
};

const getHandoff: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 4) !== "handoffs") return null;
  if (!store.handoff) return apiError(404, "HANDOFF_NOT_AVAILABLE", "Chưa có bàn giao cho hồ sơ này.");
  return ok(store.handoff);
};

const listAudit: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 4) !== "audit-events") return null;
  return ok({ events: store.auditEvents, nextCursor: null });
};

const listWorkItems: FixtureHandler = (r, store) => {
  if (r.method !== "GET" || seg(r, 2) !== "work-items") return null;
  const items: unknown[] = [];
  const base = { caseId: store.case.id, caseVersion: store.case.version, createdAt: store.now() };
  if (store.conflicts.length > 0) {
    items.push({
      ...base,
      kind: "INTAKE_INCOMPLETE",
      titleVi: "Xử lý mâu thuẫn chứng cứ",
      reasonVi: `Còn ${store.conflicts.length} mâu thuẫn cần con người xử lý`,
      severity: "BLOCKING",
      primaryRoute: `/ho-so/${store.case.id}/doi-chieu`,
    });
  }
  if (!store.intakeComplete && (store.flags.intakeIncompleteReasons?.length ?? 0) > 0) {
    items.push({
      ...base,
      kind: "INTAKE_INCOMPLETE",
      titleVi: "Bổ sung tài liệu còn thiếu",
      reasonVi: store.flags.intakeIncompleteReasons?.[0] ?? "Còn khoảng trống chứng cứ",
      severity: "ATTENTION",
      primaryRoute: `/ho-so/${store.case.id}/khoang-trong`,
    });
  }
  if (store.intakeComplete) {
    items.push({
      ...base,
      kind: "RISK_DISPOSITION_PENDING",
      titleVi: "Tiếp tục thẩm định hồ sơ",
      reasonVi: "Hồ sơ đã bàn giao, sẵn sàng cho bước tiếp theo",
      severity: "INFO",
      primaryRoute: `/ho-so/${store.case.id}/tham-dinh`,
    });
  }
  return ok({ items });
};

export const coreHandlers: readonly FixtureHandler[] = [
  listCases,
  createCase,
  getCase,
  createUploadIntent,
  completeUpload,
  getDocumentReview,
  confirmDocument,
  listEvidence,
  listConflicts,
  completeIntake,
  getHandoff,
  listAudit,
  listWorkItems,
];
