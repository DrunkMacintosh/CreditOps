-- PROPOSED maker output store for the Credit Underwriting Agent.
-- Assessments are append-only evidence-grounded analysis, never a decision:
-- the payload schema has no approval/rejection/score/waiver field and every
-- finding carries citations (enforced by the application schema; provenance
-- columns are extracted here for audit queries).  All data is synthetic.

create table public.underwriting_assessments (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  task_id uuid not null,
  execution_id uuid not null,
  agent_role text not null default 'CREDIT_UNDERWRITING'
    check (agent_role = 'CREDIT_UNDERWRITING'),
  prompt_version text not null check (length(btrim(prompt_version)) > 0),
  model_id text not null check (length(btrim(model_id)) > 0),
  endpoint_id text not null check (length(btrim(endpoint_id)) > 0),
  assessment jsonb not null check (jsonb_typeof(assessment) = 'object'),
  assessment_schema_version text not null default 'underwriting-assessment-v1'
    check (length(btrim(assessment_schema_version)) > 0),
  evidence_view_built_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  -- Composite case FK through the source task: the assessment binds to the
  -- exact task row (and therefore case + case version) that produced it.
  constraint underwriting_assessments_task_case_fk
    foreign key (task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  -- One durable maker output per (case, version, task): duplicate delivery
  -- resolves to the existing row instead of a second assessment.
  constraint underwriting_assessments_task_key
    unique (case_id, case_version, task_id),
  constraint underwriting_assessments_time_order
    check (evidence_view_built_at <= created_at)
);

create index underwriting_assessments_case_idx
  on public.underwriting_assessments (case_id, case_version, created_at desc);

create trigger underwriting_assessments_are_append_only
before update or delete on public.underwriting_assessments
for each row execute function public.reject_append_only_mutation();

-- Additive handoff-state extension: the maker->checker package.  The original
-- intake state remains valid; nothing existing is rewritten.
alter table public.handoffs
  drop constraint handoffs_state_check;
alter table public.handoffs
  add constraint handoffs_state_check
  check (state in ('READY_FOR_SPECIALIST_REVIEW', 'READY_FOR_RISK_REVIEW'));

-- RLS: force row security; reads scoped to active case assignments exactly
-- like sibling tables; writes only through the backend service role.
alter table public.underwriting_assessments enable row level security;
alter table public.underwriting_assessments force row level security;

create policy underwriting_assessments_select_assigned
on public.underwriting_assessments
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = underwriting_assessments.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.underwriting_assessments from public, anon, authenticated;

grant select on public.underwriting_assessments to authenticated;

grant all on public.underwriting_assessments to service_role;
