-- pgTAP: legal_compliance_assessments + policy_corpus_versions +
-- controlled_check_records append-only stores.  All data below is synthetic
-- and created solely for demonstration; the case belongs to the invented SME
-- "Cong ty TNHH Thuong Mai Dich Vu An Phat Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(20);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000b1',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000b1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type, status,
  max_attempts, input_schema_version, input_payload, idempotency_key
)
values (
  '30000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1, null, 'LEGAL_COMPLIANCE_COLLATERAL',
  'RUNNING', 3, '1', '{}'::jsonb, 'ORCH:case-b1:1:LEGAL_COMPLIANCE_COLLATERAL'
);

-- 1. A reviewer assessment persists with full provenance columns.
insert into public.legal_compliance_assessments (
  id, case_id, case_version, task_id, execution_id,
  prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
)
values (
  '50000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  '30000000-0000-0000-0000-0000000000b1',
  '40000000-0000-0000-0000-0000000000b1',
  'legal-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
  '{"legal_entity_review":{"findings":[]}}'::jsonb,
  clock_timestamp()
);

select is(
  (select count(*) from public.legal_compliance_assessments),
  1::bigint,
  'a reviewer assessment row persists'
);

-- 2. Append-only: no update, no delete.
select throws_ok(
  $$update public.legal_compliance_assessments
    set assessment = '{"legal_entity_review":{"findings":["rewritten"]}}'::jsonb$$,
  '42501',
  null,
  'assessments are append-only (no update)'
);

select throws_ok(
  $$delete from public.legal_compliance_assessments$$,
  '42501',
  null,
  'assessments are append-only (no delete)'
);

-- 3. One assessment per (case, version, task).
select throws_ok(
  $$insert into public.legal_compliance_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      '40000000-0000-0000-0000-0000000000b2',
      'legal-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23505',
  null,
  'duplicate delivery cannot create a second assessment for the same task'
);

