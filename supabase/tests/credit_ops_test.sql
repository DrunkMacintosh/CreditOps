-- pgTAP: credit_ops_packages / ops_action_authorizations /
-- document_request_approvals append-only stores + additive handoff state.
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(26);

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
  '10000000-0000-0000-0000-0000000000c1', 1, null, 'CREDIT_OPERATIONS',
  'RUNNING', 3, '1', '{}'::jsonb, 'ORCH:case-c1:1:CREDIT_OPERATIONS'
);

-- 1. A credit-ops package persists with full provenance columns.
insert into public.credit_ops_packages (
  id, case_id, case_version, task_id, execution_id,
  prompt_version, model_id, endpoint_id, package, evidence_view_built_at
)
values (
  '50000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1,
  '30000000-0000-0000-0000-0000000000c1',
  '40000000-0000-0000-0000-0000000000c1',
  'credit-ops-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
  '{"proposed_actions":[{"id":"70000000-0000-0000-0000-0000000000c1","execution_status":"DRAFT"}],"document_requests":[{"id":"71000000-0000-0000-0000-0000000000c1","approval_status":"PENDING_APPROVAL"}]}'::jsonb,
  clock_timestamp()
);

select is(
  (select count(*) from public.credit_ops_packages),
  1::bigint,
  'a credit-ops package row persists'
);

-- 2. Append-only: no update, no delete (e: the package row is immutable;
-- approving a request can never mutate it).
select throws_ok(
  $$update public.credit_ops_packages
    set package = '{"proposed_actions":[{"execution_status":"EXECUTED"}]}'::jsonb$$,
  '42501',
  null,
  'credit-ops packages are append-only (no update -- nothing can ever be relabelled EXECUTED)'
);

select throws_ok(
  $$delete from public.credit_ops_packages$$,
  '42501',
  null,
  'credit-ops packages are append-only (no delete)'
);

