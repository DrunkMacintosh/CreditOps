import { describe, expect, it } from "vitest";

import { parseConditionLedger } from "../../../lib/api/conditions";
import { parseDisbursementAction, parseDisbursementList } from "../../../lib/api/disbursements";
import { parseFacility, parseLedgerSnapshot, parseRepaymentEvent } from "../../../lib/api/repayments";
import { parseRiskReviewStatus } from "../../../lib/api/risk-review";
import { parseRecoveryCases, parseSettlementView } from "../../../lib/api/settlement-recovery";
import { buildStore, FIXTURE_CASE_ID } from "../../../lib/fixtures/dataset";
import { dispatch } from "../../../lib/fixtures/router";
import type { FixtureStore } from "../../../lib/fixtures/store";
import type { FixtureRequest, HttpMethod } from "../../../lib/fixtures/types";
import type { ScenarioId } from "../../../lib/fixtures/types";

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

function get(store: FixtureStore, resource: string) {
  return dispatch(makeRequest("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/${resource}`), store);
}

describe("stage-focus fixture handlers", () => {
  describe("risk review (risk-challenge)", () => {
    it("returns a HIGH LLM challenge with an OPEN gate and one unresolved challenge on load", () => {
      const store = buildStore("risk-challenge");
      const response = get(store, "risk-review");

      expect(response.status).toBe(200);
      const status = parseRiskReviewStatus(response.body);
      expect(status.caseId).toBe(FIXTURE_CASE_ID);
      expect(status.gateStatus).toBe("OPEN");
      expect(status.unresolvedChallengeCount).toBe(1);
      expect(status.challenges).toHaveLength(1);

      const challenge = status.challenges[0];
      expect(challenge.id).toBe("chal-dscr");
      expect(challenge.challengeType).toBe("UNSUPPORTED_ASSUMPTION");
      expect(challenge.severity).toBe("HIGH");
      expect(challenge.raisedBy).toBe("LLM");
      expect(challenge.dispositions).toHaveLength(0);
      expect(challenge.citations[0]).toMatchObject({ kind: "CALCULATOR_RESULT", resultId: "calc-dscr" });
    });

    it("records a MAKER_MUST_REVISE disposition without satisfying the gate", () => {
      const store = buildStore("risk-challenge");
      store.setSlice("risk", { disposition: "MAKER_MUST_REVISE" });
      const status = parseRiskReviewStatus(get(store, "risk-review").body);

      expect(status.gateStatus).toBe("OPEN");
      expect(status.unresolvedChallengeCount).toBe(0);
      const disposition = status.challenges[0].dispositions[0];
      expect(disposition.dispositionType).toBe("MAKER_MUST_REVISE");
      expect(disposition.rationale.length).toBeGreaterThan(0);
    });

    it("falls through to the not-available empty state for other scenarios", () => {
      const store = buildStore("clean-complete");
      const response = get(store, "risk-review");
      expect(response.status).toBe(404);
      expect(response.body).toMatchObject({ code: "RISK_REVIEW_NOT_AVAILABLE" });
    });
  });

  describe("conditions (pending-condition)", () => {
    it("surfaces a PENDING condition that blocks confirmation", () => {
      const store = buildStore("pending-condition");
      const response = get(store, "conditions");

      expect(response.status).toBe(200);
      const ledger = parseConditionLedger(response.body);
      expect(ledger.confirmable).toBe(false);
      expect(ledger.conditions).toHaveLength(1);
      expect(ledger.conditions[0].id).toBe("cond-bao-hiem-tai-san");
      expect(ledger.conditions[0].status).toBe("PENDING");
    });

    it("returns the empty ledger (still not confirmable) for other scenarios", () => {
      const ledger = parseConditionLedger(get(buildStore("clean-complete"), "conditions").body);
      expect(ledger.conditions).toHaveLength(0);
      expect(ledger.confirmable).toBe(false);
    });
  });

  describe("proposed disbursements (execution-unknown)", () => {
    it("surfaces EXECUTION_UNKNOWN with both gates satisfied and an unresolved receipt", () => {
      const store = buildStore("execution-unknown");
      const response = get(store, "proposed-disbursements");

      expect(response.status).toBe(200);
      const list = parseDisbursementList(response.body);
      expect(list.actions).toHaveLength(1);
      const detail = list.actions[0];
      expect(detail.action.status).toBe("EXECUTION_UNKNOWN");
      expect(detail.action.amount).toBe("5000000000");
      expect(typeof detail.action.amount).toBe("string");
      expect(detail.validatedGateStatus).toBe("SATISFIED");
      expect(detail.authorizedGateStatus).toBe("SATISFIED");
      expect(detail.receipts[0].resultStatus).toBe("EXECUTION_UNKNOWN");
      expect(detail.receipts[0].receiptRef).toBeNull();
    });

    it("reconciles to the human-chosen outcome and reflects it on the next read", () => {
      const store = buildStore("execution-unknown");
      const response = dispatch(
        makeRequest(
          "POST",
          `/api/v1/cases/${FIXTURE_CASE_ID}/proposed-disbursements/disb-attempt-1/reconcile`,
          { outcome: "CONFIRMED_EXECUTED", rationale: "Đã đối chiếu với core-banking (mock)." },
        ),
        store,
      );

      expect(response.status).toBe(201);
      expect(parseDisbursementAction(response.body).status).toBe("CONFIRMED_EXECUTED");
      expect(store.getSlice("disbursement")).toEqual({ state: "CONFIRMED_EXECUTED" });

      const list = parseDisbursementList(get(store, "proposed-disbursements").body);
      expect(list.actions[0].action.status).toBe("CONFIRMED_EXECUTED");
    });
  });

  describe("legal (policy-unavailable)", () => {
    it("fails closed with a retryable 503 when the policy source is down", () => {
      const store = buildStore("policy-unavailable");
      const response = get(store, "legal");
      expect(response.status).toBe(503);
      expect(response.body).toMatchObject({ code: "POLICY_SOURCE_UNAVAILABLE", retryable: true });
    });

    it("does not fabricate an assessment for scenarios with policy available", () => {
      const response = get(buildStore("clean-complete"), "legal");
      expect(response.status).toBe(404);
      expect(response.body).toMatchObject({ code: "LEGAL_ASSESSMENT_NOT_AVAILABLE" });
    });
  });

  describe("repayments (repayment-anomaly)", () => {
    it("opens a facility, records an event, and surfaces an anomalous ledger", () => {
      const store = buildStore("repayment-anomaly");

      const facilityResponse = dispatch(
        makeRequest("POST", `/api/v1/cases/${FIXTURE_CASE_ID}/repayments`, {
          principal: "5000000000",
          annualRatePercent: "11.5",
          termMonths: 12,
          repaymentStyle: "EQUAL_PRINCIPAL",
          firstPaymentDate: "2026-08-18",
        }),
        store,
      );
      expect(facilityResponse.status).toBe(201);
      const facility = parseFacility(facilityResponse.body);
      expect(facility.id).toBe("fac-1");
      expect(facility.principal).toBe("5000000000");

      const eventResponse = dispatch(
        makeRequest("POST", `/api/v1/cases/${FIXTURE_CASE_ID}/repayments/fac-1/events`, {
          kind: "PAYMENT",
          amount: "3800000000",
          externalReference: "ext-pmt-1",
          effectiveDate: "2026-08-18",
        }),
        store,
      );
      expect(eventResponse.status).toBe(201);
      const event = parseRepaymentEvent(eventResponse.body);
      expect(event.created).toBe(true);
      expect(store.getSlice("repayment")).toEqual({ recognized: true });
      expect(store.auditEvents.some((e) => e.eventType === "REPAYMENT_ANOMALY_RECORDED")).toBe(true);

      const ledgerResponse = dispatch(
        makeRequest("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/repayments/fac-1/ledger`),
        store,
      );
      expect(ledgerResponse.status).toBe(200);
      const ledger = parseLedgerSnapshot(ledgerResponse.body);
      expect(ledger.isSettled).toBe(false);
      expect(ledger.exceptions[0].kind).toBe("UNDERPAID_PERIOD");
      expect(ledger.exceptions[0].amount).toBe("1200000");
      expect(ledger.periods.some((p) => p.status === "PARTIALLY_PAID")).toBe(true);

      // Every money figure crosses the wire as an exact-decimal STRING.
      const moneyFields = [
        ledger.netPaid,
        ledger.outstandingFees,
        ledger.outstandingInterest,
        ledger.outstandingPrincipal,
        ledger.outstandingTotal,
        ledger.overpayment,
      ];
      for (const value of moneyFields) expect(typeof value).toBe("string");
      for (const period of ledger.periods) {
        for (const value of [
          period.expectedFee,
          period.expectedInterest,
          period.expectedPrincipal,
          period.allocatedFee,
          period.allocatedInterest,
          period.allocatedPrincipal,
          period.outstandingTotal,
        ]) {
          expect(typeof value).toBe("string");
        }
      }
    });
  });

  describe("settlement + recovery (settlement-recovery)", () => {
    it("cannot settle while a non-zero balance remains", () => {
      const store = buildStore("settlement-recovery");
      const view = parseSettlementView(get(store, "settlement").body);
      expect(view.confirmable).toBe(false);
      expect(view.checks).toHaveLength(1);
      const check = view.checks[0];
      expect(check.outstandingPrincipal).toBe("420000000");
      expect(check.zeroBalanceConfirmed).toBe(false);
      expect(check.openExceptionCount).toBe(1);
      expect(typeof check.outstandingInterest).toBe("string");
    });

    it("prepares a recovery case with options awaiting a separate human gate", () => {
      const store = buildStore("settlement-recovery");
      const recovery = parseRecoveryCases(get(store, "recovery").body);
      expect(recovery.recoveryCases).toHaveLength(1);
      const recoveryCase = recovery.recoveryCases[0];
      expect(recoveryCase.status).toBe("PREPARING");
      expect(recoveryCase.approvedBy).toBeNull();
      expect(recoveryCase.evidenceRefs).toContain("obl-goc-con-lai");
      expect(recoveryCase.options.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("keeps every focus stage inert for an unrelated scenario", () => {
    const scenario: ScenarioId = "missing-documents";
    const store = buildStore(scenario);
    // risk-review / legal 404 (not-available); the list reads default to empty.
    expect(get(store, "risk-review").status).toBe(404);
    expect(get(store, "legal").status).toBe(404);
    expect(parseConditionLedger(get(store, "conditions").body).conditions).toHaveLength(0);
    expect(parseDisbursementList(get(store, "proposed-disbursements").body).actions).toHaveLength(0);
    expect(parseSettlementView(get(store, "settlement").body).checks).toHaveLength(0);
    expect(parseRecoveryCases(get(store, "recovery").body).recoveryCases).toHaveLength(0);
  });
});
