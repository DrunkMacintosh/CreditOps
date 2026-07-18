import { describe, expect, it } from "vitest";

import { buildStore } from "../../../lib/fixtures/dataset";
import { evaluateScenario, getScenario, SCENARIOS } from "../../../lib/fixtures/scenarios";
import type { AssertionStatus, ScenarioId } from "../../../lib/fixtures/types";

const VALID_STATUSES: AssertionStatus[] = ["pending", "pass", "fail"];

describe("SCENARIOS catalogue", () => {
  it("holds exactly the 12 brief scenarios with unique 1..12 ordinals", () => {
    expect(SCENARIOS).toHaveLength(12);
    const ordinals = SCENARIOS.map((s) => s.ordinal).sort((a, b) => a - b);
    expect(ordinals).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(new Set(ordinals).size).toBe(12);
  });

  it("gives every scenario the required non-empty metadata", () => {
    for (const scenario of SCENARIOS) {
      expect(scenario.title.trim().length).toBeGreaterThan(0);
      expect(scenario.initialState.trim().length).toBeGreaterThan(0);
      expect(scenario.agentUnderTest.trim().length).toBeGreaterThan(0);
      expect(scenario.humanGate.trim().length).toBeGreaterThan(0);
      expect(scenario.expectedResult.trim().length).toBeGreaterThan(0);
      expect(scenario.auditEvent.trim().length).toBeGreaterThan(0);
      expect(scenario.focusSection.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("evaluateScenario", () => {
  it("returns a valid status and a non-empty actual for every scenario's fresh store", () => {
    for (const scenario of SCENARIOS) {
      const result = evaluateScenario(scenario.id, buildStore(scenario.id));
      expect(VALID_STATUSES).toContain(result.status);
      expect(result.actual.trim().length).toBeGreaterThan(0);
    }
  });

  it.each<[ScenarioId, AssertionStatus]>([
    ["clean-complete", "pass"],
    ["missing-documents", "pass"],
    ["conflicting-facts", "pass"],
    ["unauthorized-access", "pass"],
    ["policy-unavailable", "pass"],
    ["execution-unknown", "pass"],
  ])("reports %s as %s on its initial store", (id, expected) => {
    expect(evaluateScenario(id, buildStore(id)).status).toBe(expected);
  });
});

describe("getScenario", () => {
  it("returns the matching definition", () => {
    expect(getScenario("clean-complete").ordinal).toBe(1);
  });

  it("throws on an unknown id", () => {
    expect(() => getScenario("does-not-exist" as ScenarioId)).toThrow();
  });
});
