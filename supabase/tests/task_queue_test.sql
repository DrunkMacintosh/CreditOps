begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(6);

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

select * from finish();
rollback;
