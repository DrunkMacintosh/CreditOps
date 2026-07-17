-- PROPOSED schema contract for synthetic demonstrations. Workflow-state values are
-- intentionally unconstrained until an official SHB workflow is supplied.

create table public.credit_cases (
  id uuid primary key default gen_random_uuid(),
  case_version integer not null default 1 check (case_version > 0),
  workflow_state text not null check (length(btrim(workflow_state)) > 0),
  case_schema_version text not null default '1' check (length(case_schema_version) > 0),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp()
);

create table public.case_assignments (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  officer_id uuid not null,
  assigned_by uuid not null,
  assigned_at timestamptz not null default clock_timestamp(),
  revoked_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  constraint case_assignments_revocation_after_assignment
    check (revoked_at is null or revoked_at >= assigned_at),
  constraint case_assignments_case_officer_key unique (case_id, officer_id)
);

create index case_assignments_active_officer_idx
  on public.case_assignments (officer_id, case_id)
  where revoked_at is null;

create table public.audit_events (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  event_type text not null check (length(btrim(event_type)) > 0),
  actor_type text not null check (length(btrim(actor_type)) > 0),
  actor_id uuid,
  artifact_type text not null check (length(btrim(artifact_type)) > 0),
  artifact_id uuid not null,
  event_schema_version text not null default '1' check (length(event_schema_version) > 0),
  event_data jsonb not null default '{}'::jsonb
    check (jsonb_typeof(event_data) = 'object'),
  created_at timestamptz not null default clock_timestamp()
);

create index audit_events_case_created_idx
  on public.audit_events (case_id, created_at, id);

create trigger audit_events_are_immutable
before update or delete on public.audit_events
for each row execute function public.reject_append_only_mutation();

alter table public.credit_cases enable row level security;
alter table public.credit_cases force row level security;
alter table public.case_assignments enable row level security;
alter table public.case_assignments force row level security;
alter table public.audit_events enable row level security;
alter table public.audit_events force row level security;

create policy case_assignments_select_own_active
on public.case_assignments
for select
to authenticated
using (
  officer_id = (select auth.uid())
  and revoked_at is null
);

create policy credit_cases_select_assigned
on public.credit_cases
for select
to authenticated
using (
  exists (
    select 1
    from public.case_assignments as assignment
    where assignment.case_id = credit_cases.id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy audit_events_select_assigned
on public.audit_events
for select
to authenticated
using (
  exists (
    select 1
    from public.case_assignments as assignment
    where assignment.case_id = audit_events.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.credit_cases, public.case_assignments, public.audit_events
  from public, anon, authenticated;
grant select on public.credit_cases, public.case_assignments, public.audit_events
  to authenticated;
grant all on public.credit_cases, public.case_assignments to service_role;
grant select, insert on public.audit_events to service_role;
