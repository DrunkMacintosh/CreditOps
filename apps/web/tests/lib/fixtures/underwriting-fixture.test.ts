import { describe, expect, it } from "vitest";

import { parseUnderwritingAssessment } from "../../../lib/api/underwriting";
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

function getUnderwriting(store: FixtureStore) {
  return dispatch(
    makeRequest("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/underwriting`),
    store,
  );
}

describe("underwriting fixture handler", () => {
  it("serves a rich assessment for clean-complete that parses through the real parser", () => {
    const store = buildStore("clean-complete");
    const response = getUnderwriting(store);

    expect(response.status).toBe(200);
    // Prove the wire body survives the REAL parser (camelCase envelope over a
    // snake_case assessment body, Decimals as strings).
    const view = parseUnderwritingAssessment(response.body);

    expect(view.assessmentId).toBe("uw-assessment-1");
    expect(view.caseId).toBe(FIXTURE_CASE_ID);
    expect(view.agentRole).toBe("CREDIT_UNDERWRITING");
    expect(view.handoff?.state).toBe("READY_FOR_RISK_REVIEW");
    expect(view.assessment.provenance?.modelId).toBe("synthetic-mock-model");

    // Non-empty findings across the rendered sections.
    expect(view.assessment.business.findings.length).toBeGreaterThan(0);
    expect(view.assessment.financial.findings.length).toBeGreaterThan(0);
    expect(view.assessment.cashFlow.findings.length).toBeGreaterThan(0);
    expect(view.assessment.repaymentSource.findings.length).toBeGreaterThan(0);
    expect(view.assessment.repaymentSource.downsideScenarios.length).toBeGreaterThan(0);

    // Proposed structure with an exact-decimal amount string parsed to a number.
    expect(view.assessment.proposedStructure?.proposedAmountVnd).toBe(5000000000);
    expect(view.assessment.proposedStructure?.proposedAmountRaw).toBe("5000000000");
    expect(view.assessment.proposedStructure?.tenorMonths).toBe(12);

    // Risks / mitigants / assumptions / evidence gaps.
    expect(view.assessment.risks.length).toBeGreaterThanOrEqual(2);
    expect(view.assessment.mitigants.length).toBeGreaterThanOrEqual(1);
    expect(view.assessment.assumptions.length).toBeGreaterThanOrEqual(1);
    expect(view.assessment.evidenceGaps.length).toBeGreaterThanOrEqual(1);

    // At least one COMPUTED calculator result (DSCR) with a value + raw string.
    const dscr = view.assessment.calculatorResults.find((c) => c.calculator === "dscr");
    expect(dscr).toBeDefined();
    expect(dscr?.outcome.status).toBe("COMPUTED");
    if (dscr?.outcome.status === "COMPUTED") {
      expect(dscr.outcome.value).toBeCloseTo(1.45);
      expect(dscr.outcome.raw).toBe("1.450000");
    }

    // Visible citations of the accepted kinds resolve to confirmed facts and
    // calculator results.
    const financialCitations = view.assessment.financial.findings[0].citations;
    expect(financialCitations.length).toBeGreaterThan(0);
    expect(financialCitations.some((c) => c.confirmedFactId === "ev-cf-doanh-thu")).toBe(true);
    const cashFlowCitations = view.assessment.cashFlow.findings[0].citations;
    expect(cashFlowCitations.some((c) => c.resultId === "calc-dscr")).toBe(true);
  });

  it("references the stale evidence in statement/uncertainty text for downstream-stale", () => {
    const store = buildStore("downstream-stale");
    const view = parseUnderwritingAssessment(getUnderwriting(store).body);

    // The store carries a stale confirmed fact (ev-cf-doanh-thu). The assessment
    // must reference it and note the supersession in uncertainty text — without
    // inventing a wire "stale" field the parser does not read.
    const referencesStaleFact = view.assessment.business.findings.some((f) =>
      f.citations.some((c) => c.confirmedFactId === "ev-cf-doanh-thu"),
    );
    expect(referencesStaleFact).toBe(true);

    const uncertaintyMentionsSupersession = view.assessment.business.findings.some((f) =>
      f.uncertaintyVi.includes("ev-cf-doanh-thu"),
    );
    expect(uncertaintyMentionsSupersession).toBe(true);

    // Still a valid, computable assessment with the DSCR calculator.
    const dscr = view.assessment.calculatorResults.find((c) => c.calculator === "dscr");
    expect(dscr?.outcome.status).toBe("COMPUTED");
  });

  it("falls through to UNDERWRITING_NOT_AVAILABLE for a non-focused scenario", () => {
    const scenario: ScenarioId = "missing-documents";
    const response = getUnderwriting(buildStore(scenario));
    expect(response.status).toBe(404);
    expect(response.body).toMatchObject({ code: "UNDERWRITING_NOT_AVAILABLE" });
  });
});
