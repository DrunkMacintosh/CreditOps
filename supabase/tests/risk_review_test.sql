-- pgTAP: risk_review_assessments / risk_review_challenges /
-- challenge_dispositions append-only stores + additive handoff state.
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(26);

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
  '10000000-0000-0000-0000-0000000000b1', 1, null, 'INDEPENDENT_RISK_REVIEW',
  'RUNNING', 3, '1', '{}'::jsonb, 'ORCH:case-b1:1:INDEPENDENT_RISK_REVIEW'
);

-- 1. A checker assessment persists with full provenance columns.
insert into public.risk_review_assessments (
  id, case_id, case_version, task_id, execution_id,
  prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
)
values (
  '50000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  '30000000-0000-0000-0000-0000000000b1',
  '40000000-0000-0000-0000-0000000000b1',
  'risk-review-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
  '{"challenges":[]}'::jsonb,
  clock_timestamp()
);

select is(
  (select count(*) from public.risk_review_assessments),
  1::bigint,
  'a checker assessment row persists'
);

-- 2. Append-only: no update, no delete.
select throws_ok(
  $$update public.risk_review_assessments
    set assessment = '{"challenges":["rewritten"]}'::jsonb$$,
  '42501',
  null,
  'checker assessments are append-only (no update)'
);

select throws_ok(
  $$delete from public.risk_review_assessments$$,
  '42501',
  null,
  'checker assessments are append-only (no delete)'
);

-- 3. One assessment per (case, version, task).
select throws_ok(
  $$insert into public.risk_review_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      '40000000-0000-0000-0000-0000000000b2',
      'risk-review-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23505',
  null,
  'duplicate delivery cannot create a second assessment for the same task'
);

