-- pgTAP: risk_pre_analyses append-only blind pre-analysis store (Pass A of the
-- two-pass Independent Risk Review).  All data below is synthetic and created
-- solely for demonstration; the case belongs to the invented SME "Cong ty
-- TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(10);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000c1',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000c1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type, status,
  max_attempts, input_schema_version, input_payload, idempotency_key
)
values (
  '30000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1, null, 'INDEPENDENT_RISK_REVIEW',
  'RUNNING', 3, '1', '{}'::jsonb, 'ORCH:case-c1:1:INDEPENDENT_RISK_REVIEW'
);

-- 1. A blind pre-analysis persists with full provenance columns.
insert into public.risk_pre_analyses (
  id, case_id, case_version, task_id, execution_id,
  prompt_version, schema_version, analysis
)
values (
  '50000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1,
  '30000000-0000-0000-0000-0000000000c1',
  '40000000-0000-0000-0000-0000000000c1',
  'risk-pre-analysis-prompt-v1', 'risk-pre-analysis-v1',
  '{"independent_risks":[],"observations":[]}'::jsonb
);

select is(
  (select count(*) from public.risk_pre_analyses),
  1::bigint,
  'a blind pre-analysis row persists'
);

-- 2. Append-only: no update, no delete.
select throws_ok(
  $$update public.risk_pre_analyses
    set analysis = '{"independent_risks":["rewritten"]}'::jsonb$$,
  '42501',
  null,
  'blind pre-analyses are append-only (no update)'
);

select throws_ok(
  $$delete from public.risk_pre_analyses$$,
  '42501',
  null,
  'blind pre-analyses are append-only (no delete)'
);

-- 3. One pre-analysis per (case, version, task): a redelivery that reruns Pass
-- A cannot create a second blind pre-analysis for the same task.
select throws_ok(
  $$insert into public.risk_pre_analyses (
      case_id, case_version, task_id, execution_id,
      prompt_version, schema_version, analysis
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      '40000000-0000-0000-0000-0000000000c2',
      'risk-pre-analysis-prompt-v1', 'risk-pre-analysis-v1', '{}'::jsonb
    )$$,
  '23505',
  null,
  'duplicate delivery cannot create a second blind pre-analysis for the same task'
);

-- 4. The analysis payload must be a JSON object, never a scalar/array.
select throws_ok(
  $$insert into public.risk_pre_analyses (
      case_id, case_version, task_id, execution_id,
      prompt_version, schema_version, analysis
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      '40000000-0000-0000-0000-0000000000c3',
      'risk-pre-analysis-prompt-v1', 'risk-pre-analysis-v1', '["not-an-object"]'::jsonb
    )$$,
  '23514',
  null,
  'a non-object analysis payload is rejected'
);

-- 5. The role column is pinned to the checker role.
select throws_ok(
  $$insert into public.risk_pre_analyses (
      case_id, case_version, task_id, execution_id, agent_role,
      prompt_version, schema_version, analysis
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      '40000000-0000-0000-0000-0000000000c4',
      'CREDIT_UNDERWRITING',
      'risk-pre-analysis-prompt-v1', 'risk-pre-analysis-v1', '{}'::jsonb
    )$$,
  '23514',
  null,
  'the pre-analysis role is pinned to INDEPENDENT_RISK_REVIEW'
);

-- 6. This migration grants NO write access on maker tables.
select is(
  (
    select count(*) from information_schema.role_table_grants
    where table_schema = 'public'
      and table_name in ('underwriting_assessments', 'legal_compliance_assessments')
      and grantee in ('authenticated', 'anon', 'creditops_api')
      and privilege_type in ('INSERT', 'UPDATE', 'DELETE')
  ),
  0::bigint,
  'no write grant exists on any maker assessment table'
);

-- 7. RLS: the assigned officer reads; an unassigned actor sees nothing; writes
-- remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.risk_pre_analyses),
  1::bigint,
  'the assigned officer can read the blind pre-analysis'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.risk_pre_analyses),
  0::bigint,
  'an unassigned actor cannot read any blind pre-analysis'
);

select throws_ok(
  $$insert into public.risk_pre_analyses (
      case_id, case_version, task_id, execution_id,
      prompt_version, schema_version, analysis
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      '40000000-0000-0000-0000-0000000000c9',
      'risk-pre-analysis-prompt-v1', 'risk-pre-analysis-v1', '{}'::jsonb
    )$$,
  '42501',
  null,
  'authenticated users cannot write blind pre-analyses (service role only)'
);

reset role;

select * from finish();
rollback;
