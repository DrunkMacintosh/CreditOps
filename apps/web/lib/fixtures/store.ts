// -----------------------------------------------------------------------------
// FixtureStore — the in-memory synthetic backend for one active scenario.
//
// A fresh store is built from a scenario's dataset on activation. Reads return
// current slices; mutations (driven by real workspace CTAs or the switcher's
// deterministic Test controls) update it in place and append audit events.
// Everything is deterministic: generated ids come from a counter and timestamps
// from a fixed clock, so tests and repeated runs are stable.
// -----------------------------------------------------------------------------

import type {
  AuditEventDto,
  ConflictDto,
  ConfirmedFactDto,
  CreditCaseDto,
  DocumentReviewDto,
  HandoffDto,
} from "../api/contracts";

// Scenario behaviour toggles read by handlers to shape responses without
// bespoke per-scenario branches everywhere.
export interface ScenarioFlags {
  // Every mutation on this case is forbidden to the current actor → 403.
  unauthorized?: boolean;
  // The case itself is not visible to the current actor → 404 (never reveals
  // whether the case exists).
  crossCaseHidden?: boolean;
  // Policy/knowledge source is down → policy-dependent reads 503.
  policyUnavailable?: boolean;
  // The next confirm/disposition should fail with a 409 stale-version.
  staleGuard?: boolean;
  // Reasons intake completion must fail closed (409 INTAKE_INCOMPLETE). Empty
  // or absent means intake may complete once conflicts are cleared.
  intakeIncompleteReasons?: readonly string[];
}

export class FixtureStore {
  readonly scenarioId: string;
  readonly flags: ScenarioFlags;

  case: CreditCaseDto;
  documents: Map<string, DocumentReviewDto>;
  evidence: ConfirmedFactDto[];
  conflicts: ConflictDto[];
  handoff: HandoffDto | null;
  intakeComplete: boolean;
  auditEvents: AuditEventDto[];

  // Stage-specific slices (underwriting, risk, disbursement, monitoring, …)
  // live here so their handlers can own their shapes in separate files without
  // widening this class. Keyed by domain name.
  private readonly slices = new Map<string, unknown>();

  // Deterministic id + clock generators.
  private idSeq = 0;
  private clockMinutes = 0;
  private readonly clockBase: number;

  constructor(init: {
    scenarioId: string;
    flags?: ScenarioFlags;
    case: CreditCaseDto;
    documents?: DocumentReviewDto[];
    evidence?: ConfirmedFactDto[];
    conflicts?: ConflictDto[];
    handoff?: HandoffDto | null;
    intakeComplete?: boolean;
    auditEvents?: AuditEventDto[];
    // ISO instant the deterministic clock starts from.
    clockBaseIso?: string;
  }) {
    this.scenarioId = init.scenarioId;
    this.flags = init.flags ?? {};
    this.case = init.case;
    this.documents = new Map((init.documents ?? []).map((d) => [d.documentId, d]));
    this.evidence = init.evidence ?? [];
    this.conflicts = init.conflicts ?? [];
    this.handoff = init.handoff ?? null;
    this.intakeComplete = init.intakeComplete ?? false;
    this.auditEvents = init.auditEvents ?? [];
    this.clockBase = Date.parse(init.clockBaseIso ?? "2026-07-18T02:00:00.000Z");
  }

  // Monotonic, deterministic id with a domain prefix.
  nextId(prefix: string): string {
    this.idSeq += 1;
    return `${prefix}-${this.scenarioId}-${String(this.idSeq).padStart(4, "0")}`;
  }

  // Deterministic ISO timestamp that advances one minute per call.
  now(): string {
    this.clockMinutes += 1;
    return new Date(this.clockBase + this.clockMinutes * 60_000).toISOString();
  }

  bumpCaseVersion(): number {
    this.case = { ...this.case, version: this.case.version + 1, updatedAt: this.now() };
    return this.case.version;
  }

  recordAudit(event: {
    eventType: string;
    actorType: string;
    actorId?: string | null;
    artifactType: string;
    artifactId: string;
    eventData?: Record<string, unknown>;
  }): AuditEventDto {
    const entry: AuditEventDto = {
      id: this.nextId("audit"),
      caseVersion: this.case.version,
      eventType: event.eventType,
      actorType: event.actorType,
      actorId: event.actorId ?? null,
      artifactType: event.artifactType,
      artifactId: event.artifactId,
      eventData: event.eventData ?? {},
      createdAt: this.now(),
    };
    // Newest first — matches the cursor-paginated audit timeline ordering.
    this.auditEvents.unshift(entry);
    return entry;
  }

  getSlice<T>(domain: string): T | undefined {
    return this.slices.get(domain) as T | undefined;
  }

  setSlice<T>(domain: string, value: T): void {
    this.slices.set(domain, value);
  }
}
