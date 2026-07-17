begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(9);

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

insert into public.financing_requests (
  id,
  case_id,
  case_version,
  request_version,
  requested_amount,
  purpose_vi,
  created_by
)
values (
  '20000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  1,
  1,
  5000000000,
  'Bổ sung vốn lưu động',
  '00000000-0000-0000-0000-000000000001'
);

set local role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  true
);

select is(
  (select count(*) from public.financing_requests),
  0::bigint,
  'an unassigned officer cannot discover a financing request'
);

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is(
  (select count(*) from public.financing_requests),
  1::bigint,
  'the active assigned officer can read the financing request'
);

select throws_ok(
  $$insert into public.financing_requests (
      case_id, case_version, request_version, requested_amount, purpose_vi, created_by
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, 2, 1, 'Không được phép',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '42501',
  null,
  'an authenticated officer cannot write a financing request directly'
);

reset role;

select throws_ok(
  $$update public.financing_requests set purpose_vi = 'MUTATED'$$,
  '42501',
  'financing requests are append-only',
  'a privileged update cannot mutate a financing request version'
);

select throws_ok(
  $$delete from public.financing_requests$$,
  '42501',
  'financing requests are append-only',
  'a privileged delete cannot remove a financing request version'
);

set local role service_role;

select lives_ok(
  $$update public.credit_cases
    set case_version = 2
    where id = '10000000-0000-0000-0000-000000000001'$$,
  'a case version can advance while its historical financing request remains'
);

select lives_ok(
  $$insert into public.financing_requests (
      case_id, case_version, request_version, requested_amount, purpose_vi, created_by
    ) values (
      '10000000-0000-0000-0000-000000000001', 2, 2, 1, 'Phiên bản tiếp theo',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  'the service role can append a financing request version'
);

reset role;

select is(
  (
    select count(*)
    from public.financing_requests
    where case_id = '10000000-0000-0000-0000-000000000001'
  ),
  2::bigint,
  'advancing the case preserves every financing request version'
);

select throws_ok(
  $$insert into public.financing_requests (
      case_id, case_version, request_version, requested_amount, purpose_vi, created_by
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, 3, 1, 'Sai phiên bản hồ sơ',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  'financing request case_version must match the current credit case version',
  'a new financing request must bind to the current case version'
);

select * from finish();
rollback;
