begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(6);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

-- An assignment written without a role (the prototype intake path) takes the default.
insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

select is(
  (
    select case_role
    from public.case_assignments
    where case_id = '10000000-0000-0000-0000-000000000001'
      and officer_id = '00000000-0000-0000-0000-000000000001'
  ),
  'INTAKE_OFFICER',
  'an assignment written without a role backfills to the prototype INTAKE_OFFICER default'
);

select throws_ok(
  $$insert into public.case_assignments (case_id, officer_id, assigned_by, case_role)
    values (
      '10000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000002',
      '00000000-0000-0000-0000-000000000010',
      'CHIEF_WIZARD'
    )$$,
  '23514',
  null,
  'a case role outside the closed synthetic set is rejected'
);

-- The same officer may hold a second, distinct role on the same case.
insert into public.case_assignments (case_id, officer_id, assigned_by, case_role)
values (
  '10000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010',
  'UNDERWRITER'
);

select is(
  (
    select count(*)::int
    from public.case_assignments
    where case_id = '10000000-0000-0000-0000-000000000001'
      and officer_id = '00000000-0000-0000-0000-000000000001'
  ),
  2,
  'one officer may hold two distinct roles on a single case'
);

select throws_ok(
  $$insert into public.case_assignments (case_id, officer_id, assigned_by, case_role)
    values (
      '10000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000010',
      'UNDERWRITER'
    )$$,
  '23505',
  null,
  'a duplicate (case, officer, role) assignment is rejected'
);

-- The redundant flat-key FK from fact_confirmations is gone; the ACTIVE-
-- assignment trigger remains the (stronger) enforcement layer.
select hasnt_fk(
  'public', 'fact_confirmations', 'fact_confirmations_assignment_fk',
  'the flat-assignment FK was dropped with the flat unique key'
);

select has_trigger(
  'public', 'fact_confirmations', 'fact_confirmations_enforce_active_authority',
  'the active-authority trigger still guards confirmations'
);

select * from finish();
rollback;
