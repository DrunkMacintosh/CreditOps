-- pgTAP: controlled_check_records inbox-dedup key
-- (case_id, case_version, task_id, check_type).  Duplicate delivery of the
-- same check for one task is rejected, while the distinct check types a single
-- task legitimately produces (KYC / AML_WATCHLIST / RELATED_PARTY) all persist.
-- All data below is synthetic and created solely for demonstration.

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(4);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000c1',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type, status,
  max_attempts, input_schema_version, input_payload, idempotency_key
)
values (
  '30000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1, null, 'LEGAL_COMPLIANCE_COLLATERAL',
  'RUNNING', 3, '1', '{}'::jsonb, 'ORCH:case-c1:1:LEGAL_COMPLIANCE_COLLATERAL'
);

-- 1. The first controlled-check record for a (task, check_type) persists.
insert into public.controlled_check_records (
  id, case_id, case_version, task_id, check_type, provider_id, tool_name,
  tool_version, subject_type, subject_ref_vi, status, result_summary_vi,
  invoked_at
)
values (
  '70000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1,
  '30000000-0000-0000-0000-0000000000c1',
  'KYC', 'synthetic-mock-compliance-provider', 'synthetic-kyc-mock', 'mock-v1',
  'ENTITY', 'Cong ty TNHH Thuong Mai Dich Vu An Phat Demo', 'CLEAR',
  'Khong phat hien trong du lieu mo phong.', clock_timestamp()
);

select is(
  (select count(*) from public.controlled_check_records),
  1::bigint,
  'a controlled-check record persists'
);

-- 2. Duplicate delivery of the SAME check for the SAME task is rejected: a
-- fresh id cannot create a second row for (case, version, task, KYC).
select throws_ok(
  $$insert into public.controlled_check_records (
      id, case_id, case_version, task_id, check_type, provider_id, tool_name,
      tool_version, subject_type, subject_ref_vi, status, result_summary_vi,
      invoked_at
    ) values (
      '70000000-0000-0000-0000-0000000000c2',
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      'KYC', 'synthetic-mock-compliance-provider', 'synthetic-kyc-mock', 'mock-v1',
      'ENTITY', 'Cong ty TNHH Thuong Mai Dich Vu An Phat Demo', 'CLEAR',
      'Ket qua trung lap mo phong.', clock_timestamp()
    )$$,
  '23505',
  null,
  'a duplicate (case, version, task, check_type) delivery is rejected'
);

-- 3. A DIFFERENT check_type for the SAME task is allowed: one task writes one
-- record per check type, so the dedup key includes check_type.
insert into public.controlled_check_records (
  id, case_id, case_version, task_id, check_type, provider_id, tool_name,
  tool_version, subject_type, subject_ref_vi, status, result_summary_vi,
  invoked_at
)
values (
  '70000000-0000-0000-0000-0000000000c3',
  '10000000-0000-0000-0000-0000000000c1', 1,
  '30000000-0000-0000-0000-0000000000c1',
  'AML_WATCHLIST', 'synthetic-mock-compliance-provider', 'synthetic-aml-mock',
  'mock-v1', 'ENTITY', 'Cong ty TNHH Thuong Mai Dich Vu An Phat Demo',
  'CLEAR', 'Khong phat hien trong du lieu mo phong.', clock_timestamp()
);

select is(
  (select count(*) from public.controlled_check_records),
  2::bigint,
  'a distinct check_type for the same task persists alongside the first'
);

-- 4. Duplicate delivery of the second check is likewise rejected.
select throws_ok(
  $$insert into public.controlled_check_records (
      id, case_id, case_version, task_id, check_type, provider_id, tool_name,
      tool_version, subject_type, subject_ref_vi, status, result_summary_vi,
      invoked_at
    ) values (
      '70000000-0000-0000-0000-0000000000c4',
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      'AML_WATCHLIST', 'synthetic-mock-compliance-provider', 'synthetic-aml-mock',
      'mock-v1', 'ENTITY', 'Cong ty TNHH Thuong Mai Dich Vu An Phat Demo',
      'CLEAR', 'Ket qua trung lap mo phong.', clock_timestamp()
    )$$,
  '23505',
  null,
  'a duplicate AML_WATCHLIST delivery for the same task is rejected'
);

select * from finish();
rollback;
