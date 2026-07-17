begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(4);

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

set local role authenticated;

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  true
);

select is(
  (select count(*) from public.credit_cases),
  0::bigint,
  'an unassigned officer cannot read a case'
);

select is(
  (select count(*) from public.case_assignments),
  0::bigint,
  'an unassigned officer cannot discover case assignments'
);

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is(
  (select count(*) from public.credit_cases),
  1::bigint,
  'the active assigned officer can read the case'
);

select is(
  (select count(*) from public.case_assignments),
  1::bigint,
  'the assigned officer can read only their own active assignment'
);

select * from finish();
rollback;
