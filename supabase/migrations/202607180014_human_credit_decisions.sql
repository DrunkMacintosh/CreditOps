-- PROPOSED stage-6 human credit decision store (master design section 5 stage
-- 6; section 13 "decisions" family: human_credit_decisions +
-- approved_term_snapshots; P0 #9 remainder).
--
-- ONLY a human actor with decision authority records a credit decision.  No
-- agent path ever writes here: the sole write surface is the human-only API
-- (services/api/src/creditops/api/credit_decisions.py) through its Postgres
-- adapter, and the recorded ``actor_type`` is 'HUMAN:CREDIT_APPROVER'.  A
-- decision binds the EXACT case version and the exact memo/assessment artifact
-- versions the decider reviewed (memo_artifact_id / risk_assessment_id /
-- underwriting_assessment_id); that binding is validated fail-closed at the
-- application layer against the current case state (a stale version 409s, an
-- unknown artifact 422s).
--
-- ``decision`` is a CLOSED, PROPOSED synthetic taxonomy -- APPROVED_AS_PROPOSED,
-- APPROVED_WITH_CONDITIONS, RETURNED_FOR_REVISION, MORE_INFORMATION_REQUIRED,
-- DECLINED_BY_HUMAN.  It has NO official SHB decision vocabulary behind it and
-- must be reconfigured when an official source is supplied (design sections 4
-- and 5 stage 6).
--
-- Both tables are append-only (immutable trigger) and one decision exists per
-- case version -- a revision bumps the case version, never edits a decision.
-- An approved_term_snapshots row freezes the approved terms 1:1 with its
-- decision.  This migration records NO gate and drives NO orchestration; gate
-- and orchestration wiring is a later lead decision (design section 5 stage 6:
-- HG_CREDIT_DECISION_RECORDED is not derived here).
--
-- All customer data, policies, documents, and banking-system responses in this
-- project are synthetic and created solely for demonstration.

create table public.human_credit_decisions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  -- Closed PROPOSED synthetic decision taxonomy (see header): no official SHB
  -- decision vocabulary exists; reconfigure when a source is supplied.
  decision text not null check (
    decision in (
      'APPROVED_AS_PROPOSED',
      'APPROVED_WITH_CONDITIONS',
      'RETURNED_FOR_REVISION',
      'MORE_INFORMATION_REQUIRED',
      'DECLINED_BY_HUMAN'
    )
  ),
  rationale_vi text not null check (length(btrim(rationale_vi)) > 0),
  -- The human decider and the authority role they exercised.  Provenance only:
  -- a non-human actor can never reach this table (application-layer role gate).
  decided_by uuid not null,
  decided_by_role text not null check (length(btrim(decided_by_role)) > 0),
  -- Exact artifact versions the decider reviewed.  Nullable: a decision may be
  -- recorded without binding every artifact, but when bound the application
  -- layer proves each id is the current-version artifact for the case.
  memo_artifact_id uuid,
  risk_assessment_id uuid,
  underwriting_assessment_id uuid,
  -- APPROVED_WITH_CONDITIONS carries its non-empty conditions here; every other
  -- decision type carries the empty default.  The array shape is enforced here;
  -- the decision-type/conditions coupling is enforced in the domain model.
  conditions jsonb not null default '[]'::jsonb
    check (jsonb_typeof(conditions) = 'array'),
  created_at timestamptz not null default clock_timestamp(),
  -- ONE decision per case version: a duplicate insert for the same (case,
  -- version) is rejected, and the application layer resolves it to the existing
  -- decision (idempotent record-or-get).  A revision bumps the case version.
  constraint human_credit_decisions_case_version_key
    unique (case_id, case_version),
  -- Referenced by the snapshot's composite FK below so the snapshot binds the
  -- exact same (decision, case, version) triple.
  constraint human_credit_decisions_id_case_version_key
    unique (id, case_id, case_version)
);

create index human_credit_decisions_case_idx
  on public.human_credit_decisions (case_id, case_version, created_at desc);

create trigger human_credit_decisions_are_append_only
before update or delete on public.human_credit_decisions
for each row execute function public.reject_append_only_mutation();

alter table public.human_credit_decisions enable row level security;
alter table public.human_credit_decisions force row level security;

create policy human_credit_decisions_select_assigned
on public.human_credit_decisions
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = human_credit_decisions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.human_credit_decisions from public, anon, authenticated;
grant select on public.human_credit_decisions to authenticated;
grant all on public.human_credit_decisions to service_role;

-- One immutable snapshot of the approved terms, frozen at decision time, 1:1
-- with its decision.  ``terms`` is an object whose amount/currency/term/rate
-- fields are each nullable INSIDE the object; ``snapshot_hash`` is the
-- canonical sha256 of those terms (computed in the domain).  A decision that
-- forbids approved terms (DECLINED / RETURNED / MORE_INFORMATION) never gets a
-- row here -- that coupling is enforced in the domain/application layer.
create table public.approved_term_snapshots (
  id uuid primary key default gen_random_uuid(),
  decision_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  terms jsonb not null check (jsonb_typeof(terms) = 'object'),
  snapshot_hash char(64) not null check (snapshot_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp(),
  -- Composite FK: the snapshot binds the exact (decision, case, version) triple,
  -- so a snapshot can never drift onto a different case version than its
  -- decision.
  constraint approved_term_snapshots_decision_fk
    foreign key (decision_id, case_id, case_version)
    references public.human_credit_decisions(id, case_id, case_version)
    on delete restrict,
  -- 1:1 with the decision.
  constraint approved_term_snapshots_decision_key unique (decision_id)
);

create index approved_term_snapshots_case_idx
  on public.approved_term_snapshots (case_id, case_version, created_at desc);

create trigger approved_term_snapshots_are_append_only
before update or delete on public.approved_term_snapshots
for each row execute function public.reject_append_only_mutation();

alter table public.approved_term_snapshots enable row level security;
alter table public.approved_term_snapshots force row level security;

create policy approved_term_snapshots_select_assigned
on public.approved_term_snapshots
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = approved_term_snapshots.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.approved_term_snapshots from public, anon, authenticated;
grant select on public.approved_term_snapshots to authenticated;
grant all on public.approved_term_snapshots to service_role;
