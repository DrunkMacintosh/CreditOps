import { describe, expect, it } from "vitest";

import {
  parseCandidateFact,
  parseCompleteUpload,
  parseConfirmedFact,
  parseConflict,
  parseConflictList,
  parseCreditCase,
  parseCreditCaseList,
  parseDocumentReview,
  parseEvidenceList,
  parsePageRegion,
  parseUploadIntent,
} from "../../lib/api/schemas";

const rawCase = {
  id: "case-synthetic",
  version: 1,
  assignedOfficerId: "officer-synthetic",
  requestedAmount: "5000000000",
  purpose: "Nhu cầu vốn lưu động tổng hợp",
  capabilities: {
    canUpload: true,
    canConfirm: true,
    canCompleteIntake: false,
  },
};

describe("case schemas", () => {
  it("requires every per-case capability instead of defaulting missing authority", () => {
    expect(() =>
      parseCreditCase({
        ...rawCase,
        capabilities: { canConfirm: true, canCompleteIntake: true },
      }),
    ).toThrow(/canUpload/);
  });

  it("requires an envelope-level canCreateCase capability", () => {
    expect(() => parseCreditCaseList({ items: [rawCase], nextCursor: null })).toThrow(
      /canCreateCase/,
    );
    expect(() => parseCreditCaseList([rawCase])).toThrow(/danh sách hồ sơ/);
  });

  it("normalizes the strict collection envelope", () => {
    expect(
      parseCreditCaseList({
        items: [rawCase],
        nextCursor: null,
        capabilities: { canCreateCase: false },
      }),
    ).toMatchObject({
      items: [{ id: "case-synthetic" }],
      nextCursor: null,
      capabilities: { canCreateCase: false },
    });
  });
});

describe("upload schemas", () => {
  it.each([
    { mode: "signed", method: "PUT" },
    { mode: "SIGNED", method: "put" },
    { mode: "resumable" },
  ])("rejects non-canonical upload discriminants %#", (variant) => {
    expect(() =>
      parseUploadIntent({
        ...variant,
        intentId: "intent-canonical",
        expiresAt: "2099-01-01T00:00:00Z",
        uploadUrl: "https://storage.invalid/object",
        headers: {
          "Upload-Metadata": "bucketName cHJpdmF0ZQ==,objectName b3BhcXVl",
        },
      }),
    ).toThrow();
  });

  it("requires an explicit signed method", () => {
    expect(() =>
      parseUploadIntent({
        mode: "SIGNED",
        intentId: "intent-1",
        expiresAt: "2099-01-01T00:00:00Z",
        uploadUrl: "https://storage.invalid/object",
        headers: {},
      }),
    ).toThrow(/phương thức/i);
  });

  it("requires resumable object binding in a case-insensitive Upload-Metadata header", () => {
    const base = {
      mode: "RESUMABLE",
      intentId: "intent-2",
      expiresAt: "2099-01-01T00:00:00Z",
      uploadUrl: "https://storage.invalid/tus",
      headers: { authorization: "temporary" },
    };
    expect(() => parseUploadIntent(base)).toThrow(/bucketName.*objectName/i);
    expect(
      parseUploadIntent({
        ...base,
        headers: {
          authorization: "temporary",
          "upload-metadata": "bucketName cHJpdmF0ZQ==,objectName b3BhcXVl",
        },
      }),
    ).toMatchObject({ mode: "RESUMABLE", intentId: "intent-2" });
  });

  it("rejects any direct-upload authorization that enables upsert", () => {
    expect(() =>
      parseUploadIntent({
        mode: "SIGNED",
        intentId: "intent-upsert",
        expiresAt: "2099-01-01T00:00:00Z",
        uploadUrl: "https://storage.invalid/object",
        method: "PUT",
        headers: { "x-upsert": "true" },
      }),
    ).toThrow(/upsert/i);
  });

  it("accepts only an exact duplicate or registered completion variant", () => {
    expect(
      parseCompleteUpload({
        outcome: "DUPLICATE",
        duplicateOfDocumentId: "document-existing",
      }),
    ).toEqual({ outcome: "DUPLICATE", duplicateOfDocumentId: "document-existing" });
    expect(
      parseCompleteUpload({
        outcome: "REGISTERED",
        documentId: "document-1",
        documentVersionId: "version-1",
        task: { id: "task-1", status: "PENDING" },
      }),
    ).toMatchObject({ outcome: "REGISTERED", documentId: "document-1" });
  });

  it.each([
    {},
    { outcome: "DUPLICATE", duplicateOfDocumentId: "existing", documentId: "new" },
    {
      outcome: "REGISTERED",
      documentId: "new",
      documentVersionId: "version",
      duplicateOfDocumentId: "existing",
      task: { id: "task", status: "PENDING" },
    },
    { outcome: "REGISTERED", documentId: "new", documentVersionId: "version" },
  ])("rejects ambiguous completion shape %#", (value) => {
    expect(() => parseCompleteUpload(value)).toThrow();
  });
});

function omit(source: Record<string, unknown>, ...keys: string[]): Record<string, unknown> {
  const clone = { ...source };
  for (const key of keys) delete clone[key];
  return clone;
}

const region = { page: 1, x: 0.1, y: 0.1, width: 0.2, height: 0.2 };

