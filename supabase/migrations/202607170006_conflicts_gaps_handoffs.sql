-- PROPOSED workflow records for synthetic evidence review. Blocking levels and
-- dispositions are stored explicitly but no official SHB threshold is encoded.

create table public.evidence_conflicts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  field_key text not null check (length(btrim(field_key)) > 0),
  left_confirmed_fact_id uuid not null,
  right_confirmed_fact_id uuid not null,
  issue_vi text not null check (length(btrim(issue_vi)) > 0),
  status text not null default 'OPEN' check (status in ('OPEN', 'RESOLVED', 'STALE')),
  resolution_data jsonb check (
    resolution_data is null or jsonb_typeof(resolution_data) = 'object'
  ),
  conflict_schema_version text not null default '1'
    check (length(conflict_schema_version) > 0),
  created_at timestamptz not null default clock_timestamp(),
  resolved_at timestamptz,
  stale_at timestamptz,
  constraint evidence_conflicts_distinct_facts
    check (left_confirmed_fact_id <> right_confirmed_fact_id),
  constraint evidence_conflicts_left_fact_case_fk
    foreign key (left_confirmed_fact_id, case_id, case_version)
    references public.confirmed_facts(id, case_id, case_version)
    on delete restrict,
  constraint evidence_conflicts_right_fact_case_fk
    foreign key (right_confirmed_fact_id, case_id, case_version)
    references public.confirmed_facts(id, case_id, case_version)
    on delete restrict,
  constraint evidence_conflicts_fact_pair_key
    unique (case_id, case_version, left_confirmed_fact_id, right_confirmed_fact_id),
  constraint evidence_conflicts_time_order check (
    (resolved_at is null or resolved_at >= created_at)
    and (stale_at is null or stale_at >= created_at)
  )
);

create index evidence_conflicts_open_idx
  on public.evidence_conflicts (case_id, case_version, field_key, created_at)
  where status = 'OPEN';

create table public.evidence_gaps (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  affected_task_id uuid not null,
  status text not null default 'PROVISIONAL' check (
    status in ('PROVISIONAL', 'FORMAL', 'RESOLVED', 'STALE')
  ),
  blocking_level text not null check (
    blocking_level in ('BLOCKING', 'CONDITIONAL', 'CLARIFICATION')
  ),
  issue_vi text not null check (length(btrim(issue_vi)) > 0),
  missing_information_vi text not null check (length(btrim(missing_information_vi)) > 0),
  existing_evidence jsonb not null default '[]'::jsonb
    check (jsonb_typeof(existing_evidence) = 'array'),
  suggested_evidence_vi jsonb not null default '[]'::jsonb
    check (jsonb_typeof(suggested_evidence_vi) = 'array'),
  policy_citations jsonb not null default '[]'::jsonb
    check (jsonb_typeof(policy_citations) = 'array'),
  resolution_data jsonb check (
    resolution_data is null or jsonb_typeof(resolution_data) = 'object'
  ),
  gap_schema_version text not null default '1' check (length(gap_schema_version) > 0),
  created_by_type text not null check (length(btrim(created_by_type)) > 0),
  created_by_id uuid,
  created_at timestamptz not null default clock_timestamp(),
  resolved_at timestamptz,
  stale_at timestamptz,
  constraint evidence_gaps_task_case_fk
    foreign key (affected_task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  constraint evidence_gaps_time_order check (
    (resolved_at is null or resolved_at >= created_at)
    and (stale_at is null or stale_at >= created_at)
  )
);

create index evidence_gaps_active_idx
  on public.evidence_gaps (case_id, case_version, status, created_at)
  where status in ('PROVISIONAL', 'FORMAL');

create table public.handoffs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  source_task_id uuid not null,
  state text not null default 'READY_FOR_SPECIALIST_REVIEW'
    check (state = 'READY_FOR_SPECIALIST_REVIEW'),
  handoff_schema_version text not null default '1' check (length(handoff_schema_version) > 0),
  handoff_data jsonb not null check (jsonb_typeof(handoff_data) = 'object'),
  created_by_type text not null check (length(btrim(created_by_type)) > 0),
  created_by_id uuid,
  created_at timestamptz not null default clock_timestamp(),
  stale_at timestamptz,
  constraint handoffs_task_case_fk
    foreign key (source_task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  constraint handoffs_case_version_key unique (case_id, case_version, id),
  constraint handoffs_stale_after_creation
    check (stale_at is null or stale_at >= created_at)
);

create index handoffs_case_version_idx
  on public.handoffs (case_id, case_version, created_at);

create or replace function public.protect_handoff_immutable_content()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'handoffs cannot be deleted';
  end if;

  if row(
    new.id,
    new.case_id,
    new.case_version,
    new.source_task_id,
    new.state,
    new.handoff_schema_version,
    new.handoff_data,
    new.created_by_type,
    new.created_by_id,
    new.created_at
  ) is distinct from row(
    old.id,
    old.case_id,
    old.case_version,
    old.source_task_id,
    old.state,
    old.handoff_schema_version,
    old.handoff_data,
    old.created_by_type,
    old.created_by_id,
    old.created_at
  ) then
    raise exception using
      errcode = '42501',
      message = 'handoff content is immutable';
  end if;

  return new;
end;
$$;

revoke all on function public.protect_handoff_immutable_content() from public;

create trigger handoffs_immutable_content
before update or delete on public.handoffs
for each row execute function public.protect_handoff_immutable_content();

alter table public.evidence_conflicts enable row level security;
alter table public.evidence_conflicts force row level security;
alter table public.evidence_gaps enable row level security;
alter table public.evidence_gaps force row level security;
alter table public.handoffs enable row level security;
alter table public.handoffs force row level security;

create policy evidence_conflicts_select_assigned on public.evidence_conflicts
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = evidence_conflicts.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy evidence_gaps_select_assigned on public.evidence_gaps
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = evidence_gaps.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy handoffs_select_assigned on public.handoffs
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = handoffs.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on
  public.evidence_conflicts,
  public.evidence_gaps,
  public.handoffs
from public, anon, authenticated;

grant select on
  public.evidence_conflicts,
  public.evidence_gaps,
  public.handoffs
to authenticated;

grant all on
  public.evidence_conflicts,
  public.evidence_gaps,
  public.handoffs
to service_role;
