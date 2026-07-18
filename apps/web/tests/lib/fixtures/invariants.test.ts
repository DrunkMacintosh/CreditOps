import { describe, expect, it } from "vitest";

import { buildStore, FIXTURE_CASE_ID } from "../../../lib/fixtures/dataset";
import { dispatch } from "../../../lib/fixtures/router";
import { evaluateScenario, SCENARIOS } from "../../../lib/fixtures/scenarios";
import type { FixtureRequest, HttpMethod } from "../../../lib/fixtures/types";

const SCENARIO_IDS = SCENARIOS.map((s) => s.id);

const DECISION_EVENT = /APPROV|REJECT|DECISION|WAIVE/i;
const HIDDEN_REASONING_KEY = /reasoning|chain.?of.?thought|prompt|raw|thought/i;

function req(method: HttpMethod, path: string, body: unknown = null): FixtureRequest {
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

// The read surface each scenario exposes; used to prove no agent-authored credit
// decision ever leaks onto the wire.
function readBodies(store: ReturnType<typeof buildStore>): unknown[] {
  const base = `/api/v1/cases/${FIXTURE_CASE_ID}`;
  return [
    dispatch(req("GET", "/api/v1/cases"), store),
    dispatch(req("GET", base), store),
    dispatch(req("GET", `${base}/evidence`), store),
    dispatch(req("GET", `${base}/conflicts`), store),
    dispatch(req("GET", `${base}/handoffs`), store),
    dispatch(req("GET", `${base}/audit-events`), store),
    dispatch(req("GET", "/api/v1/work-items"), store),
  ].map((r) => r.body);
}

describe("safety invariant — agents cannot write a human credit decision", () => {
  it("records no agent-authored approve/reject/decision/waive audit event", () => {
    for (const id of SCENARIO_IDS) {
      const store = buildStore(id);
      for (const event of store.auditEvents) {
        if (event.actorType === "AGENT") {
          expect(event.eventType).not.toMatch(DECISION_EVENT);
        }
      }
    }
  });

  it("never returns a credit-decision field on any read", () => {
    for (const id of SCENARIO_IDS) {
      const store = buildStore(id);
      for (const body of readBodies(store)) {
        const serialised = JSON.stringify(body ?? {});
        expect(serialised).not.toMatch(/creditDecision/i);
        expect(serialised).not.toMatch(/"decision"\s*:\s*"(APPROVED|REJECTED)"/i);
      }
    }
  });
});

describe("safety invariant — no hidden reasoning in audit metadata", () => {
  it("keeps eventData a plain object with no reasoning keys or oversized strings", () => {
    for (const id of SCENARIO_IDS) {
      const store = buildStore(id);
      for (const event of store.auditEvents) {
        const data = event.eventData;
        expect(data).not.toBeNull();
        expect(typeof data).toBe("object");
        expect(Array.isArray(data)).toBe(false);
        for (const [key, value] of Object.entries(data)) {
          expect(key).not.toMatch(HIDDEN_REASONING_KEY);
          if (typeof value === "string") {
            expect(value.length).toBeLessThanOrEqual(500);
          }
        }
      }
    }
  });
});

describe("safety invariant — evidence and citations stay visible", () => {
  it("gives every clean-complete evidence item a page-region source", () => {
    const store = buildStore("clean-complete");
    const evidence = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/evidence`), store);
    const items = (evidence.body as { items: { source: Record<string, number> }[] }).items;
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) {
      const s = item.source;
      for (const key of ["page", "x", "y", "width", "height"] as const) {
        expect(typeof s[key]).toBe("number");
      }
    }
  });

  it("carries source values on every conflicting-facts conflict", () => {
    const store = buildStore("conflicting-facts");
    const conflicts = dispatch(req("GET", `/api/v1/cases/${FIXTURE_CASE_ID}/conflicts`), store);
    const items = (conflicts.body as { items: { sources: { value: unknown }[] }[] }).items;
    expect(items.length).toBeGreaterThan(0);
    for (const conflict of items) {
      expect(conflict.sources.length).toBeGreaterThanOrEqual(2);
      for (const source of conflict.sources) {
        expect(source.value).toBeDefined();
      }
    }
  });

  it("exposes an array of evidenceRefs on every scenario definition", () => {
    for (const scenario of SCENARIOS) {
      expect(Array.isArray(scenario.evidenceRefs)).toBe(true);
    }
  });
});

describe("safety invariant — human gates hold by default", () => {
  it("does not let settlement close with an open obligation", () => {
    const store = buildStore("settlement-recovery");
    expect(evaluateScenario("settlement-recovery", store).status).not.toBe("fail");
  });

  it("does not open disbursement while a condition is pending", () => {
    const store = buildStore("pending-condition");
    expect(evaluateScenario("pending-condition", store).status).not.toBe("fail");
  });

  it("never reports a confirmed disbursement execution by default", () => {
    const store = buildStore("execution-unknown");
    const disbursement = store.getSlice<{ state?: string }>("disbursement");
    expect(disbursement?.state).not.toBe("CONFIRMED_EXECUTED");
    expect(evaluateScenario("execution-unknown", store).actual).not.toMatch(/CONFIRMED_EXECUTED/i);
  });
});
