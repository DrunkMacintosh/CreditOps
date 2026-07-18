begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(8);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

-- A goal contract naming a non-empty prohibition set (the universal human-only
-- bans) inserts cleanly.
insert into public.goal_contracts (
  id, contract_key, version, objective_vi, prohibited_actions,
  output_schema_ref, output_schema_version,
  max_input_tokens, max_output_tokens, max_tool_calls
) values (
  '20000000-0000-0000-0000-000000000001',
  'underwriting-assessment', 1, 'Đánh giá thẩm định tín dụng',
  '["APPROVE_CREDIT","REJECT_CREDIT","WAIVE_POLICY","SIGN_DOCUMENT",'
  || '"EXECUTE_DISBURSEMENT","SEND_CUSTOMER_COMMUNICATION","CONFIRM_CANDIDATE_FACT",'
  || '"CLOSE_GAP_OR_CONFLICT","EXPAND_OWN_PERMISSIONS"]'::jsonb,
  'underwriting-assessment-output', '1', 100000, 8000, 12
);

select is(
  (select count(*)::int from public.goal_contracts),
  1,
  'a well-formed goal contract inserts'
);

select throws_ok(
  $$insert into public.goal_contracts (
      contract_key, version, objective_vi, prohibited_actions,
      output_schema_ref, output_schema_version,
      max_input_tokens, max_output_tokens, max_tool_calls
    ) values (
      'underwriting-assessment', 1, 'Bản trùng', '["APPROVE_CREDIT"]'::jsonb,
      'underwriting-assessment-output', '1', 100000, 8000, 12
    )$$,
  '23505',
  null,
  'a goal contract is unique on (contract_key, version)'
);

select throws_ok(
  $$update public.goal_contracts set objective_vi = 'Sửa đổi'$$,
  '42501',
  null,
  'goal contracts are append-only and cannot be updated'
);

select throws_ok(
  $$delete from public.goal_contracts$$,
  '42501',
  null,
  'goal contracts cannot be deleted'
);

-- A context manifest with an identifier-only object payload inserts cleanly
-- (task_id left null: an unbound manifest skips the composite task FK).
insert into public.agent_context_manifests (
  id, case_id, case_version, goal_contract_id, goal_contract_version,
  agent_role, profile_version, prompt_version, schema_version,
  context_hash, manifest
) values (
  '30000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001', 1,
  '20000000-0000-0000-0000-000000000001', 1,
  'CREDIT_UNDERWRITING', 'underwriting-profile-v1', 'underwriting-prompt-v1', '1',
  'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
  '{"authoritative_fact_refs": []}'::jsonb
);

select is(
  (select count(*)::int from public.agent_context_manifests),
  1,
  'a well-formed context manifest inserts'
);

select throws_ok(
  $$insert into public.agent_context_manifests (
      case_id, case_version, goal_contract_id, goal_contract_version,
      agent_role, profile_version, prompt_version, schema_version,
      context_hash, manifest
    ) values (
      '10000000-0000-0000-0000-000000000001', 1,
      '20000000-0000-0000-0000-000000000001', 1,
      'CREDIT_UNDERWRITING', 'underwriting-profile-v1', 'underwriting-prompt-v1', '1',
      'deadbeef', '[]'::jsonb
    )$$,
  '23514',
  null,
  'a non-object manifest payload is rejected'
);

select throws_ok(
  $$update public.agent_context_manifests set context_hash = 'tampered'$$,
  '42501',
  null,
  'context manifests are append-only and cannot be updated'
);

select throws_ok(
  $$delete from public.agent_context_manifests$$,
  '42501',
  null,
  'context manifests cannot be deleted'
);

select * from finish();
rollback;
