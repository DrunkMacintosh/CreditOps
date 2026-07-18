-- pgTAP: underwriting_assessments append-only store + additive handoff state.
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(15);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000a1',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000a1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type, status,
  max_attempts, input_schema_version, input_payload, idempotency_key
)
values (
  '30000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 1, null, 'CREDIT_UNDERWRITING',
  'RUNNING', 3, '1', '{}'::jsonb, 'ORCH:case-a1:1:CREDIT_UNDERWRITING'
);

-- 1. A maker assessment persists with full provenance columns.
insert into public.underwriting_assessments (
  id, case_id, case_version, task_id, execution_id,
  prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
)
values (
  '50000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 1,
  '30000000-0000-0000-0000-0000000000a1',
  '40000000-0000-0000-0000-0000000000a1',
  'underwriting-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
  '{"business":{"findings":[]}}'::jsonb,
  clock_timestamp()
);

select is(
  (select count(*) from public.underwriting_assessments),
  1::bigint,
  'a maker assessment row persists'
);

-- 2. Append-only: no update, no delete.
select throws_ok(
  $$update public.underwriting_assessments
    set assessment = '{"business":{"findings":["rewritten"]}}'::jsonb$$,
  '42501',
  null,
  'assessments are append-only (no update)'
);

select throws_ok(
  $$delete from public.underwriting_assessments$$,
  '42501',
  null,
  'assessments are append-only (no delete)'
);

-- 3. One assessment per (case, version, task).
select throws_ok(
  $$insert into public.underwriting_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 1,
      '30000000-0000-0000-0000-0000000000a1',
      '40000000-0000-0000-0000-0000000000a2',
      'underwriting-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23505',
  null,
  'duplicate delivery cannot create a second assessment for the same task'
);

-- 4. The role column is pinned to the maker role.
select throws_ok(
  $$insert into public.underwriting_assessments (
      case_id, case_version, task_id, execution_id, agent_role,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 1,
      '30000000-0000-0000-0000-0000000000a1',
      '40000000-0000-0000-0000-0000000000a3',
      'INDEPENDENT_RISK_REVIEW',
      'underwriting-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23514',
  null,
  'the assessment role is pinned to CREDIT_UNDERWRITING'
);

-- 5. The assessment binds to a real task of the same case and version.
select throws_ok(
  $$insert into public.underwriting_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 1,
      '30000000-0000-0000-0000-0000000000ff',
      '40000000-0000-0000-0000-0000000000a4',
      'underwriting-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23503',
  null,
  'an assessment must reference its producing task (composite case FK)'
);

-- 6. Additive handoff state: the maker->checker package state is accepted.
insert into public.handoffs (
  id, case_id, case_version, source_task_id, state,
  handoff_data, created_by_type
)
values (
  '60000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 1,
  '30000000-0000-0000-0000-0000000000a1',
  'READY_FOR_RISK_REVIEW',
  '{"assessmentId":"50000000-0000-0000-0000-0000000000a1"}'::jsonb,
  'AGENT:CREDIT_UNDERWRITING'
);

select is(
  (
    select count(*) from public.handoffs
    where state = 'READY_FOR_RISK_REVIEW'
  ),
  1::bigint,
  'a maker handoff persists in state READY_FOR_RISK_REVIEW'
);

-- 7. The original intake state remains valid (additive change).
insert into public.handoffs (
  case_id, case_version, source_task_id, state,
  handoff_data, created_by_type
)
values (
  '10000000-0000-0000-0000-0000000000a1', 1,
  '30000000-0000-0000-0000-0000000000a1',
  'READY_FOR_SPECIALIST_REVIEW',
  '{"candidates":[]}'::jsonb,
  'AGENT:INTAKE'
);

select is(
  (
    select count(*) from public.handoffs
    where state = 'READY_FOR_SPECIALIST_REVIEW'
  ),
  1::bigint,
  'the intake handoff state is still valid after the additive extension'
);

-- 8. Unknown states remain rejected.
select throws_ok(
  $$insert into public.handoffs (
      case_id, case_version, source_task_id, state,
      handoff_data, created_by_type
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 1,
      '30000000-0000-0000-0000-0000000000a1',
      'READY_FOR_APPROVAL',
      '{}'::jsonb, 'AGENT:CREDIT_UNDERWRITING'
    )$$,
  '23514',
  null,
  'handoff states outside the closed set are rejected'
);

-- 9. Maker handoff content is immutable (state, data, binding).
select throws_ok(
  $$update public.handoffs
    set handoff_data = '{"assessmentId":"tampered"}'::jsonb
    where state = 'READY_FOR_RISK_REVIEW'$$,
  '42501',
  null,
  'maker handoff content is immutable'
);

select throws_ok(
  $$update public.handoffs set state = 'READY_FOR_SPECIALIST_REVIEW'
    where state = 'READY_FOR_RISK_REVIEW'$$,
  '42501',
  null,
  'a maker handoff cannot be re-labelled into another state'
);

select throws_ok(
  $$delete from public.handoffs where state = 'READY_FOR_RISK_REVIEW'$$,
  '42501',
  null,
  'maker handoffs cannot be deleted'
);

-- 10. RLS: the assigned officer reads; an unassigned actor sees nothing.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is(
  (select count(*) from public.underwriting_assessments),
  1::bigint,
  'the assigned officer can read the case assessment'
);

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000099',
  true
);

select is(
  (select count(*) from public.underwriting_assessments),
  0::bigint,
  'an unassigned actor cannot read any assessment'
);

select throws_ok(
  $$insert into public.underwriting_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 1,
      '30000000-0000-0000-0000-0000000000a1',
      '40000000-0000-0000-0000-0000000000a5',
      'underwriting-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '42501',
  null,
  'authenticated users cannot write assessments (service role only)'
);

reset role;

select * from finish();
rollback;
