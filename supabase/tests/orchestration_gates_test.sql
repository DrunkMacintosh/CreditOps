begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(16);

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

-- Agent tasks are case-scoped (no document version) and typed from the
-- closed registry.
insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type, status,
  max_attempts, input_schema_version, input_payload, idempotency_key
)
values
  ('30000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001', 1, null, 'ORCHESTRATOR_PLAN',
   'PENDING', 3, '1', '{}'::jsonb, 'ORCH-PLAN:case-1:1'),
  ('30000000-0000-0000-0000-000000000002',
   '10000000-0000-0000-0000-000000000001', 1, null, 'CREDIT_UNDERWRITING',
   'PENDING', 3, '1', '{}'::jsonb, 'ORCH:case-1:1:CREDIT_UNDERWRITING');

select throws_ok(
  $$insert into public.processing_tasks (
      case_id, case_version, document_version_id, task_type, status,
      max_attempts, input_schema_version, input_payload, idempotency_key
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, null, 'UNKNOWN_AGENT',
      'PENDING', 3, '1', '{}'::jsonb, 'ORCH:unknown'
    )$$,
  '23514',
  null,
  'an unknown task type violates the closed task-type registry'
);

select throws_ok(
  $$insert into public.processing_tasks (
      case_id, case_version, document_version_id, task_type, status,
      max_attempts, input_schema_version, input_payload, idempotency_key
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, null, 'DOCUMENT_INGESTION',
      'PENDING', 3, '1', '{}'::jsonb, 'ORCH:docless'
    )$$,
  '23514',
  null,
  'document ingestion requires a document version'
);

insert into public.task_dependencies (case_id, case_version, task_id, depends_on_task_id)
values (
  '10000000-0000-0000-0000-000000000001', 1,
  '30000000-0000-0000-0000-000000000002',
  '30000000-0000-0000-0000-000000000001'
);

select throws_ok(
  $$insert into public.task_dependencies (
      case_id, case_version, task_id, depends_on_task_id
    ) values (
      '10000000-0000-0000-0000-000000000001', 1,
      '30000000-0000-0000-0000-000000000001',
      '30000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a task cannot depend on itself'
);

select throws_ok(
  $$delete from public.task_dependencies$$,
  '42501',
  null,
  'task dependencies are append-only'
);

insert into public.human_gates (case_id, case_version, gate_type)
values ('10000000-0000-0000-0000-000000000001', 1, 'G1_INTAKE_COMPLETE');

select throws_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values ('10000000-0000-0000-0000-000000000001', 1, 'G1_INTAKE_COMPLETE')$$,
  '23505',
  null,
  'one gate row exists per case version and gate type'
);

select throws_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values ('10000000-0000-0000-0000-000000000001', 1, 'G9_UNKNOWN')$$,
  '23514',
  null,
  'gate types are limited to the synthetic G1-G4 set'
);

select throws_ok(
  $$update public.human_gates set disposition_ref = 'sneaky'
    where gate_type = 'G1_INTAKE_COMPLETE'$$,
  '42501',
  null,
  'an open gate cannot be mutated except by satisfaction'
);

update public.human_gates
set status = 'SATISFIED',
    satisfied_by_actor_id = '00000000-0000-0000-0000-000000000001',
    disposition_ref = 'intake-handoff',
    satisfied_at = clock_timestamp()
where case_id = '10000000-0000-0000-0000-000000000001'
  and gate_type = 'G1_INTAKE_COMPLETE';

select is(
  (
    select status from public.human_gates
    where gate_type = 'G1_INTAKE_COMPLETE'
  ),
  'SATISFIED',
  'an open gate transitions to SATISFIED with a disposition reference'
);

select throws_ok(
  $$update public.human_gates set status = 'OPEN'
    where gate_type = 'G1_INTAKE_COMPLETE'$$,
  '42501',
  null,
  'a satisfied gate is immutable and cannot be reopened'
);

select throws_ok(
  $$delete from public.human_gates$$,
  '42501',
  null,
  'human gates cannot be deleted'
);

insert into public.planner_proposals (
  case_id, case_version, execution_id, proposal, status,
  validation_errors, prompt_version, schema_version
)
values (
  '10000000-0000-0000-0000-000000000001', 1,
  '40000000-0000-0000-0000-000000000001',
  '{"steps":[]}'::jsonb, 'REJECTED',
  '["unknown task type proposed"]'::jsonb,
  'orchestrator-prompt-v1', 'orchestrator-proposal-v1'
);

select throws_ok(
  $$update public.planner_proposals set status = 'ACCEPTED'$$,
  '42501',
  null,
  'planner proposals are append-only advisory history'
);

select throws_ok(
  $$delete from public.planner_proposals$$,
  '42501',
  null,
  'planner proposals cannot be deleted'
);

select is(
  (select count(*) from pgmq.meta where queue_name = 'creditops_agent_tasks'),
  1::bigint,
  'the separate agent-task queue exists exactly once'
);

set local role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select is(
  (select count(*) from public.human_gates),
  1::bigint,
  'the assigned officer can read case human gates'
);

select is(
  (select count(*) from public.planner_proposals),
  1::bigint,
  'the assigned officer can read planner proposal history'
);

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000099',
  true
);

select is(
  (select count(*) from public.human_gates),
  0::bigint,
  'an unassigned actor cannot read another case''s gates'
);

select * from finish();
rollback;