-- 4. The role column is pinned to the checker role.
select throws_ok(
  $$insert into public.risk_review_assessments (
      case_id, case_version, task_id, execution_id, agent_role,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      '40000000-0000-0000-0000-0000000000b3',
      'CREDIT_UNDERWRITING',
      'risk-review-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23514',
  null,
  'the assessment role is pinned to INDEPENDENT_RISK_REVIEW'
);

-- 5. A challenge is a first-class, append-only row bound to the assessment.
insert into public.risk_review_challenges (
  id, assessment_id, case_id, case_version,
  target_maker_source, target_maker_assessment_id, target_section_path,
  challenge_type, statement_vi, citations, severity, confidence, raised_by
)
values (
  '70000000-0000-0000-0000-0000000000b1',
  '50000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  'CREDIT_UNDERWRITING', '60000000-0000-0000-0000-0000000000b1', 'risks[0]',
  'UNSUPPORTED_ASSUMPTION', 'Gia dinh chua co can cu (du lieu mo phong).',
  '[{"kind":"MAKER_FINDING"}]'::jsonb, 'HIGH', 'MEDIUM', 'LLM'
);

select is(
  (select count(*) from public.risk_review_challenges),
  1::bigint,
  'a checker challenge row persists'
);

-- 6. Challenges are append-only: no update, no delete.
select throws_ok(
  $$update public.risk_review_challenges set severity = 'LOW'$$,
  '42501',
  null,
  'challenges are append-only (no update)'
);

select throws_ok(
  $$delete from public.risk_review_challenges$$,
  '42501',
  null,
  'challenges are append-only (no delete)'
);

-- 7. A challenge with no citations is rejected at the database level too.
select throws_ok(
  $$insert into public.risk_review_challenges (
      id, assessment_id, case_id, case_version,
      target_maker_source, target_maker_assessment_id, target_section_path,
      challenge_type, statement_vi, citations, severity, confidence, raised_by
    ) values (
      '70000000-0000-0000-0000-0000000000b2',
      '50000000-0000-0000-0000-0000000000b1',
      '10000000-0000-0000-0000-0000000000b1', 1,
      'CREDIT_UNDERWRITING', '60000000-0000-0000-0000-0000000000b1', 'risks[1]',
      'OMITTED_RISK', 'khong co can cu', '[]'::jsonb, 'LOW', 'LOW', 'LLM'
    )$$,
  '23514',
  null,
  'a challenge with no citations is rejected'
);

-- 8. A challenge cannot bind to an assessment/case pair that does not match.
select throws_ok(
  $$insert into public.risk_review_challenges (
      id, assessment_id, case_id, case_version,
      target_maker_source, target_maker_assessment_id, target_section_path,
      challenge_type, statement_vi, citations, severity, confidence, raised_by
    ) values (
      '70000000-0000-0000-0000-0000000000b3',
      '50000000-0000-0000-0000-0000000000b1',
      '10000000-0000-0000-0000-0000000000b1', 2,
      'CREDIT_UNDERWRITING', '60000000-0000-0000-0000-0000000000b1', 'risks[0]',
      'OTHER_CONCERN', 'sai phien ban', '[{"kind":"MAKER_FINDING"}]'::jsonb,
      'LOW', 'LOW', 'DETERMINISTIC'
    )$$,
  '23503',
  null,
  'a challenge must bind to a real (assessment, case, version) triple'
);

-- 9. A challenge-level human disposition persists and never touches the
-- challenge row (b, c: disagreements persist; full history readable).
insert into public.challenge_dispositions (
  assessment_id, case_id, case_version, challenge_id,
  disposition_type, rationale_vi, actor_id, actor_role
)
values (
  '50000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  '70000000-0000-0000-0000-0000000000b1',
  'MAKER_MUST_REVISE', 'Can yeu cau MAKER bo sung can cu (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000099', 'RISK_REVIEWER'
);

select is(
  (select count(*) from public.challenge_dispositions),
  1::bigint,
  'a human disposition persists'
);

select is(
  (select severity from public.risk_review_challenges
   where id = '70000000-0000-0000-0000-0000000000b1'),
  'HIGH',
  'the disposed challenge row itself is untouched by the disposition'
);

-- 10. Dispositions are append-only: no update, no delete.
select throws_ok(
  $$update public.challenge_dispositions set disposition_type = 'NOTED'$$,
  '42501',
  null,
  'dispositions are append-only (no update)'
);

select throws_ok(
  $$delete from public.challenge_dispositions$$,
  '42501',
  null,
  'dispositions are append-only (no delete)'
);

-- 11. An assessment-level disposition (challenge_id null) must be NOTED.
select throws_ok(
  $$insert into public.challenge_dispositions (
      assessment_id, case_id, case_version, challenge_id,
      disposition_type, rationale_vi, actor_id, actor_role
    ) values (
      '50000000-0000-0000-0000-0000000000b1',
      '10000000-0000-0000-0000-0000000000b1', 1, null,
      'ACCEPTED_RISK', 'khong duoc phep', '00000000-0000-0000-0000-000000000099',
      'RISK_REVIEWER'
    )$$,
  '23514',
  null,
  'an assessment-level disposition must be NOTED'
);

insert into public.challenge_dispositions (
  assessment_id, case_id, case_version, challenge_id,
  disposition_type, rationale_vi, actor_id, actor_role
)
values (
  '50000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1, null,
  'NOTED', 'Khong co thach thuc nghiem trong; da xem xet (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000099', 'RISK_REVIEWER'
);

select is(
  (
    select count(*) from public.challenge_dispositions
    where challenge_id is null and disposition_type = 'NOTED'
  ),
  1::bigint,
  'an assessment-level NOTED disposition persists'
);

-- 12. Additive handoff state: the checker->operations package state is
-- accepted, and both prior states remain valid.
insert into public.handoffs (
  id, case_id, case_version, source_task_id, state,
  handoff_data, created_by_type
)
values (
  '80000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  '30000000-0000-0000-0000-0000000000b1',
  'READY_FOR_OPERATIONS',
  '{"assessmentId":"50000000-0000-0000-0000-0000000000b1"}'::jsonb,
  'AGENT:INDEPENDENT_RISK_REVIEW'
);

select is(
  (select count(*) from public.handoffs where state = 'READY_FOR_OPERATIONS'),
  1::bigint,
  'a checker handoff persists in state READY_FOR_OPERATIONS'
);

insert into public.handoffs (
  case_id, case_version, source_task_id, state, handoff_data, created_by_type
)
values (
  '10000000-0000-0000-0000-0000000000b1', 1,
  '30000000-0000-0000-0000-0000000000b1',
  'READY_FOR_RISK_REVIEW', '{"assessmentId":"x"}'::jsonb, 'AGENT:CREDIT_UNDERWRITING'
);

select is(
  (select count(*) from public.handoffs where state = 'READY_FOR_RISK_REVIEW'),
  1::bigint,
  'the maker->checker handoff state is still valid after the additive extension'
);

select throws_ok(
  $$insert into public.handoffs (
      case_id, case_version, source_task_id, state, handoff_data, created_by_type
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      'READY_FOR_MADE_UP_STATE', '{}'::jsonb, 'AGENT:INDEPENDENT_RISK_REVIEW'
    )$$,
  '23514',
  null,
  'handoff states outside the closed set are rejected'
);

-- 13. This migration grants NO write access on maker tables.
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

-- 14. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.risk_review_assessments),
  1::bigint,
  'the assigned officer can read the checker assessment'
);

select is(
  (select count(*) from public.risk_review_challenges),
  1::bigint,
  'the assigned officer can read challenges'
);

select is(
  (select count(*) from public.challenge_dispositions),
  2::bigint,
  'the assigned officer can read dispositions -- (d) unresolved challenges stay visible'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.risk_review_assessments),
  0::bigint,
  'an unassigned actor cannot read any checker assessment'
);

select throws_ok(
  $$insert into public.challenge_dispositions (
      assessment_id, case_id, case_version, challenge_id,
      disposition_type, rationale_vi, actor_id, actor_role
    ) values (
      '50000000-0000-0000-0000-0000000000b1',
      '10000000-0000-0000-0000-0000000000b1', 1,
      '70000000-0000-0000-0000-0000000000b1',
      'ESCALATED', 'khong duoc phep', '00000000-0000-0000-0000-000000000099',
      'RISK_REVIEWER'
    )$$,
  '42501',
  null,
  'authenticated users cannot write dispositions directly (service role only)'
);

select throws_ok(
  $$insert into public.risk_review_assessments (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, assessment, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000b1', 1,
      '30000000-0000-0000-0000-0000000000b1',
      '40000000-0000-0000-0000-0000000000b9',
      'risk-review-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '42501',
  null,
  'authenticated users cannot write checker assessments (service role only)'
);

reset role;

select * from finish();
rollback;
