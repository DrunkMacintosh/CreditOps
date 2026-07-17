begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(15);

select has_extension('pgmq', 'the PGMQ extension is installed');

select is(
  (
    select count(*)
    from pgmq.meta
    where queue_name = 'creditops_document_tasks'
  ),
  1::bigint,
  'the logged document-task queue exists exactly once'
);

select has_function('pgmq', 'read', 'workers can lease messages with pgmq.read');
select has_function('pgmq', 'archive', 'workers can archive completed messages');

select ok(
  has_function_privilege(
    'service_role',
    'pgmq.send(text,jsonb,integer)',
    'EXECUTE'
  ),
  'service_role has exact EXECUTE on queue send'
);

select ok(
  has_function_privilege(
    'service_role',
    'pgmq.read(text,integer,integer)',
    'EXECUTE'
  ),
  'service_role has exact EXECUTE on queue read'
);

select ok(
  has_function_privilege(
    'service_role',
    'pgmq.archive(text,bigint)',
    'EXECUTE'
  ),
  'service_role has exact EXECUTE on queue archive'
);

select is(
  (
    select coalesce(bool_or(has_function_privilege('service_role', proc.oid, 'EXECUTE')), false)
    from pg_proc as proc
    join pg_namespace as namespace on namespace.oid = proc.pronamespace
    where namespace.nspname = 'pgmq' and proc.proname = 'pop'
  ),
  false,
  'service_role has no EXECUTE privilege on destructive queue reads'
);

select results_eq(
  $$select slot_no from public.worker_slots order by slot_no$$,
  $$values (1)$$,
  'the durable worker-slot table contains only the global slot'
);

select throws_ok(
  $$insert into public.worker_slots (slot_no) values (2)$$,
  '23514',
  null,
  'a second numbered worker slot violates the single-slot contract'
);

select ok(
  public.try_acquire_worker_slot(
    '50000000-0000-0000-0000-000000000001',
    '51000000-0000-0000-0000-000000000001',
    clock_timestamp() + interval '1 minute'
  ),
  'the first worker atomically acquires the global slot'
);

select is(
  public.try_acquire_worker_slot(
    '50000000-0000-0000-0000-000000000002',
    '51000000-0000-0000-0000-000000000002',
    clock_timestamp() + interval '1 minute'
  ),
  false,
  'a concurrent worker cannot acquire the leased global slot'
);

create temporary table leased_queue_message as
select *
from pgmq.read('creditops_document_tasks', 30, 1)
where false;

select pgmq.send(
  'creditops_document_tasks',
  '{"schema_version":"1","task_id":"40000000-0000-0000-0000-000000000001"}'::jsonb
);

insert into leased_queue_message
select * from pgmq.read('creditops_document_tasks', 30, 1);

select is(
  (select count(*) from leased_queue_message),
  1::bigint,
  'pgmq.read leases the sent message'
);

select ok(
  (select pgmq.archive('creditops_document_tasks', msg_id) from leased_queue_message),
  'pgmq.archive durably removes the completed message from the active queue'
);

select is(
  (
    select count(*)
    from pgmq.a_creditops_document_tasks as archived
    join leased_queue_message as leased using (msg_id)
  ),
  1::bigint,
  'the completed message is retained in the queue archive'
);

select * from finish();
rollback;
