-- Stage 10 (master design section 5 giai đoạn 10 "Kiểm tra điều kiện giải ngân"):
-- the typed disbursement ConditionLedger and the human confirmation gate
-- HG_DISBURSEMENT_CONDITIONS_CONFIRMED.
--
-- Independent credit operations verify signed contracts, perfected security,
-- customer own-funds participation, purpose documents, licences and any other
-- bound conditions BEFORE disbursement.  This migration stores that ledger:
--
--   * public.disbursement_conditions -- one row per condition, bound to its
--     SOURCE human credit decision (composite FK to the exact decision + case +
--     version triple), with a CLOSED status set and evidence references.
--   * public.condition_status_events -- the append-only status-history trail;
--     the VERIFIED event captures the verifier + verification time (the row
--     itself never carries a mutable verifier column).
--
-- DETERMINISTIC TRANSITIONS: the conditions row's status may be UPDATED only
-- via an allowed edge.  A BEFORE UPDATE trigger encodes the SAME transition map
-- as services/api/src/creditops/domain/conditions.py::ALLOWED_TRANSITIONS and
-- rejects (a) any UPDATE that changes a column other than status/evidence_refs
-- (42501, append-only identity) and (b) any status pair not in the map (23514).
-- Deletes are forbidden (42501).  This is defence in depth: the application
-- layer re-checks the same map before every write.
--
-- WAIVER is HUMAN-only with an authority record: a waiver / not-applicable
-- ruling is recorded as a status event carrying rationale + actor + role.  No
-- agent path ever writes here -- the sole write surface is the human-only API
-- (services/api/src/creditops/api/conditions.py) through its Postgres adapter.
--
-- PROPOSED / SYNTHETIC: 'HG_DISBURSEMENT_CONDITIONS_CONFIRMED' is a synthetic
-- gate name with NO official SHB role mapping, approval delegation, or control
-- code, exactly like the existing G1..G4 and HG_ synthetic gates
-- (202607180001, 202607180012, 202607180016).  The status taxonomy and the
-- transition edges are likewise a prototype configuration, to be reconfigured
-- when an official source exists.  Additive only: the new gate is a superset of
-- the prior CHECK set, so every existing human_gates row remains valid.
--
-- All customer data, policies, documents, and banking-system responses in this
-- project are synthetic and created solely for demonstration.

-- 1. Extend the human_gates gate-type registry.  The prior CHECK was last
--    re-declared in 202607180017_credit_notifications.sql (which added
--    HG_CREDIT_NOTIFICATION_APPROVED) as the constraint
--    public.human_gates_gate_type_check; dropping and re-adding keeps the
--    additive, one-superset-of-the-other semantics: this list is a strict
--    superset of that one (HG_CREDIT_NOTIFICATION_APPROVED is retained), so no
--    existing gate type is removed.
alter table public.human_gates
  drop constraint human_gates_gate_type_check;

