-- pgTAP: proposed_disbursement_actions / disbursement_execution_receipts (the
-- stage-11 proposed disbursement).  Proves the CLOSED execution-status set, the
-- exact-decimal amount + labelled-mock constraints, the deterministic transition
-- trigger (allowed edge succeeds; forbidden edge and identity mutation rejected;
-- delete rejected), the UNIQUE idempotency key, the receipt_ref/result coupling,
-- the append-only receipts trail, and RLS.  All data below is synthetic and
-- created solely for demonstration; the case belongs to the invented SME
-- "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(18);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  1,
  'AWAITING_DISBURSEMENT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- The source (permitting) human credit decision the action binds to.
insert into public.human_credit_decisions (
  id, case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
)
values (
  'd0000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'APPROVED_AS_PROPOSED',
  'Da duyet (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
);

-- 1. A proposed disbursement action persists (defaults to PROPOSED).
insert into public.proposed_disbursement_actions (
  id, case_id, case_version, decision_id, amount_text, currency,
  beneficiary_ref_vi, account_ref_vi, created_by
)
values (
  'a0000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'd0000000-0000-0000-0000-0000000000f1',
  '5000000000', 'VND', 'Nha cung cap (mo phong)', 'TK-BENEFICIARY-DEMO',
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select status from public.proposed_disbursement_actions
   where id = 'a0000000-0000-0000-0000-0000000000f1'),
  'PROPOSED',
  'a proposed disbursement action persists and defaults to PROPOSED'
);

-- 2. Unknown status is rejected by the closed synthetic taxonomy.
select throws_ok(
  $$insert into public.proposed_disbursement_actions (
      case_id, case_version, decision_id, amount_text, currency,
      beneficiary_ref_vi, account_ref_vi, status, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', '1', 'VND', 'b', 'a',
      'EXECUTED_BY_AGENT', '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'an unknown execution status violates the closed synthetic taxonomy'
);

-- 3. A malformed / non-positive amount is rejected (exact decimal, no float).
select throws_ok(
  $$insert into public.proposed_disbursement_actions (
      case_id, case_version, decision_id, amount_text, currency,
      beneficiary_ref_vi, account_ref_vi, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', 'abc', 'VND', 'b', 'a',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a malformed amount is rejected (must be a valid positive decimal)'
);

-- 4. Currency must be non-empty.
select throws_ok(
  $$insert into public.proposed_disbursement_actions (
      case_id, case_version, decision_id, amount_text, currency,
      beneficiary_ref_vi, account_ref_vi, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', '1', '   ', 'b', 'a',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a blank currency is rejected'
);

-- 5. The composite FK binds the exact (decision, case, version) triple: a
--    mismatched case version has no parent decision.
select throws_ok(
  $$insert into public.proposed_disbursement_actions (
      case_id, case_version, decision_id, amount_text, currency,
      beneficiary_ref_vi, account_ref_vi, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 2,
      'd0000000-0000-0000-0000-0000000000f1', '1', 'VND', 'b', 'a',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23503',
  null,
  'an action cannot bind a different case version than its source decision'
);

-- 6. An ALLOWED transition (PROPOSED -> EXECUTION_REQUESTED) succeeds.
update public.proposed_disbursement_actions
  set status = 'EXECUTION_REQUESTED'
  where id = 'a0000000-0000-0000-0000-0000000000f1';

select is(
  (select status from public.proposed_disbursement_actions
   where id = 'a0000000-0000-0000-0000-0000000000f1'),
  'EXECUTION_REQUESTED',
  'an allowed execution transition is applied'
);

-- 7. A FORBIDDEN transition (EXECUTION_REQUESTED -> PROPOSED) is rejected.
select throws_ok(
  $$update public.proposed_disbursement_actions
      set status = 'PROPOSED'
      where id = 'a0000000-0000-0000-0000-0000000000f1'$$,
  '23514',
  null,
  'a forbidden execution transition is rejected by the trigger'
);

-- 8. Only status may change; mutating identity / money is rejected.
select throws_ok(
  $$update public.proposed_disbursement_actions
      set amount_text = '9999'
      where id = 'a0000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'only the status of a proposed disbursement action may change'
);

-- 9. Actions cannot be deleted.
select throws_ok(
  $$delete from public.proposed_disbursement_actions
      where id = 'a0000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'proposed disbursement actions cannot be deleted'
);

-- 10. A labelled-mock execution receipt persists (CONFIRMED_EXECUTED + ref).
insert into public.disbursement_execution_receipts (
  id, action_id, idempotency_key, adapter_label, result_status, receipt_ref,
  recorded_by
)
values (
  'e0000000-0000-0000-0000-0000000000f1',
  'a0000000-0000-0000-0000-0000000000f1',
  'idem-1', 'MOCK_DISBURSEMENT_EXECUTION_ADAPTER', 'CONFIRMED_EXECUTED',
  'receipt-ref-1', '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.disbursement_execution_receipts),
  1::bigint,
  'a labelled-mock execution receipt persists'
);

-- 11. A duplicate idempotency key is rejected (a duplicate delivery can never
--     move money twice).
select throws_ok(
  $$insert into public.disbursement_execution_receipts (
      action_id, idempotency_key, adapter_label, result_status, receipt_ref,
      recorded_by
    ) values (
      'a0000000-0000-0000-0000-0000000000f1', 'idem-1',
      'MOCK_DISBURSEMENT_EXECUTION_ADAPTER', 'CONFIRMED_EXECUTED', 'other-ref',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23505',
  null,
  'a duplicate idempotency key is rejected'
);

-- 12. receipt_ref must be null for an EXECUTION_UNKNOWN result.
select throws_ok(
  $$insert into public.disbursement_execution_receipts (
      action_id, idempotency_key, adapter_label, result_status, receipt_ref,
      recorded_by
    ) values (
      'a0000000-0000-0000-0000-0000000000f1', 'idem-2',
      'MOCK_DISBURSEMENT_EXECUTION_ADAPTER', 'EXECUTION_UNKNOWN', 'should-not-exist',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'an EXECUTION_UNKNOWN receipt must not carry a receipt_ref'
);

-- 13. The adapter label must be the single labelled mock.
select throws_ok(
  $$insert into public.disbursement_execution_receipts (
      action_id, idempotency_key, adapter_label, result_status, receipt_ref,
      recorded_by
    ) values (
      'a0000000-0000-0000-0000-0000000000f1', 'idem-3',
      'REAL_CORE_BANKING', 'CONFIRMED_EXECUTED', 'r',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a receipt must be stamped with the labelled mock adapter'
);

-- 14-15. Execution receipts are append-only.
select throws_ok(
  $$update public.disbursement_execution_receipts set receipt_ref = 'tampered'$$,
  '42501',
  null,
  'disbursement execution receipts are append-only (no update)'
);

select throws_ok(
  $$delete from public.disbursement_execution_receipts$$,
  '42501',
  null,
  'disbursement execution receipts are append-only (no delete)'
);

-- 16-18. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.proposed_disbursement_actions),
  1::bigint,
  'the assigned officer can read the proposed disbursement action'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.proposed_disbursement_actions),
  0::bigint,
  'an unassigned actor cannot read any proposed disbursement action'
);

select throws_ok(
  $$insert into public.proposed_disbursement_actions (
      case_id, case_version, decision_id, amount_text, currency,
      beneficiary_ref_vi, account_ref_vi, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', '1', 'VND', 'b', 'a',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '42501',
  null,
  'authenticated users cannot write proposed disbursement actions (service role only)'
);

reset role;

select * from finish();
rollback;
