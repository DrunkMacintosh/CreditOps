begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(22);

-- pgTAP is installed in the extensions schema.  The custom creditops_api role
-- (unlike the Supabase-managed authenticated/anon roles) is not granted USAGE
-- there, so once we SET ROLE creditops_api below, pgTAP assertion functions
-- (is/lives_ok/throws_ok) would be unresolvable ("function does not exist").
-- Grant USAGE for this test transaction only (rolled back with the tx); it does
-- not alter the production role and does not affect the RLS behaviour under test.
grant usage on schema extensions to creditops_api;

select is(
  (select rolbypassrls from pg_roles where rolname = 'creditops_api'),
  false,
  'the API role cannot bypass row-level security'
);

select is(
  (select rolcanlogin from pg_roles where rolname = 'creditops_api'),
  false,
  'the API role has no direct login credential'
);

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
  '00000000-0000-0000-0000-000000000001'
);

insert into public.financing_requests (
  case_id,
  case_version,
  request_version,
  requested_amount,
  purpose_vi,
  created_by
)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  1,
  5000000000,
  'Bổ sung vốn lưu động',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.audit_events (
  case_id,
  case_version,
  event_type,
  actor_type,
  actor_id,
  artifact_type,
  artifact_id
)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  'CASE_CREATED',
  'HUMAN',
  '00000000-0000-0000-0000-000000000001',
  'CREDIT_CASE',
  '10000000-0000-0000-0000-000000000001'
);

set local role creditops_api;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  true
);

select is((select count(*) from public.credit_cases), 0::bigint, 'other actor sees no cases');
select is((select count(*) from public.case_assignments), 0::bigint, 'other actor sees no assignments');
select is((select count(*) from public.financing_requests), 0::bigint, 'other actor sees no requests');
select is((select count(*) from public.audit_events), 0::bigint, 'other actor sees no audit');

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is((select count(*) from public.credit_cases), 1::bigint, 'assigned actor sees the case');
select is((select count(*) from public.case_assignments), 1::bigint, 'assigned actor sees their assignment');
select is((select count(*) from public.financing_requests), 1::bigint, 'assigned actor sees the request');
select is((select count(*) from public.audit_events), 1::bigint, 'assigned actor sees the audit event');

select lives_ok(
  $$insert into public.credit_cases (id, workflow_state, created_by)
    values (
      '10000000-0000-0000-0000-000000000002',
      'INTAKE_DRAFT',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  'the API role can create a case for the current actor'
);

select throws_ok(
  $$insert into public.credit_cases (id, workflow_state, created_by)
    values (
      '10000000-0000-0000-0000-000000000003',
      'INTAKE_DRAFT',
      '00000000-0000-0000-0000-000000000002'
    )$$,
  '42501',
  null,
  'the API role cannot create a case for another actor'
);

select lives_ok(
  $$insert into public.case_assignments (case_id, officer_id, assigned_by)
    values (
      '10000000-0000-0000-0000-000000000002',
      '00000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  'the API role can create its active self-assignment'
);

select throws_ok(
  $$insert into public.case_assignments (case_id, officer_id, assigned_by)
    values (
      '10000000-0000-0000-0000-000000000002',
      '00000000-0000-0000-0000-000000000002',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '42501',
  null,
  'the API role cannot assign a case to another actor'
);

select lives_ok(
  $$insert into public.financing_requests (
      case_id, case_version, request_version, requested_amount, purpose_vi, created_by
    ) values (
      '10000000-0000-0000-0000-000000000002', 1, 1, 1, 'Vốn lưu động',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  'the API role can append its assigned financing request'
);

select throws_ok(
  $$insert into public.financing_requests (
      case_id, case_version, request_version, requested_amount, purpose_vi, created_by
    ) values (
      '10000000-0000-0000-0000-000000000002', 1, 2, 1, 'Không được phép',
      '00000000-0000-0000-0000-000000000002'
    )$$,
  '42501',
  null,
  'the API role cannot append a request for another actor'
);

select lives_ok(
  $$insert into public.audit_events (
      case_id, case_version, event_type, actor_type, actor_id, artifact_type, artifact_id
    ) values (
      '10000000-0000-0000-0000-000000000002', 1, 'CASE_CREATED', 'HUMAN',
      '00000000-0000-0000-0000-000000000001', 'CREDIT_CASE',
      '10000000-0000-0000-0000-000000000002'
    )$$,
  'the API role can append its assigned audit event'
);

select throws_ok(
  $$insert into public.audit_events (
      case_id, case_version, event_type, actor_type, actor_id, artifact_type, artifact_id
    ) values (
      '10000000-0000-0000-0000-000000000002', 1, 'CASE_CREATED', 'HUMAN',
      '00000000-0000-0000-0000-000000000002', 'CREDIT_CASE',
      '10000000-0000-0000-0000-000000000002'
    )$$,
  '42501',
  null,
  'the API role cannot append an audit event for another actor'
);

reset role;
update public.case_assignments
set revoked_at = clock_timestamp()
where case_id = '10000000-0000-0000-0000-000000000001';

set local role creditops_api;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is(
  (
    select count(*)
    from public.credit_cases
    where id = '10000000-0000-0000-0000-000000000001'
  ),
  0::bigint,
  'revocation hides the case from its creator'
);

select is(
  (
    select count(*)
    from public.case_assignments
    where case_id = '10000000-0000-0000-0000-000000000001'
  ),
  0::bigint,
  'revocation hides the assignment'
);

select is(
  (
    select count(*)
    from public.financing_requests
    where case_id = '10000000-0000-0000-0000-000000000001'
  ),
  0::bigint,
  'revocation hides financing requests'
);

select is(
  (
    select count(*)
    from public.audit_events
    where case_id = '10000000-0000-0000-0000-000000000001'
  ),
  0::bigint,
  'revocation hides audit events'
);

reset role;
select * from finish();
rollback;