alter table public.human_gates
  add constraint human_gates_gate_type_check check (
    gate_type in (
      'G1_INTAKE_COMPLETE',
      'G2_GAP_REQUEST_APPROVAL',
      'G3_RISK_DISPOSITION',
      'G4_OPS_AUTHORIZATION',
      'HG_FINANCING_NEED_CONFIRMED',
      'HG_UNDERWRITING_ASSESSMENT_REVIEWED',
      'HG_LEGAL_ASSESSMENT_REVIEWED',
      'HG_MAKER_SUBMISSION_CONFIRMED',
      'HG_CREDIT_NOTIFICATION_APPROVED',
      'HG_DISBURSEMENT_CONDITIONS_CONFIRMED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). '
  'HG_DISBURSEMENT_CONDITIONS_CONFIRMED is the stage-10 disbursement-conditions '
  'confirmation gate, satisfied only by an independent OPS checker after every '
  'condition is VERIFIED / WAIVED_BY_HUMAN / NOT_APPLICABLE_BY_HUMAN. Like the '
  'other HG_ gates it is human-satisfied only and is NOT required_gate on any '
  'task-graph node -- coupling orchestration readiness to it is a deferred '
  'decision.';

-- 2. The disbursement condition ledger.  Every condition binds its source
--    credit decision (composite FK to the exact decision + case + version
--    triple), the case version, an owner, an optional due date, and its
--    evidence references.  status defaults to PENDING and is one of the CLOSED
--    synthetic 8 values.
create table public.disbursement_conditions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  decision_id uuid not null,
  condition_text_vi text not null check (length(btrim(condition_text_vi)) > 0),
  owner_vi text check (owner_vi is null or length(btrim(owner_vi)) > 0),
  due_date date,
  -- CLOSED PROPOSED synthetic status taxonomy (design giai đoạn 10): reconfigure
  -- when an official source is supplied.
  status text not null default 'PENDING' check (
    status in (
      'PENDING',
      'EVIDENCE_SUBMITTED',
      'VERIFIED',
      'FAILED',
      'WAIVER_REQUESTED',
      'WAIVED_BY_HUMAN',
      'SUPERSEDED',
      'NOT_APPLICABLE_BY_HUMAN'
    )
  ),
  evidence_refs jsonb not null default '[]'::jsonb
    check (jsonb_typeof(evidence_refs) = 'array'),
  created_at timestamptz not null default clock_timestamp(),
  -- The condition binds the EXACT source decision + case + version triple, so a
  -- condition can never drift onto a different case version than the decision
  -- that sourced it (mirrors approved_term_snapshots' composite FK).
  constraint disbursement_conditions_decision_fk
    foreign key (decision_id, case_id, case_version)
    references public.human_credit_decisions(id, case_id, case_version)
    on delete restrict
);

create index disbursement_conditions_case_idx
  on public.disbursement_conditions (case_id, case_version, created_at desc);

create index disbursement_conditions_decision_idx
  on public.disbursement_conditions (decision_id);

-- DETERMINISTIC transition enforcement.  On UPDATE: only status/evidence_refs
-- may change (else 42501); the status pair must be an allowed edge in the same
-- map as domain/conditions.py::ALLOWED_TRANSITIONS (else 23514).  On DELETE:
-- forbidden (42501).  There is NO implicit edge and no self-transition.
create or replace function public.enforce_disbursement_condition_transition()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  allowed text[];
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'disbursement conditions cannot be deleted';
  end if;

  -- Only status and evidence_refs may ever change; identity/binding is frozen.
  if row(
    new.id, new.case_id, new.case_version, new.decision_id,
    new.condition_text_vi, new.owner_vi, new.due_date, new.created_at
  ) is distinct from row(
    old.id, old.case_id, old.case_version, old.decision_id,
    old.condition_text_vi, old.owner_vi, old.due_date, old.created_at
  ) then
    raise exception using
      errcode = '42501',
      message = 'only status and evidence_refs of a disbursement condition may change';
  end if;

  allowed := case old.status
    when 'PENDING' then
      array['EVIDENCE_SUBMITTED', 'WAIVER_REQUESTED', 'NOT_APPLICABLE_BY_HUMAN', 'SUPERSEDED']
    when 'EVIDENCE_SUBMITTED' then array['VERIFIED', 'FAILED', 'SUPERSEDED']
    when 'FAILED' then array['EVIDENCE_SUBMITTED', 'WAIVER_REQUESTED', 'SUPERSEDED']
    when 'WAIVER_REQUESTED' then array['WAIVED_BY_HUMAN', 'FAILED', 'SUPERSEDED']
    when 'VERIFIED' then array['SUPERSEDED']
    when 'WAIVED_BY_HUMAN' then array['SUPERSEDED']
    when 'NOT_APPLICABLE_BY_HUMAN' then array['SUPERSEDED']
    when 'SUPERSEDED' then array[]::text[]
    else array[]::text[]
  end;

  if not (new.status = any(allowed)) then
    raise exception using
      errcode = '23514',
      message = format(
        'forbidden disbursement condition transition %s -> %s',
        old.status, new.status
      );
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_disbursement_condition_transition() from public;

create trigger disbursement_conditions_enforce_transition
before update or delete on public.disbursement_conditions
for each row execute function public.enforce_disbursement_condition_transition();

alter table public.disbursement_conditions enable row level security;
alter table public.disbursement_conditions force row level security;

create policy disbursement_conditions_select_assigned
on public.disbursement_conditions
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = disbursement_conditions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.disbursement_conditions from public, anon, authenticated;
grant select on public.disbursement_conditions to authenticated;
grant all on public.disbursement_conditions to service_role;

-- 3. The append-only status-history trail.  Every transition (and the creation
--    event, from_status null -> PENDING) records the actor + role + time, so
--    the VERIFIED event is the verifier + verification-time record.  A waiver /
--    not-applicable event carries the human authority rationale.
create table public.condition_status_events (
  id uuid primary key default gen_random_uuid(),
  condition_id uuid not null
    references public.disbursement_conditions(id) on delete restrict,
  from_status text check (
    from_status is null or from_status in (
      'PENDING', 'EVIDENCE_SUBMITTED', 'VERIFIED', 'FAILED',
      'WAIVER_REQUESTED', 'WAIVED_BY_HUMAN', 'SUPERSEDED', 'NOT_APPLICABLE_BY_HUMAN'
    )
  ),
  to_status text not null check (
    to_status in (
      'PENDING', 'EVIDENCE_SUBMITTED', 'VERIFIED', 'FAILED',
      'WAIVER_REQUESTED', 'WAIVED_BY_HUMAN', 'SUPERSEDED', 'NOT_APPLICABLE_BY_HUMAN'
    )
  ),
  rationale_vi text check (rationale_vi is null or length(btrim(rationale_vi)) > 0),
  actor_id uuid not null,
  actor_role text not null check (length(btrim(actor_role)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  -- WAIVER is HUMAN-only with an AUTHORITY RECORD: a transition to
  -- WAIVED_BY_HUMAN or NOT_APPLICABLE_BY_HUMAN MUST carry a rationale (the
  -- captured authority record), enforced at the database.  Every other event
  -- (including the null -> PENDING creation event) may omit it.
  constraint condition_status_events_authority_rationale check (
    to_status not in ('WAIVED_BY_HUMAN', 'NOT_APPLICABLE_BY_HUMAN')
    or rationale_vi is not null
  )
);

create index condition_status_events_condition_idx
  on public.condition_status_events (condition_id, created_at);

create index condition_status_events_verified_actor_idx
  on public.condition_status_events (condition_id, actor_id)
  where to_status = 'VERIFIED';

create trigger condition_status_events_are_append_only
before update or delete on public.condition_status_events
for each row execute function public.reject_append_only_mutation();

alter table public.condition_status_events enable row level security;
alter table public.condition_status_events force row level security;

-- RLS is enforced by joining through the parent condition to the active case
-- assignment (the events table carries no case_id of its own).
create policy condition_status_events_select_assigned
on public.condition_status_events
for select to authenticated using (
  exists (
    select 1
    from public.disbursement_conditions as condition
    join public.case_assignments as assignment
      on assignment.case_id = condition.case_id
    where condition.id = condition_status_events.condition_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.condition_status_events from public, anon, authenticated;
grant select on public.condition_status_events to authenticated;
grant all on public.condition_status_events to service_role;