-- 4. The role column is pinned to the reviewer role.
select throws_ok(
  $$insert into public.legal_compliance_assessments (
      case_id, case_version, task_id, execution_id, agent_role,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      '40000000-0000-0000-0000-0000000000b3',
      'CREDIT_UNDERWRITING',
      'legal-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23514',
  null,
  'the assessment role is pinned to LEGAL_COMPLIANCE_COLLATERAL'
);

-- 5. The assessment binds to a real task of the same case and version.
select throws_ok(
  $$insert into public.legal_compliance_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000ff',
      '40000000-0000-0000-0000-0000000000b4',
      'legal-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23503',
  null,
  'an assessment must reference its producing task (composite case FK)'
);

-- 6. The reviewer feeds the same specialist->checker handoff state as the
-- underwriting maker (READY_FOR_RISK_REVIEW, already accepted since 202607180003).
insert into public.handoffs (
  id, case_id, case_version, source_task_id, state,
  handoff_data, created_by_type
)
values (
  '60000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  '30000000-0000-0000-0000-0000000000b1',
  'READY_FOR_RISK_REVIEW',
  '{"assessmentId":"50000000-0000-0000-0000-0000000000b1"}'::jsonb,
  'AGENT:LEGAL_COMPLIANCE_COLLATERAL'
);

select is(
  (
    select count(*) from public.handoffs
    where state = 'READY_FOR_RISK_REVIEW'
      and created_by_type = 'AGENT:LEGAL_COMPLIANCE_COLLATERAL'
  ),
  1::bigint,
  'a reviewer handoff persists in state READY_FOR_RISK_REVIEW'
);

-- 7. Policy corpus version registry: an upsert records the loaded version.
insert into public.policy_corpus_versions (
  corpus_id, version, checksum_sha256, active, is_synthetic
)
values (
  'SHB-SYNTHETIC-POLICY-CORPUS', 'v1',
  '2bad8a80bf0b2352569bbe7e29cf6af46426c3e7a366da7d901e26c3b61cdede',
  true, true
);

select is(
  (select is_synthetic from public.policy_corpus_versions
   where corpus_id = 'SHB-SYNTHETIC-POLICY-CORPUS' and version = 'v1'),
  true,
  'a loaded policy corpus version is labelled synthetic'
);

select throws_ok(
  $$insert into public.policy_corpus_versions (
      corpus_id, version, checksum_sha256
    ) values ('SHB-SYNTHETIC-POLICY-CORPUS', 'v1',
              '2bad8a80bf0b2352569bbe7e29cf6af46426c3e7a366da7d901e26c3b61cdede')$$,
  '23505',
  null,
  'a corpus version is registered at most once per (corpus_id, version)'
);

select throws_ok(
  $$insert into public.policy_corpus_versions (
      corpus_id, version, checksum_sha256
    ) values ('SHB-SYNTHETIC-POLICY-CORPUS', 'v2', 'not-a-valid-hex-checksum')$$,
  '23514',
  null,
  'the checksum column rejects a non-hex-64 value'
);

-- 8. Controlled-check records: append-only, mock-only.
insert into public.controlled_check_records (
  id, case_id, case_version, task_id, check_type, provider_id, tool_name,
  tool_version, subject_type, subject_ref_vi, status, result_summary_vi,
  invoked_at
)
values (
  '70000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  '30000000-0000-0000-0000-0000000000b1',
  'KYC', 'synthetic-mock-compliance-provider', 'synthetic-kyc-mock', 'mock-v1',
  'ENTITY', 'Cong ty TNHH Thuong Mai Dich Vu An Phat Demo', 'CLEAR',
  'Khong phat hien trong du lieu mo phong.', clock_timestamp()
);

select is(
  (select count(*) from public.controlled_check_records),
  1::bigint,
  'a controlled-check record persists'
);

select is(
  (select is_mock from public.controlled_check_records limit 1),
  true,
  'controlled-check records are always marked mock'
);

select throws_ok(
  $$insert into public.controlled_check_records (
      id, case_id, case_version, task_id, check_type, provider_id, tool_name,
      tool_version, subject_type, subject_ref_vi, status, result_summary_vi,
      is_mock, invoked_at
    ) values (
      '70000000-0000-0000-0000-0000000000b2',
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      'KYC', 'real-provider', 'real-kyc-tool', 'v9',
      'ENTITY', 'x', 'CLEAR', 'x', false, clock_timestamp()
    )$$,
  '23514',
  null,
  'a controlled-check record cannot be inserted with is_mock = false'
);

select throws_ok(
  $$insert into public.controlled_check_records (
      id, case_id, case_version, task_id, check_type, provider_id, tool_name,
      tool_version, subject_type, subject_ref_vi, status, result_summary_vi,
      invoked_at
    ) values (
      '70000000-0000-0000-0000-0000000000b3',
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      'SANCTIONS', 'synthetic-mock-compliance-provider', 'x', 'v1',
      'ENTITY', 'x', 'CLEAR', 'x', clock_timestamp()
    )$$,
  '23514',
  null,
  'check_type outside the closed set is rejected'
);

select throws_ok(
  $$update public.controlled_check_records set status = 'HIT'$$,
  '42501',
  null,
  'controlled-check records are append-only (no update)'
);

select throws_ok(
  $$delete from public.controlled_check_records$$,
  '42501',
  null,
  'controlled-check records are append-only (no delete)'
);

-- 9. RLS: the assigned officer reads; an unassigned actor sees nothing.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is(
  (select count(*) from public.legal_compliance_assessments),
  1::bigint,
  'the assigned officer can read the case legal assessment'
);

select is(
  (select count(*) from public.controlled_check_records),
  1::bigint,
  'the assigned officer can read the case controlled-check records'
);

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000099',
  true
);

select is(
  (select count(*) from public.legal_compliance_assessments),
  0::bigint,
  'an unassigned actor cannot read any legal assessment'
);

select throws_ok(
  $$insert into public.legal_compliance_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      '40000000-0000-0000-0000-0000000000b5',
      'legal-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '42501',
  null,
  'authenticated users cannot write assessments (service role only)'
);

reset role;

select * from finish();
rollback;
