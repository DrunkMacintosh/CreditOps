import { describe, expect, it } from "vitest";

import { buildStore, FIXTURE_CASE_ID, FIXTURE_DOCUMENT_ID } from "../../../lib/fixtures/dataset";
import { dispatch } from "../../../lib/fixtures/router";
import type { FixtureRequest, HttpMethod } from "../../../lib/fixtures/types";

// Build a FixtureRequest by hand so dispatch() is exercised directly, with no
// fetch patching. The path is everything AFTER the /api/creditops prefix.
function req(
  method: HttpMethod,
  path: string,
  body: unknown = null,
  headers: Record<string, string> = {},
): FixtureRequest {
  const [rawPath, rawQuery = ""] = path.split("?");
  return {
    method,
    path: rawPath,
    segments: rawPath.split("/").filter(Boolean),
    query: new URLSearchParams(rawQuery),
    body,
    headers: new Headers(headers),
  };
}

describe("dispatch — clean-complete reads", () => {
  it("lists the fixture case with a create capability", () => {
    const store = buildStore("clean-complete");
    const res = dispatch(req("GET", "/api/v1/cases"), store);

    expect(res.status).toBe(200);
    const body = res.body as {
      items: { id: string }[];
      nextCursor: string | null;
      capabilities: { canCreateCase: boolean };
    };
    expect(Array.isArray(body.items)).toBe(true);
    expect(body.items[0].id).toBe(FIXTURE_CASE_ID);
    expect(body.nextCursor).toBeNull();
    expect(body.capabilities.canCreateCase).toBe(true);
  });

  it("returns the fixture case by its id", () => {
    const store = buildStore("clean-complete");
    const res = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}`), store);

    expect(res.status).toBe(200);
    expect((res.body as { id: string }).id).toBe(FIXTURE_CASE_ID);
  });

  it("hides an unknown case behind an ambiguous 404", () => {
    const store = buildStore("clean-complete");
    const res = dispatch(
      req("GET", "/api/v1/cases/99999999-9999-4999-8999-999999999999"),
      store,
    );

    expect(res.status).toBe(404);
    expect((res.body as { code: string }).code).toBe("CASE_NOT_FOUND");
  });

  it("returns the documented evidence / conflicts / handoff / audit shapes", () => {
    const store = buildStore("clean-complete");

    const evidence = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/evidence`), store);
    expect(evidence.status).toBe(200);
    expect(Array.isArray((evidence.body as { items: unknown[] }).items)).toBe(true);

    const conflicts = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/conflicts`), store);
    expect(conflicts.status).toBe(200);
    expect(Array.isArray((conflicts.body as { items: unknown[] }).items)).toBe(true);

    const handoffs = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/handoffs`), store);
    expect(handoffs.status).toBe(200);
    const handoff = handoffs.body as { handoffId: string; state: string; caseVersion: number };
    expect(handoff.handoffId).toBeTruthy();
    expect(handoff.state).toBe("READY_FOR_SPECIALIST_REVIEW");

    const audit = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/audit-events`), store);
    expect(audit.status).toBe(200);
    const auditBody = audit.body as { events: unknown[]; nextCursor: string | null };
    expect(Array.isArray(auditBody.events)).toBe(true);
    expect(auditBody.nextCursor).toBeNull();
  });

  it("reports intake completion as idempotent (created:false) once already complete", () => {
    const store = buildStore("clean-complete");
    const res = dispatch(
      req("POST", `/api/v1/cases/${FIXTURE_CASE_ID}/intake-completion`, {}),
      store,
    );

    expect(res.status).toBe(200);
    expect((res.body as { created: boolean }).created).toBe(false);
  });
});

describe("dispatch — intake gating", () => {
  it("blocks intake completion with reasons when documents are missing", () => {
    const store = buildStore("missing-documents");
    const res = dispatch(
      req("POST", `/api/v1/cases/${FIXTURE_CASE_ID}/intake-completion`, {}),
      store,
    );

    expect(res.status).toBe(409);
    const body = res.body as { code: string; details: { reasons: string[] } };
    expect(body.code).toBe("INTAKE_INCOMPLETE");
    expect(Array.isArray(body.details.reasons)).toBe(true);
    expect(body.details.reasons.length).toBeGreaterThan(0);
  });

  it("surfaces conflicts with at least two sources and blocks completion", () => {
    const store = buildStore("conflicting-facts");

    const conflicts = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/conflicts`), store);
    expect(conflicts.status).toBe(200);
    const items = (conflicts.body as { items: { sources: unknown[] }[] }).items;
    expect(items.length).toBeGreaterThan(0);
    for (const conflict of items) {
      expect(conflict.sources.length).toBeGreaterThanOrEqual(2);
    }

    const completion = dispatch(
      req("POST", `/api/v1/cases/${FIXTURE_CASE_ID}/intake-completion`, {}),
      store,
    );
    expect(completion.status).toBe(409);
    expect((completion.body as { code: string }).code).toBe("INTAKE_INCOMPLETE");
  });
});

describe("dispatch — unauthorized access", () => {
  it("forbids forced mutations but still serves the read and hides capabilities", () => {
    const store = buildStore("unauthorized-access");

    const upload = dispatch(
      req("POST", `/api/v1/cases/${FIXTURE_CASE_ID}/upload-intents`, {
        fileName: "x.pdf",
        contentType: "application/pdf",
        sizeBytes: 1,
      }),
      store,
    );
    expect(upload.status).toBe(403);
    expect((upload.body as { code: string }).code).toBe("FORBIDDEN");

    const confirm = dispatch(
      req("POST", `/api/v1/documents/${FIXTURE_DOCUMENT_ID}/confirmations`, {
        expectedDocumentVersion: 1,
        dispositions: [],
      }),
      store,
    );
    expect(confirm.status).toBe(403);
    expect((confirm.body as { code: string }).code).toBe("FORBIDDEN");

    const read = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}`), store);
    expect(read.status).toBe(200);
    expect((read.body as { capabilities: { canConfirm: boolean } }).capabilities.canConfirm).toBe(
      false,
    );
  });
});

describe("dispatch — confirmation flow", () => {
  it("accepts a matching document version and grows evidence, rejecting a stale version", () => {
    const store = buildStore("clean-complete");

    // Register a fresh document so its candidate is not already confirmed.
    const complete = dispatch(
      req("POST", "/api/v1/upload-intents/intent-1/complete", {}),
      store,
    );
    expect(complete.status).toBe(201);
    const documentId = (complete.body as { documentId: string }).documentId;

    const review = dispatch(req("GET", `/api/v1/documents/${documentId}/review`), store);
    expect(review.status).toBe(200);
    const candidate = (review.body as { candidates: { id: string }[] }).candidates[0];

    const before = store.evidence.length;
    const confirm = dispatch(
      req("POST", `/api/v1/documents/${documentId}/confirmations`, {
        expectedDocumentVersion: 1,
        dispositions: [{ candidateId: candidate.id, disposition: "ACCEPTED" }],
      }),
      store,
    );
    expect(confirm.status).toBe(201);
    expect(store.evidence.length).toBe(before + 1);

    const stale = dispatch(
      req("POST", `/api/v1/documents/${FIXTURE_DOCUMENT_ID}/confirmations`, {
        expectedDocumentVersion: 999,
        dispositions: [],
      }),
      store,
    );
    expect(stale.status).toBe(409);
    expect((stale.body as { code: string }).code).toBe("STALE_DOCUMENT_VERSION");
  });
});