-- 3. One package per (case, version, task): duplicate delivery resolves to
-- the existing row instead of a second package.
select throws_ok(
  $$insert into public.credit_ops_packages (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, package, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      '40000000-0000-0000-0000-0000000000c2',
      'credit-ops-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23505',
  null,
  'duplicate delivery cannot create a second package for the same task'
);

-- 4. The role column is pinned to the credit-ops role.
select throws_ok(
  $$insert into public.credit_ops_packages (
      case_id, case_version, task_id, execution_id, agent_role,
      prompt_version, model_id, endpoint_id, package, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      '40000000-0000-0000-0000-0000000000c3',
      'INDEPENDENT_RISK_REVIEW',
      'credit-ops-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '23514',
  null,
  'the package role is pinned to CREDIT_OPERATIONS'
);

-- 5. A human action authorization persists, append-only, and never touches
-- the package row (it only RECORDS authority; nothing executes).
insert into public.ops_action_authorizations (
  id, package_id, case_id, case_version, action_id,
  actor_id, actor_role, rationale_vi
)
values (
  '80000000-0000-0000-0000-0000000000c1',
  '50000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1,
  '70000000-0000-0000-0000-0000000000c1',
  '00000000-0000-0000-0000-000000000001', 'OPS_OFFICER',
  'Uy quyen hanh dong de xuat (du lieu mo phong).'
);

select is(
  (select count(*) from public.ops_action_authorizations),
  1::bigint,
  'a human action authorization persists'
);

select is(
  (
    select package -> 'proposed_actions' -> 0 ->> 'execution_status'
    from public.credit_ops_packages
    where id = '50000000-0000-0000-0000-0000000000c1'
  ),
  'DRAFT',
  'the authorized action row itself stays DRAFT -- authorization records authority, never execution'
);

select throws_ok(
  $$update public.ops_action_authorizations set rationale_vi = 'sua doi'$$,
  '42501',
  null,
  'action authorizations are append-only (no update)'
);

select throws_ok(
  $$delete from public.ops_action_authorizations$$,
  '42501',
  null,
  'action authorizations are append-only (no delete)'
);

-- 6. At most one authorization per (package, action).
select throws_ok(
  $$insert into public.ops_action_authorizations (
      package_id, case_id, case_version, action_id,
      actor_id, actor_role, rationale_vi
    ) values (
      '50000000-0000-0000-0000-0000000000c1',
      '10000000-0000-0000-0000-0000000000c1', 1,
      '70000000-0000-0000-0000-0000000000c1',
      '00000000-0000-0000-0000-000000000002', 'OPS_OFFICER', 'lap lai'
    )$$,
  '23505',
  null,
  'a second authorization for the same action is rejected'
);

-- 7. An authorization cannot bind to a package/case pair that does not match.
select throws_ok(
  $$insert into public.ops_action_authorizations (
      package_id, case_id, case_version, action_id,
      actor_id, actor_role, rationale_vi
    ) values (
      '50000000-0000-0000-0000-0000000000c1',
      '10000000-0000-0000-0000-0000000000c1', 2,
      '70000000-0000-0000-0000-0000000000c2',
      '00000000-0000-0000-0000-000000000001', 'OPS_OFFICER', 'sai phien ban'
    )$$,
  '23503',
  null,
  'an authorization must bind to a real (package, case, version) triple'
);

-- 8. A human document-request approval persists, append-only, and never
-- touches the package row (the stored request stays PENDING_APPROVAL; the
-- APPROVED status is a derived read-time view).
insert into public.document_request_approvals (
  id, package_id, case_id, case_version, request_id,
  actor_id, actor_role, rationale_vi
)
values (
  '81000000-0000-0000-0000-0000000000c1',
  '50000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1,
  '71000000-0000-0000-0000-0000000000c1',
  '00000000-0000-0000-0000-000000000001', 'OPS_OFFICER',
  'Phe duyet yeu cau bo sung tai lieu (du lieu mo phong).'
);

select is(
  (select count(*) from public.document_request_approvals),
  1::bigint,
  'a human document-request approval persists'
);

select is(
  (
    select package -> 'document_requests' -> 0 ->> 'approval_status'
    from public.credit_ops_packages
    where id = '50000000-0000-0000-0000-0000000000c1'
  ),
  'PENDING_APPROVAL',
  'the stored request row stays PENDING_APPROVAL -- approval is a derived view, never a mutation'
);

select throws_ok(
  $$update public.document_request_approvals set rationale_vi = 'sua doi'$$,
  '42501',
  null,
  'document-request approvals are append-only (no update)'
);

select throws_ok(
  $$delete from public.document_request_approvals$$,
  '42501',
  null,
  'document-request approvals are append-only (no delete)'
);

select throws_ok(
  $$insert into public.document_request_approvals (
      package_id, case_id, case_version, request_id,
      actor_id, actor_role, rationale_vi
    ) values (
      '50000000-0000-0000-0000-0000000000c1',
      '10000000-0000-0000-0000-0000000000c1', 1,
      '71000000-0000-0000-0000-0000000000c1',
      '00000000-0000-0000-0000-000000000002', 'OPS_OFFICER', 'lap lai'
    )$$,
  '23505',
  null,
  'a second approval for the same request is rejected'
);

-- 9. Additive handoff state: the operations->human-decision state is
-- accepted, prior states remain valid, and invented states are rejected.
insert into public.handoffs (
  id, case_id, case_version, source_task_id, state,
  handoff_data, created_by_type
)
values (
  '82000000-0000-0000-0000-0000000000c1',
  '10000000-0000-0000-0000-0000000000c1', 1,
  '30000000-0000-0000-0000-0000000000c1',
  'READY_FOR_HUMAN_DECISION',
  '{"packageId":"50000000-0000-0000-0000-0000000000c1"}'::jsonb,
  'AGENT:CREDIT_OPERATIONS'
);

select is(
  (select count(*) from public.handoffs where state = 'READY_FOR_HUMAN_DECISION'),
  1::bigint,
  'an operations handoff persists in state READY_FOR_HUMAN_DECISION'
);

insert into public.handoffs (
  case_id, case_version, source_task_id, state, handoff_data, created_by_type
)
values (
  '10000000-0000-0000-0000-0000000000c1', 1,
  '30000000-0000-0000-0000-0000000000c1',
  'READY_FOR_OPERATIONS', '{"assessmentId":"x"}'::jsonb,
  'AGENT:INDEPENDENT_RISK_REVIEW'
);

select is(
  (select count(*) from public.handoffs where state = 'READY_FOR_OPERATIONS'),
  1::bigint,
  'the checker->operations handoff state is still valid after the additive extension'
);

select throws_ok(
  $$insert into public.handoffs (
      case_id, case_version, source_task_id, state, handoff_data, created_by_type
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      'READY_FOR_EXECUTION', '{}'::jsonb, 'AGENT:CREDIT_OPERATIONS'
    )$$,
  '23514',
  null,
  'handoff states outside the closed set are rejected (no execution state exists)'
);

-- 10. This migration grants NO write access on any upstream agent table.
select is(
  (
    select count(*) from information_schema.role_table_grants
    where table_schema = 'public'
      and table_name in (
        'underwriting_assessments', 'legal_compliance_assessments',
        'risk_review_assessments'
      )
      and grantee in ('authenticated', 'anon', 'creditops_api')
      and privilege_type in ('INSERT', 'UPDATE', 'DELETE')
  ),
  0::bigint,
  'no write grant exists on any upstream agent output table'
);

-- 11. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.credit_ops_packages),
  1::bigint,
  'the assigned officer can read the credit-ops package'
);

select is(
  (select count(*) from public.ops_action_authorizations),
  1::bigint,
  'the assigned officer can read action authorizations'
);

select is(
  (select count(*) from public.document_request_approvals),
  1::bigint,
  'the assigned officer can read document-request approvals'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.credit_ops_packages),
  0::bigint,
  'an unassigned actor cannot read any credit-ops package'
);

select throws_ok(
  $$insert into public.ops_action_authorizations (
      package_id, case_id, case_version, action_id,
      actor_id, actor_role, rationale_vi
    ) values (
      '50000000-0000-0000-0000-0000000000c1',
      '10000000-0000-0000-0000-0000000000c1', 1,
      '70000000-0000-0000-0000-0000000000c9',
      '00000000-0000-0000-0000-000000000099', 'OPS_OFFICER', 'khong duoc phep'
    )$$,
  '42501',
  null,
  'authenticated users cannot write authorizations directly (service role only)'
);

select throws_ok(
  $$insert into public.credit_ops_packages (
      case_id, case_version, task_id, execution_id,
      prompt_version, model_id, endpoint_id, package, evidence_view_built_at
    ) values (
      '10000000-0000-0000-0000-0000000000c1', 1,
      '30000000-0000-0000-0000-0000000000c1',
      '40000000-0000-0000-0000-0000000000c9',
      'credit-ops-prompt-v1', 'synthetic-model', 'synthetic-endpoint',
      '{}'::jsonb, clock_timestamp()
    )$$,
  '42501',
  null,
  'authenticated users cannot write credit-ops packages (service role only)'
);

reset role;

select * from finish();
rollback;