const rawCandidate = {
  id: "candidate-1",
  caseId: "case-1",
  caseVersion: 2,
  documentVersionId: "docver-1",
  fieldKey: "requested_amount",
  proposedValue: "5000000000",
  confidence: 0.87,
  source: region,
};

describe("page region schema", () => {
  it("accepts a normalized region", () => {
    expect(parsePageRegion(region)).toEqual(region);
  });

  it.each([
    { ...region, x: 0.9, width: 0.2 }, // x + width > 1
    { ...region, y: 0.95, height: 0.2 }, // y + height > 1
    { ...region, page: 0 }, // page must be >= 1
    { ...region, width: 0 }, // width must be > 0
    { ...region, height: 0 }, // height must be > 0
    { ...region, x: -0.1 }, // x below the unit interval
    { ...region, x: Number.POSITIVE_INFINITY }, // non-finite rejected
  ])("rejects an out-of-bounds region %#", (value) => {
    expect(() => parsePageRegion(value)).toThrow();
  });
});

describe("candidate fact schema", () => {
  it("parses every required candidate field", () => {
    expect(parseCandidateFact(rawCandidate)).toMatchObject({
      id: "candidate-1",
      proposedValue: "5000000000",
      confidence: 0.87,
      source: region,
    });
  });

  it.each([
    { ...rawCandidate, confidence: 1.2 }, // confidence above 1
    { ...rawCandidate, confidence: -0.1 }, // confidence below 0
    { ...rawCandidate, proposedValue: { nested: true } }, // object value fails closed
    { ...rawCandidate, proposedValue: null }, // null value fails closed
  ])("rejects an invalid candidate %#", (value) => {
    expect(() => parseCandidateFact(value)).toThrow();
  });

  it("accepts boolean and numeric proposed values", () => {
    expect(parseCandidateFact({ ...rawCandidate, proposedValue: true }).proposedValue).toBe(true);
    expect(parseCandidateFact({ ...rawCandidate, proposedValue: 42 }).proposedValue).toBe(42);
  });
});

describe("document review schema", () => {
  const rawReview = {
    documentId: "document-1",
    caseId: "case-1",
    documentVersionId: "docver-1",
    documentVersion: 3,
    stage: "READY_FOR_OFFICER_REVIEW",
    fileName: "tong-hop.pdf",
    pageCount: 4,
    candidates: [rawCandidate],
  };

  it("parses the canonical review payload", () => {
    expect(parseDocumentReview(rawReview)).toMatchObject({
      documentId: "document-1",
      documentVersion: 3,
      stage: "READY_FOR_OFFICER_REVIEW",
      fileName: "tong-hop.pdf",
      pageCount: 4,
      candidates: [{ id: "candidate-1" }],
    });
  });

  it("rejects an unknown document stage (enum freeze)", () => {
    expect(() => parseDocumentReview({ ...rawReview, stage: "ARCHIVED" })).toThrow();
  });

  it("rejects a review missing the document version", () => {
    expect(() => parseDocumentReview(omit(rawReview, "documentVersion"))).toThrow();
  });

  it("accepts the expectedDocumentVersion alias", () => {
    expect(
      parseDocumentReview({
        ...omit(rawReview, "documentVersion"),
        expectedDocumentVersion: 7,
      }).documentVersion,
    ).toBe(7);
  });

  it("tolerates missing metadata as null and an empty candidate list", () => {
    expect(
      parseDocumentReview({ ...omit(rawReview, "fileName", "pageCount"), candidates: [] }),
    ).toMatchObject({
      fileName: null,
      pageCount: null,
      candidates: [],
    });
  });
});

describe("confirmed fact and evidence schema", () => {
  const rawConfirmed = {
    id: "confirmed-1",
    caseId: "case-1",
    caseVersion: 2,
    candidateId: "candidate-1",
    confirmationId: "confirmation-1",
    documentVersionId: "docver-1",
    fieldKey: "requested_amount",
    value: "5000000000",
    candidateValue: "5000000000",
    source: region,
    confirmedAt: "2026-07-18T09:00:00Z",
  };

  it("defaults stale to false when the wire omits it", () => {
    expect(parseConfirmedFact(rawConfirmed).stale).toBe(false);
    expect(parseConfirmedFact({ ...rawConfirmed, stale: true }).stale).toBe(true);
  });

  it("accepts the items list and tolerates the facts alias", () => {
    expect(parseEvidenceList({ items: [rawConfirmed] }).items).toHaveLength(1);
    expect(parseEvidenceList({ facts: [rawConfirmed] }).items).toHaveLength(1);
  });
});

describe("conflict schema", () => {
  const source = { documentVersionId: "docver-1", value: "5000000000", source: region };
  const rawConflict = {
    id: "conflict-1",
    caseId: "case-1",
    caseVersion: 2,
    fieldKey: "requested_amount",
    sources: [source, { documentVersionId: "docver-2", value: "6000000000", source: null }],
    detectedAt: "2026-07-18T09:00:00Z",
    stale: false,
  };

  it("preserves every source in a conflict", () => {
    expect(parseConflict(rawConflict).sources).toHaveLength(2);
  });

  it("rejects a conflict with a single source (never a resolved winner)", () => {
    expect(() => parseConflict({ ...rawConflict, sources: [source] })).toThrow();
  });

  it("parses a conflict list envelope", () => {
    expect(parseConflictList({ items: [rawConflict] }).items).toHaveLength(1);
  });
});
