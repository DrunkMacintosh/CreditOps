-- Transactional outbox (master design section 14.2).  A material command
-- commits its domain mutation and one outbox event in the SAME transaction;
-- a separate dispatcher publishes to the durable queue afterwards, so a
-- crash between commit and send can never strand invisible work.
--
-- Core columns are append-only.  Dispatch bookkeeping (dispatched_at,
-- dispatch_attempts) is the ONLY mutable surface, and a dispatched event can
-- never revert to undispatched.  Payloads carry identifier-only envelopes,
-- never document bodies or secrets.

create table public.outbox_events (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  event_type text not null check (
    event_type in ('TASK_READY')
  ),
  event_schema_version text not null default '1'
    check (length(btrim(event_schema_version)) between 1 and 20),
  aggregate_type text not null check (
    length(btrim(aggregate_type)) between 1 and 100
  ),
  aggregate_id uuid not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  correlation_id text check (
    correlation_id is null or length(btrim(correlation_id)) between 1 and 200
  ),
  created_at timestamptz not null default clock_timestamp(),
  dispatched_at timestamptz,
  dispatch_attempts integer not null default 0 check (dispatch_attempts >= 0)
);

create index outbox_events_undispatched_idx
  on public.outbox_events (created_at, id)
  where dispatched_at is null;

create index outbox_events_case_idx
  on public.outbox_events (case_id, case_version, created_at);

-- Append-only core: updates may change ONLY dispatch bookkeeping, and only
-- forward (never un-dispatching, never decrementing attempts).  Deletes are
-- rejected outright.
create or replace function public.outbox_events_guard_mutation()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'outbox_events rows are append-only and cannot be deleted'
      using errcode = '42501';
  end if;
  if new.id is distinct from old.id
     or new.case_id is distinct from old.case_id
     or new.case_version is distinct from old.case_version
     or new.event_type is distinct from old.event_type
     or new.event_schema_version is distinct from old.event_schema_version
     or new.aggregate_type is distinct from old.aggregate_type
     or new.aggregate_id is distinct from old.aggregate_id
     or new.payload is distinct from old.payload
     or new.correlation_id is distinct from old.correlation_id
     or new.created_at is distinct from old.created_at then
    raise exception 'outbox_events core columns are immutable'
      using errcode = '42501';
  end if;
  if old.dispatched_at is not null
     and new.dispatched_at is distinct from old.dispatched_at then
    raise exception 'a dispatched outbox event cannot be re-dispatched or reverted'
      using errcode = '42501';
  end if;
  if new.dispatch_attempts < old.dispatch_attempts then
    raise exception 'outbox dispatch attempts can never decrease'
      using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger outbox_events_mutation_guard
before update or delete on public.outbox_events
for each row execute function public.outbox_events_guard_mutation();

-- RLS: workforce users never read the outbox; only the backend service role
-- may touch it.
alter table public.outbox_events enable row level security;
alter table public.outbox_events force row level security;

revoke all on public.outbox_events from public, anon, authenticated;
grant all on public.outbox_events to service_role;
