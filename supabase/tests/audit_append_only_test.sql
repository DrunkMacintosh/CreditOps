begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(5);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

insert into public.audit_events (
  id,
  case_id,
  case_version,
  event_type,
  actor_type,
  actor_id,
  artifact_type,
  artifact_id,
  event_data
)
values (
  '30000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  1,
  'CASE_CREATED',
  'HUMAN',
  '00000000-0000-0000-0000-000000000001',
  'CREDIT_CASE',
  '10000000-0000-0000-0000-000000000001',
  '{}'::jsonb
);

set local role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is(
  (select count(*) from public.audit_events),
  1::bigint,
  'the assigned officer can read case audit events'
);

select throws_ok(
  $$delete from public.audit_events$$,
  '42501',
  null,
  'authenticated audit delete is denied at the privilege boundary'
);

select throws_ok(
  $$update public.audit_events set event_type = 'MUTATED'$$,
  '42501',
  null,
  'authenticated audit update is denied at the privilege boundary'
);

reset role;

select throws_ok(
  $$update public.audit_events set event_type = 'MUTATED'$$,
  '42501',
  'audit events are append-only',
  'the immutable audit trigger rejects a privileged update'
);

select throws_ok(
  $$delete from public.audit_events$$,
  '42501',
  'audit events are append-only',
  'the immutable audit trigger rejects a privileged delete'
);

select * from finish();
rollback;
