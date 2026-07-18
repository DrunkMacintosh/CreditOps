import { describe, expect, it } from "vitest";

import { MEMO_SECTION_KEYS, parseCreditOpsStatus } from "../../../lib/api/credit-ops";
import { buildStore, FIXTURE_CASE_ID } from "../../../lib/fixtures/dataset";
import { dispatch } from "../../../lib/fixtures/router";
import type { FixtureStore } from "../../../lib/fixtures/store";
import type { FixtureRequest, HttpMethod, ScenarioId } from "../../../lib/fixtures/types";

function makeRequest(method: HttpMethod, path: string, body: unknown = null): FixtureRequest {
  const [rawPath, rawQuery = ""] = path.split("?");
  return {
    method,
    path: rawPath,
    segments: rawPath.split("/").filter(Boolean),
    query: new URLSearchParams(rawQuery),
    body,
    headers: new Headers(),
  };
}

function getCreditOps(store: FixtureStore) {
  return dispatch(makeRequest("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/credit-ops`), store);
}

describe("credit-ops fixture handler", () => {
  it("returns a rich package that parses through the REAL parser (clean-complete)", () => {
    const store = buildStore("clean-complete");
    const response = getCreditOps(store);

    expect(response.status).toBe(200);
    // Prove the wire body survives the production parser unchanged.
    const status = parseCreditOpsStatus(response.body);

    expect(status.caseId).toBe(FIXTURE_CASE_ID);
    expect(status.agentRole).toBe("CREDIT_OPERATIONS");
    expect(status.packageId.length).toBeGreaterThan(0);
  });

  it("marks the package complete: all four upstream artifacts present, no unresolved / blocking items", () => {
    const status = parseCreditOpsStatus(getCreditOps(buildStore("clean-complete")).body);
    const completeness = status.packageCompleteness;

    expect(completeness.allRequiredPresent).toBe(true);
    expect(completeness.unresolvedChallengeCount).toBe(0);
    expect(completeness.openBlockingGapCount).toBe(0);

    const kinds = completeness.artifacts.map((a) => a.artifact);
    expect(kinds).toEqual([
      "INTAKE_HANDOFF",
      "UNDERWRITING_ASSESSMENT",
      "LEGAL_ASSESSMENT",
      "RISK_REVIEW_ASSESSMENT",
    ]);
    for (const item of completeness.artifacts) {
      expect(item.status).toBe("PRESENT");
      // detail_vi / reference_id read off the SNAKE_CASE stored dict.
      expect(item.detailVi.length).toBeGreaterThan(0);
      expect(item.referenceId).not.toBeNull();
    }
  });

  it("consolidates provenance with a distinct citation count", () => {
    const status = parseCreditOpsStatus(getCreditOps(buildStore("clean-complete")).body);
    const consolidation = status.evidenceConsolidation;

    expect(consolidation.entries.length).toBeGreaterThanOrEqual(3);
    expect(consolidation.distinctCitationCount).toBeGreaterThan(0);
    // citation_count read off the SNAKE_CASE entry dict.
    expect(consolidation.entries.every((e) => e.citationCount >= 0)).toBe(true);
  });

  it("presents a DRAFT memo with all six sections and a synthetic disclaimer — no decision", () => {
    const status = parseCreditOpsStatus(getCreditOps(buildStore("clean-complete")).body);
    const memo = status.draftMemo;

    expect(memo.present).toBe(true);
    expect(memo.syntheticDisclaimerVi.length).toBeGreaterThan(0);
    expect(memo.sections).toHaveLength(6);
    expect(memo.sections.map((s) => s.key)).toEqual([...MEMO_SECTION_KEYS]);
    // Every section summarises real statements/citations (parser counts them).
    const totalStatements = memo.sections.reduce((sum, s) => sum + s.statementCount, 0);
    expect(totalStatements).toBeGreaterThan(0);
    const totalCitations = memo.sections.reduce((sum, s) => sum + s.citationCount, 0);
    expect(totalCitations).toBeGreaterThan(0);
    // The challenge section carries a disposition status string.
    expect(memo.dispositionStatusVi.length).toBeGreaterThan(0);
  });

  it("surfaces one approved document request and one authorized handoff action", () => {
    const status = parseCreditOpsStatus(getCreditOps(buildStore("clean-complete")).body);

    expect(status.documentRequests).toHaveLength(1);
    const request = status.documentRequests[0];
    expect(request.approvalStatus).toBe("APPROVED");
    expect(request.approvals).toHaveLength(1);

    expect(status.proposedActions).toHaveLength(1);
    const action = status.proposedActions[0];
    expect(action.actionType).toBe("PREPARE_HANDOFF_PACKAGE");
    expect(action.authorized).toBe(true);
    expect(action.authorizations).toHaveLength(1);

    expect(status.g2GateStatus).toBe("SATISFIED");
    expect(status.g4GateStatus).toBe("SATISFIED");
  });

  it("carries NO approved/rejected credit decision anywhere in the package", () => {
    const response = getCreditOps(buildStore("clean-complete"));
    // Maker assembly only. The whole serialized body must not contain any
    // credit-decision verb — nothing is approved or rejected as a decision.
    const wire = JSON.stringify(response.body).toUpperCase();
    for (const forbidden of ["APPROVE_CREDIT", "REJECT", "DENIED", "DECISION_APPROVE", "CREDIT_DECISION"]) {
      expect(wire).not.toContain(forbidden);
    }

    const status = parseCreditOpsStatus(response.body);
    // The one APPROVED status is a document-request approval (G2 human gate),
    // not a credit decision — assert that is the only "approved" surface.
    expect(status.documentRequests[0].approvalStatus).toBe("APPROVED");
    expect(status.proposedActions.every((a) => a.executionStatus !== "EXECUTED")).toBe(true);
  });

  it("falls through to the CREDIT_OPS_NOT_AVAILABLE empty state for other scenarios", () => {
    const scenario: ScenarioId = "missing-documents";
    const response = getCreditOps(buildStore(scenario));
    expect(response.status).toBe(404);
    expect(response.body).toMatchObject({ code: "CREDIT_OPS_NOT_AVAILABLE" });
  });
});
