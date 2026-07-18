-- PROPOSED planner-proposal history and the separate agent-task queue.
-- Proposals are advisory records (ADR-0001): append-only, never authoritative.

create table public.planner_proposals (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  execution_id uuid not null,
  proposal jsonb not null check (jsonb_typeof(proposal) = 'object'),
  status text not null check (
    status in ('PROPOSED', 'ACCEPTED', 'REJECTED', 'SKIPPED')
  ),
  validation_errors jsonb not null default '[]'::jsonb
    check (jsonb_typeof(validation_errors) = 'array'),
  prompt_version text not null check (length(btrim(prompt_version)) > 0),
  schema_version text not null check (length(btrim(schema_version)) > 0),
  model_version text check (
    model_version is null or length(btrim(model_version)) between 1 and 200
  ),
  created_at timestamptz not null default clock_timestamp()
);

create index planner_proposals_case_idx
  on public.planner_proposals (case_id, case_version, created_at);

create trigger planner_proposals_are_append_only
before update or delete on public.planner_proposals
for each row execute function public.reject_append_only_mutation();

-- Separate durable queue for agent tasks (orchestration + specialist roles).
-- Messages carry identifiers only, exactly like the document queue.
select pgmq.create('creditops_agent_tasks');

-- RLS: reads are scoped to active case assignments, matching sibling tables.
alter table public.task_dependencies enable row level security;
alter table public.task_dependencies force row level security;
alter table public.human_gates enable row level security;
alter table public.human_gates force row level security;
alter table public.planner_proposals enable row level security;
alter table public.planner_proposals force row level security;

create policy task_dependencies_select_assigned on public.task_dependencies
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = task_dependencies.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy human_gates_select_assigned on public.human_gates
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = human_gates.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy planner_proposals_select_assigned on public.planner_proposals
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = planner_proposals.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on
  public.task_dependencies,
  public.human_gates,
  public.planner_proposals
from public, anon, authenticated;

grant select on
  public.task_dependencies,
  public.human_gates,
  public.planner_proposals
to authenticated;

grant all on
  public.task_dependencies,
  public.human_gates,
  public.planner_proposals
to service_role;
