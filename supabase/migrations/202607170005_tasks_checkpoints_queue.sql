-- CONFIRMED queue contract: messages carry identifiers and are leased with
-- pgmq.read, then retained through pgmq.archive after durable completion.

create table public.processing_tasks (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  document_version_id uuid,
  task_type text not null check (length(btrim(task_type)) > 0),
  status text not null default 'PENDING' check (
    status in (
      'PENDING',
      'RUNNING',
      'RETRY_WAIT',
      'SUCCEEDED',
      'FAILED_MANUAL_REVIEW',
      'SUPERSEDED'
    )
  ),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  max_attempts integer not null check (max_attempts > 0),
  available_at timestamptz not null default clock_timestamp(),
  lease_token uuid,
  lease_until timestamptz,
  input_schema_version text not null default '1' check (length(input_schema_version) > 0),
  input_payload jsonb not null check (jsonb_typeof(input_payload) = 'object'),
  idempotency_key text not null check (length(btrim(idempotency_key)) > 0),
  failure_reason text check (failure_reason is null or length(failure_reason) between 1 and 2000),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  completed_at timestamptz,
  constraint processing_tasks_document_case_fk
    foreign key (document_version_id, case_id, case_version)
    references public.document_versions(id, case_id, case_version)
    on delete restrict,
  constraint processing_tasks_case_idempotency_key unique (case_id, idempotency_key),
  constraint processing_tasks_id_case_version_key unique (id, case_id, case_version),
  constraint processing_tasks_lease_pair check (
    (lease_token is null and lease_until is null)
    or (lease_token is not null and lease_until is not null)
  ),
  constraint processing_tasks_completion_after_creation check (
    completed_at is null or completed_at >= created_at
  )
);

create index processing_tasks_ready_idx
  on public.processing_tasks (status, available_at, created_at)
  where status in ('PENDING', 'RETRY_WAIT');
create index processing_tasks_case_idx
  on public.processing_tasks (case_id, case_version, created_at);

create table public.task_checkpoints (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  document_version_id uuid not null,
  sequence_no integer not null check (sequence_no > 0),
  checkpoint_type text not null check (length(btrim(checkpoint_type)) > 0),
  checkpoint_schema_version text not null default '1'
    check (length(checkpoint_schema_version) > 0),
  checkpoint_data jsonb not null check (jsonb_typeof(checkpoint_data) = 'object'),
  created_at timestamptz not null default clock_timestamp(),
  constraint task_checkpoints_task_case_fk
    foreign key (task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  constraint task_checkpoints_document_case_fk
    foreign key (document_version_id, case_id, case_version)
    references public.document_versions(id, case_id, case_version)
    on delete restrict,
  constraint task_checkpoints_task_sequence_key unique (task_id, sequence_no)
);

create index task_checkpoints_case_task_idx
  on public.task_checkpoints (case_id, task_id, sequence_no);

create table public.idempotency_records (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  actor_id uuid not null,
  operation text not null check (length(btrim(operation)) > 0),
  idempotency_key text not null check (length(btrim(idempotency_key)) > 0),
  request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  response_status integer check (response_status between 100 and 599),
  response_schema_version text check (
    response_schema_version is null or length(response_schema_version) > 0
  ),
  response_data jsonb,
  created_at timestamptz not null default clock_timestamp(),
  completed_at timestamptz,
  expires_at timestamptz,
  constraint idempotency_records_actor_operation_key
    unique (actor_id, operation, idempotency_key),
  constraint idempotency_records_completion_pair check (
    (completed_at is null and response_status is null and response_data is null)
    or (completed_at is not null and response_status is not null and response_data is not null)
  ),
  constraint idempotency_records_time_order check (
    (completed_at is null or completed_at >= created_at)
    and (expires_at is null or expires_at >= created_at)
  )
);

create index idempotency_records_case_created_idx
  on public.idempotency_records (case_id, created_at);

create table public.worker_slots (
  slot_no integer primary key check (slot_no = 1),
  lease_owner uuid,
  lease_token uuid,
  lease_until timestamptz,
  updated_at timestamptz not null default clock_timestamp(),
  constraint worker_slots_complete_lease check (
    (lease_owner is null and lease_token is null and lease_until is null)
    or (lease_owner is not null and lease_token is not null and lease_until is not null)
  )
);

insert into public.worker_slots (slot_no) values (1);

create or replace function public.try_acquire_worker_slot(
  requested_lease_owner uuid,
  requested_lease_token uuid,
  requested_lease_until timestamptz
)
returns boolean
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  updated_rows integer;
begin
  if requested_lease_owner is null
    or requested_lease_token is null
    or requested_lease_until <= statement_timestamp() then
    raise exception using
      errcode = '22023',
      message = 'worker lease requires owner, token, and a future expiry';
  end if;

  update public.worker_slots
  set lease_owner = requested_lease_owner,
      lease_token = requested_lease_token,
      lease_until = requested_lease_until,
      updated_at = clock_timestamp()
  where slot_no = 1
    and (
      lease_until is null
      or lease_until <= statement_timestamp()
      or (
        lease_owner = requested_lease_owner
        and lease_token = requested_lease_token
      )
    );

  get diagnostics updated_rows = row_count;
  return updated_rows = 1;
end;
$$;

revoke all on function public.try_acquire_worker_slot(uuid, uuid, timestamptz)
  from public, anon, authenticated;
grant execute on function public.try_acquire_worker_slot(uuid, uuid, timestamptz)
  to service_role;

select pgmq.create('creditops_document_tasks');

revoke all on schema pgmq from public, anon, authenticated;
revoke all on all tables in schema pgmq from anon, authenticated;
revoke execute on all functions in schema pgmq
  from public, anon, authenticated, service_role;
grant usage on schema pgmq to service_role;
grant execute on function pgmq.send(text, jsonb, integer) to service_role;
grant execute on function pgmq.read(text, integer, integer) to service_role;
grant execute on function pgmq.archive(text, bigint) to service_role;

alter table public.processing_tasks enable row level security;
alter table public.processing_tasks force row level security;
alter table public.task_checkpoints enable row level security;
alter table public.task_checkpoints force row level security;
alter table public.idempotency_records enable row level security;
alter table public.idempotency_records force row level security;
alter table public.worker_slots enable row level security;
alter table public.worker_slots force row level security;

create policy processing_tasks_select_assigned on public.processing_tasks
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = processing_tasks.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy task_checkpoints_select_assigned on public.task_checkpoints
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = task_checkpoints.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy idempotency_records_select_own_assigned
on public.idempotency_records
for select to authenticated
using (
  actor_id = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = idempotency_records.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on
  public.processing_tasks,
  public.task_checkpoints,
  public.idempotency_records,
  public.worker_slots
from public, anon, authenticated;

grant select on
  public.processing_tasks,
  public.task_checkpoints,
  public.idempotency_records
to authenticated;

grant all on
  public.processing_tasks,
  public.task_checkpoints,
  public.idempotency_records,
  public.worker_slots
to service_role;
