"use client";

// -----------------------------------------------------------------------------
// ScenarioProvider — the React surface over the fixture interceptor.
//
// Owns which synthetic scenario (if any) is active, persists the choice for the
// session, keeps a live pass/fail assertion in sync as the tester drives real
// workspace CTAs, and runs the deterministic Test controls. Default: OFF (the
// app talks to the real backend).
// -----------------------------------------------------------------------------

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { buildStore, FIXTURE_CASE_ID } from "./dataset";
import {
  FIXTURE_SESSION_KEY,
  getActiveStore,
  setActiveStore,
  subscribeToFixture,
} from "./interceptor";
import { evaluateScenario, getScenario } from "./scenarios";
import type { AssertionResult, ScenarioDefinition, ScenarioId } from "./types";

interface ScenarioContextValue {
  activeScenarioId: ScenarioId | null;
  activeScenario: ScenarioDefinition | null;
  fixtureCaseId: string;
  assertion: AssertionResult | null;
  activate: (id: ScenarioId) => void;
  deactivate: () => void;
  reset: () => void;
  runControl: (controlId: string) => void;
}

const ScenarioContext = createContext<ScenarioContextValue | null>(null);

export function ScenarioProvider({ children }: { children: ReactNode }) {
  const [activeScenarioId, setActiveScenarioId] = useState<ScenarioId | null>(null);
  // Bumped whenever the fixture store mutates so the assertion re-evaluates.
  const [tick, setTick] = useState(0);

  // Sync React state from the interceptor, which eagerly restores any persisted
  // scenario at module import (before effects). No re-building here → no risk of
  // clobbering a store the interceptor already seeded.
  useEffect(() => {
    const restored = getActiveStore();
    if (restored) setActiveScenarioId(restored.scenarioId as ScenarioId);
    const unsubscribe = subscribeToFixture(() => setTick((t) => t + 1));
    return unsubscribe;
  }, []);

  const activate = useCallback((id: ScenarioId) => {
    setActiveStore(buildStore(id));
    setActiveScenarioId(id);
    window.sessionStorage.setItem(FIXTURE_SESSION_KEY, id);
  }, []);

  const deactivate = useCallback(() => {
    setActiveStore(null);
    setActiveScenarioId(null);
    window.sessionStorage.removeItem(FIXTURE_SESSION_KEY);
  }, []);

  const reset = useCallback(() => {
    if (activeScenarioId) setActiveStore(buildStore(activeScenarioId));
  }, [activeScenarioId]);

  const runControl = useCallback((controlId: string) => {
    const store = getActiveStore();
    if (!store) return;
    const caseId = store.case.id;
    switch (controlId) {
      case "attempt-complete":
        // Exercise the real 409 INTAKE_INCOMPLETE path through the client stack.
        void fetch(`/api/creditops/api/v1/cases/${caseId}/intake-completion`, {
          method: "POST",
          body: "{}",
        }).catch(() => {});
        break;
      case "force-mutation":
        void fetch(`/api/creditops/api/v1/documents/doc-bctc-2025/confirmations`, {
          method: "POST",
          body: JSON.stringify({ expectedDocumentVersion: 1, dispositions: [] }),
        }).catch(() => {});
        break;
      case "cross-case":
        store.flags.crossCaseHidden = true;
        store.recordAudit({
          eventType: "AUTHORIZATION_DENIED",
          actorType: "SYSTEM",
          artifactType: "CASE",
          artifactId: caseId,
          eventData: { reason: "CROSS_CASE_ACCESS" },
        });
        setTick((t) => t + 1);
        break;
      case "dispose-must-revise":
        store.setSlice("risk", { disposition: "MAKER_MUST_REVISE" });
        store.recordAudit({
          eventType: "RISK_CHALLENGE_DISPOSED",
          actorType: "HUMAN",
          artifactType: "RISK_REVIEW",
          artifactId: caseId,
          eventData: { dispositionType: "MAKER_MUST_REVISE" },
        });
        setTick((t) => t + 1);
        break;
      case "reconcile": {
        const prev = store.getSlice<{ state?: string }>("disbursement") ?? {};
        store.setSlice("disbursement", { ...prev, state: "RECONCILING" });
        store.recordAudit({
          eventType: "DISBURSEMENT_RECONCILIATION_STARTED",
          actorType: "HUMAN",
          artifactType: "DISBURSEMENT",
          artifactId: caseId,
        });
        setTick((t) => t + 1);
        break;
      }
      case "mark-unreadable":
        store.recordAudit({
          eventType: "DOCUMENT_UNREADABLE_FLAGGED",
          actorType: "HUMAN",
          artifactType: "DOCUMENT",
          artifactId: "doc-scan-mo",
          eventData: { disposition: "UNREADABLE" },
        });
        setTick((t) => t + 1);
        break;
      default:
        break;
    }
  }, []);

  const value = useMemo<ScenarioContextValue>(() => {
    const store = getActiveStore();
    const assertion =
      activeScenarioId && store ? evaluateScenario(activeScenarioId, store) : null;
    return {
      activeScenarioId,
      activeScenario: activeScenarioId ? getScenario(activeScenarioId) : null,
      fixtureCaseId: FIXTURE_CASE_ID,
      assertion,
      activate,
      deactivate,
      reset,
      runControl,
    };
    // `tick` intentionally in deps: it forces re-eval after store mutations.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeScenarioId, tick, activate, deactivate, reset, runControl]);

  return <ScenarioContext.Provider value={value}>{children}</ScenarioContext.Provider>;
}

export function useScenario(): ScenarioContextValue {
  const ctx = useContext(ScenarioContext);
  if (!ctx) throw new Error("useScenario must be used within a ScenarioProvider");
  return ctx;
}
