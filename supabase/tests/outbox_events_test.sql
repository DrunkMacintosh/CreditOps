begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(10);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

-- A TASK_READY event with an identifier-only payload inserts cleanly.
insert into public.outbox_events (
  id, case_id, case_version, event_type, aggregate_type, aggregate_id, payload
) values (
  '40000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001', 1, 'TASK_READY',
  'PROCESSING_TASK', '30000000-0000-0000-0000-000000000001',
  '{"schema_version": "1", "task_id": "30000000-0000-0000-0000-000000000001"}'::jsonb
);

select is(
  (select count(*)::int from public.outbox_events where dispatched_at is null),
  1,
  'a fresh outbox event is undispatched'
);

select throws_ok(
  $$insert into public.outbox_events (
      case_id, case_version, event_type, aggregate_type, aggregate_id, payload
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, 'UNKNOWN_EVENT',
      'PROCESSING_TASK', '30000000-0000-0000-0000-000000000002', '{}'::jsonb
    )$$,
  '23514',
  null,
  'an unknown event type violates the closed event-type registry'
);

select throws_ok(
  $$insert into public.outbox_events (
      case_id, case_version, event_type, aggregate_type, aggregate_id, payload
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, 'TASK_READY',
      'PROCESSING_TASK', '30000000-0000-0000-0000-000000000003', '[]'::jsonb
    )$$,
  '23514',
  null,
  'a non-object payload is rejected'
);

-- Dispatch bookkeeping moves forward.
update public.outbox_events
set dispatch_attempts = 1
where id = '40000000-0000-0000-0000-000000000001';

select is(
  (select dispatch_attempts from public.outbox_events
   where id = '40000000-0000-0000-0000-000000000001'),
  1,
  'a dispatch failure increments attempts'
);

select throws_ok(
  $$update public.outbox_events
    set dispatch_attempts = 0
    where id = '40000000-0000-0000-0000-000000000001'$$,
  '42501',
  null,
  'dispatch attempts can never decrease'
);

select throws_ok(
  $$update public.outbox_events
    set payload = '{"tampered": true}'::jsonb
    where id = '40000000-0000-0000-0000-000000000001'$$,
  '42501',
  null,
  'the committed payload is immutable'
);

update public.outbox_events
set dispatched_at = clock_timestamp()
where id = '40000000-0000-0000-0000-000000000001';

select is(
  (select count(*)::int from public.outbox_events where dispatched_at is null),
  0,
  'marking dispatched removes the event from the undispatched set'
);

select throws_ok(
  $$update public.outbox_events
    set dispatched_at = null
    where id = '40000000-0000-0000-0000-000000000001'$$,
  '42501',
  null,
  'a dispatched event can never revert to undispatched'
);

select throws_ok(
  $$delete from public.outbox_events
    where id = '40000000-0000-0000-0000-000000000001'$$,
  '42501',
  null,
  'outbox events are append-only'
);

-- Workforce users have no access at all: RLS is forced and no policy exists.
select is(
  (select count(*)::int from pg_policies
   where schemaname = 'public' and tablename = 'outbox_events'),
  0,
  'no workforce RLS policy exposes the outbox'
);

select * from finish();
rollback;
