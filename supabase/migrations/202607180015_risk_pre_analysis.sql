-- PROPOSED blind pre-analysis store for the Independent Risk Review Agent.
--
-- Two-pass independent review (docs/superpowers/specs/
-- 2026-07-18-full-credit-lifecycle-agent-workflow-design.md, stage 6): Pass A
-- reads evidence/calculations/gaps WITHOUT the maker conclusions to form an
-- INDEPENDENT pre-analysis; Pass B (the risk_review_assessments store from
-- 202607180005) then compares that blind view against the maker/legal
-- artifacts and raises challenges.  This table holds the Pass A artifact.
--
-- The Pass A artifact is a STRUCTURED analysis (typed independent risks and
-- observations, each evidence-cited), NEVER free-form chain-of-thought and
-- NEVER a decision: the payload schema has no approve/reject/clear/resolve/
-- override/decision field (enforced in application code), and the row is
-- append-only.  Like the checker assessment it can NEVER satisfy any gate.
--
-- This migration grants NO write access -- and no read access -- on
-- public.underwriting_assessments or public.legal_compliance_assessments: the
-- blind pass is structurally incapable of loading maker output because this
-- store never references those tables.  All data is synthetic.

create table public.risk_pre_analyses (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  task_id uuid not null,
  execution_id uuid not null,
  agent_role text not null default 'INDEPENDENT_RISK_REVIEW'
    check (agent_role = 'INDEPENDENT_RISK_REVIEW'),
  prompt_version text not null check (length(btrim(prompt_version)) > 0),
  schema_version text not null check (length(btrim(schema_version)) > 0),
  analysis jsonb not null check (jsonb_typeof(analysis) = 'object'),
  created_at timestamptz not null default clock_timestamp(),
  -- Composite case FK through the source task, exactly like the checker
  -- assessment table: the blind pre-analysis binds to the exact task row (and
  -- therefore case + case version) that produced it.
  constraint risk_pre_analyses_task_case_fk
    foreign key (task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  -- One durable blind pre-analysis per (case, version, task): a redelivery
  -- that resumes into Pass B resolves to the existing row instead of running
  -- Pass A a second time (inbox dedup, mirroring risk_review_assessments).
  constraint risk_pre_analyses_task_key
    unique (case_id, case_version, task_id)
);

create index risk_pre_analyses_case_idx
  on public.risk_pre_analyses (case_id, case_version, created_at desc);

create trigger risk_pre_analyses_are_append_only
before update or delete on public.risk_pre_analyses
for each row execute function public.reject_append_only_mutation();

alter table public.risk_pre_analyses enable row level security;
alter table public.risk_pre_analyses force row level security;

create policy risk_pre_analyses_select_assigned
on public.risk_pre_analyses
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = risk_pre_analyses.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.risk_pre_analyses from public, anon, authenticated;

grant select on public.risk_pre_analyses to authenticated;

grant all on public.risk_pre_analyses to service_role;
