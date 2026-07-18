-- PROPOSED checker output store for the Independent Risk Review Agent.
-- Assessments are append-only independent-review analysis, never a decision:
-- the payload schema has no approve/reject/clear/resolve/override field, and
-- challenges are FIRST-CLASS append-only rows so a human disposition can bind
-- per-challenge without ever editing or deleting the challenge it disposes.
-- The checker NEVER satisfies G3_RISK_DISPOSITION; only an authorized human
-- disposition (recorded here) can, and the gate derivation lives in
-- application code (application/orchestration/gates.py), never in this
-- migration.  All data is synthetic.
--
-- This migration grants NO write access -- and no additional read access --
-- on public.underwriting_assessments or public.legal_compliance_assessments.
-- The checker's read-only access to those tables is enforced by the
-- application-layer port (application/ports/risk_review.py), not by new SQL
-- here; this migration only ever selects from them in application code
-- through the existing service_role grant those tables already carry.

create table public.risk_review_assessments (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  task_id uuid not null,
  execution_id uuid not null,
  agent_role text not null default 'INDEPENDENT_RISK_REVIEW'
    check (agent_role = 'INDEPENDENT_RISK_REVIEW'),
  prompt_version text not null check (length(btrim(prompt_version)) > 0),
  model_id text not null check (length(btrim(model_id)) > 0),
  endpoint_id text not null check (length(btrim(endpoint_id)) > 0),
  assessment jsonb not null check (jsonb_typeof(assessment) = 'object'),
  assessment_schema_version text not null default 'risk-review-assessment-v1'
    check (length(btrim(assessment_schema_version)) > 0),
  evidence_view_built_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  -- Composite case FK through the source task, exactly like the maker
  -- assessment tables: the assessment binds to the exact task row (and
  -- therefore case + case version) that produced it.
  constraint risk_review_assessments_task_case_fk
    foreign key (task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  -- One durable checker output per (case, version, task): duplicate
  -- delivery resolves to the existing row instead of a second assessment.
  constraint risk_review_assessments_task_key
    unique (case_id, case_version, task_id),
  constraint risk_review_assessments_time_order
    check (evidence_view_built_at <= created_at),
  -- Referenced by risk_review_challenges' composite FK below.  Declared in
  -- the same column order it is referenced in, matching this repo's existing
  -- convention (processing_tasks_id_case_version_key).
  constraint risk_review_assessments_id_case_version_key
    unique (id, case_id, case_version)
);

create index risk_review_assessments_case_idx
  on public.risk_review_assessments (case_id, case_version, created_at desc);

create trigger risk_review_assessments_are_append_only
before update or delete on public.risk_review_assessments
for each row execute function public.reject_append_only_mutation();

alter table public.risk_review_assessments enable row level security;
alter table public.risk_review_assessments force row level security;

create policy risk_review_assessments_select_assigned
on public.risk_review_assessments
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = risk_review_assessments.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.risk_review_assessments from public, anon, authenticated;

grant select on public.risk_review_assessments to authenticated;

grant all on public.risk_review_assessments to service_role;

-- Challenges are first-class, append-only rows (not just embedded JSON in the
-- assessment) so a disposition can bind to exactly one challenge id without
-- ever touching the assessment or challenge row it disposes.
create table public.risk_review_challenges (
  id uuid primary key,
  assessment_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  target_maker_source text not null
    check (target_maker_source in ('CREDIT_UNDERWRITING', 'LEGAL_COMPLIANCE_COLLATERAL')),
  target_maker_assessment_id uuid not null,
  target_section_path text not null check (length(btrim(target_section_path)) > 0),
  challenge_type text not null check (
    challenge_type in (
      'UNSUPPORTED_ASSUMPTION', 'OMITTED_RISK', 'INADEQUATE_MITIGANT',
      'GAP_VISIBILITY', 'EXCEPTION_VISIBILITY', 'OTHER_CONCERN'
    )
  ),
  statement_vi text not null check (length(btrim(statement_vi)) > 0),
  citations jsonb not null check (jsonb_typeof(citations) = 'array'),
  severity text not null check (severity in ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  confidence text not null check (confidence in ('HIGH', 'MEDIUM', 'LOW')),
  raised_by text not null check (raised_by in ('DETERMINISTIC', 'LLM')),
  created_at timestamptz not null default clock_timestamp(),
  constraint risk_review_challenges_assessment_fk
    foreign key (assessment_id, case_id, case_version)
    references public.risk_review_assessments(id, case_id, case_version)
    on delete restrict,
  -- Referenced by challenge_dispositions' composite FK below.
  constraint risk_review_challenges_id_assessment_key
    unique (id, assessment_id),
  constraint risk_review_challenges_citations_not_empty
    check (jsonb_array_length(citations) >= 1)
);

create index risk_review_challenges_assessment_idx
  on public.risk_review_challenges (assessment_id, created_at);

create index risk_review_challenges_case_idx
  on public.risk_review_challenges (case_id, case_version, severity);

create trigger risk_review_challenges_are_append_only
before update or delete on public.risk_review_challenges
for each row execute function public.reject_append_only_mutation();

alter table public.risk_review_challenges enable row level security;
alter table public.risk_review_challenges force row level security;

create policy risk_review_challenges_select_assigned
on public.risk_review_challenges
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = risk_review_challenges.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.risk_review_challenges from public, anon, authenticated;

grant select on public.risk_review_challenges to authenticated;

grant all on public.risk_review_challenges to service_role;

-- Human dispositions: append-only, actor id + role captured, and NEVER a
-- write to the challenge or assessment row they dispose.  ``challenge_id``
-- is null for an assessment-level disposition (the explicit human NOTED
-- disposition required when the checker raised no severe challenge at all --
-- G3 must never derive SATISFIED from silence).  A null-challenge row must
-- carry disposition_type 'NOTED'; nothing else may be recorded without a
-- challenge to attach to.
create table public.challenge_dispositions (
  id uuid primary key default gen_random_uuid(),
  assessment_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  challenge_id uuid,
  disposition_type text not null check (
    disposition_type in ('ACCEPTED_RISK', 'MAKER_MUST_REVISE', 'ESCALATED', 'NOTED')
  ),
  rationale_vi text not null check (length(btrim(rationale_vi)) > 0),
  actor_id uuid not null,
  actor_role text not null check (length(btrim(actor_role)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  constraint challenge_dispositions_assessment_fk
    foreign key (assessment_id, case_id, case_version)
    references public.risk_review_assessments(id, case_id, case_version)
    on delete restrict,
  -- MATCH SIMPLE: this check is skipped entirely when challenge_id is null
  -- (an assessment-level disposition), and enforced that a challenge-level
  -- disposition binds to a challenge that really belongs to this assessment
  -- when challenge_id is present.
  constraint challenge_dispositions_challenge_fk
    foreign key (challenge_id, assessment_id)
    references public.risk_review_challenges(id, assessment_id)
    on delete restrict,
  constraint challenge_dispositions_assessment_level_is_noted
    check (challenge_id is not null or disposition_type = 'NOTED')
);

create index challenge_dispositions_assessment_idx
  on public.challenge_dispositions (assessment_id, created_at);

create index challenge_dispositions_challenge_idx
  on public.challenge_dispositions (challenge_id, created_at);

create trigger challenge_dispositions_are_append_only
before update or delete on public.challenge_dispositions
for each row execute function public.reject_append_only_mutation();

alter table public.challenge_dispositions enable row level security;
alter table public.challenge_dispositions force row level security;

create policy challenge_dispositions_select_assigned
on public.challenge_dispositions
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = challenge_dispositions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.challenge_dispositions from public, anon, authenticated;

grant select on public.challenge_dispositions to authenticated;

grant all on public.challenge_dispositions to service_role;

-- Additive handoff-state extension: the checker->operations package.  Both
-- existing states (intake, maker->checker) remain valid; nothing existing is
-- rewritten.  G3_RISK_DISPOSITION itself is never satisfied by this handoff
-- or by any agent write -- only a human disposition (above) can move it, via
-- the deterministic derivation in application/orchestration/gates.py.
alter table public.handoffs
  drop constraint handoffs_state_check;
alter table public.handoffs
  add constraint handoffs_state_check
  check (
    state in (
      'READY_FOR_SPECIALIST_REVIEW', 'READY_FOR_RISK_REVIEW', 'READY_FOR_OPERATIONS'
    )
  );
