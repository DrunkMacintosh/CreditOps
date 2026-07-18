-- PROPOSED orchestration schema (ADR-0001: LLM proposes, deterministic engine
-- decides).  Additive only.  Gate names are SYNTHETIC placeholders; the
-- official SHB role mapping and approval delegation remain open questions.

-- 1. Close the processing_tasks task_type set to the known finite registry.
--    Existing rows are all DOCUMENT_INGESTION and remain valid.
alter table public.processing_tasks
  add constraint processing_tasks_task_type_known check (
    task_type in (
      'DOCUMENT_INGESTION',
      'ORCHESTRATOR_PLAN',
      'CREDIT_UNDERWRITING',
      'LEGAL_COMPLIANCE_COLLATERAL',
      'INDEPENDENT_RISK_REVIEW',
      'CREDIT_OPERATIONS'
    )
  ) not valid;
alter table public.processing_tasks
  validate constraint processing_tasks_task_type_known;

-- Document ingestion is always document-scoped; agent tasks never are.
alter table public.processing_tasks
  add constraint processing_tasks_document_scope check (
    (task_type = 'DOCUMENT_INGESTION') = (document_version_id is not null)
  ) not valid;
alter table public.processing_tasks
  validate constraint processing_tasks_document_scope;

-- 2. Task dependencies: task -> depends-on task, both bound to the same
--    case/version so a dependency can never cross a case-version fence.
create table public.task_dependencies (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  task_id uuid not null,
  depends_on_task_id uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint task_dependencies_task_case_fk
    foreign key (task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  constraint task_dependencies_depends_case_fk
    foreign key (depends_on_task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  constraint task_dependencies_no_self_dependency
    check (task_id <> depends_on_task_id),
  constraint task_dependencies_pair_key unique (task_id, depends_on_task_id)
);

create index task_dependencies_case_idx
  on public.task_dependencies (case_id, case_version, task_id);

create trigger task_dependencies_are_append_only
before update or delete on public.task_dependencies
for each row execute function public.reject_append_only_mutation();

-- 3. Human gates.  ASSUMPTION: G1..G4 are synthetic gate names.  Only
--    G1_INTAKE_COMPLETE may be satisfied by the deterministic engine (from the
--    intake handoff); the others require a human disposition.  A SATISFIED
--    gate is immutable; an OPEN gate may only transition to SATISFIED.
create table public.human_gates (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  gate_type text not null check (
    gate_type in (
      'G1_INTAKE_COMPLETE',
      'G2_GAP_REQUEST_APPROVAL',
      'G3_RISK_DISPOSITION',
      'G4_OPS_AUTHORIZATION'
    )
  ),
  status text not null default 'OPEN' check (status in ('OPEN', 'SATISFIED')),
  satisfied_by_actor_id uuid,
  disposition_ref text check (
    disposition_ref is null or length(btrim(disposition_ref)) between 1 and 200
  ),
  created_at timestamptz not null default clock_timestamp(),
  satisfied_at timestamptz,
  constraint human_gates_case_gate_key unique (case_id, case_version, gate_type),
  constraint human_gates_satisfaction_pair check (
    (status = 'OPEN' and satisfied_at is null
      and satisfied_by_actor_id is null and disposition_ref is null)
    or (status = 'SATISFIED' and satisfied_at is not null)
  ),
  constraint human_gates_satisfaction_after_creation check (
    satisfied_at is null or satisfied_at >= created_at
  )
);

create index human_gates_case_idx
  on public.human_gates (case_id, case_version, gate_type);

create or replace function public.protect_human_gate_transitions()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'human gates cannot be deleted';
  end if;

  if old.status = 'SATISFIED' then
    raise exception using
      errcode = '42501',
      message = 'a satisfied human gate is immutable';
  end if;

  if new.status = 'OPEN' and row(new.*) is distinct from row(old.*) then
    raise exception using
      errcode = '42501',
      message = 'an open human gate may only transition to SATISFIED';
  end if;

  if new.id <> old.id
    or new.case_id <> old.case_id
    or new.case_version <> old.case_version
    or new.gate_type <> old.gate_type
    or new.created_at <> old.created_at then
    raise exception using
      errcode = '42501',
      message = 'human gate identity is immutable';
  end if;

  return new;
end;
$$;

revoke all on function public.protect_human_gate_transitions() from public;

create trigger human_gates_guarded_transitions
before update or delete on public.human_gates
for each row execute function public.protect_human_gate_transitions();
