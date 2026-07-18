-- pgTAP: settlement_checks / settlement_receipts / recovery_cases (the stage-14
-- settlement (14A) and recovery-preparation (14B) tables).  Proves the
-- zero-balance consistency CHECK, the LABELLED MOCK receipt kinds + one-per-kind
-- uniqueness, the recovery status/evidence/options constraints, the single
-- allowed recovery transition (PREPARING -> STRATEGY_APPROVED) with its
-- separation-of-duty and frozen-identity guards, the append-only trails, the two
-- new gate-registry names, and RLS.  All data is synthetic and created solely for
-- demonstration; the case belongs to the invented SME "Cong ty TNHH Nong San
-- Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(28);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  1,
  'AWAITING_SETTLEMENT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- 1. A settlement check persists with a consistent zero-balance flag.
insert into public.settlement_checks (
  id, case_id, case_version, outstanding_principal, outstanding_interest,
  outstanding_fees, open_exception_count, zero_balance_confirmed, recorded_by
) values (
  '50000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  '0', '0', '0', 0, true, '00000000-0000-0000-0000-000000000001'
);

select is(
  (select zero_balance_confirmed from public.settlement_checks
   where id = '50000000-0000-0000-0000-0000000000f1'),
  true,
  'a settlement check persists with a consistent zero-balance flag'
);

-- 2. zero_balance_confirmed=true while a total is non-zero is rejected.
select throws_ok(
  $$insert into public.settlement_checks (
      case_id, case_version, outstanding_principal, outstanding_interest,
      outstanding_fees, open_exception_count, zero_balance_confirmed, recorded_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      '100', '0', '0', 0, true, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'zero_balance_confirmed=true with a non-zero total is rejected'
);

-- 3. zero_balance_confirmed=false while every total is zero is rejected.
select throws_ok(
  $$insert into public.settlement_checks (
      case_id, case_version, outstanding_principal, outstanding_interest,
      outstanding_fees, open_exception_count, zero_balance_confirmed, recorded_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      '0', '0', '0', 0, false, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'zero_balance_confirmed=false with an all-zero balance is rejected'
);

-- 4. A blank amount is rejected.
select throws_ok(
  $$insert into public.settlement_checks (
      case_id, case_version, outstanding_principal, outstanding_interest,
      outstanding_fees, open_exception_count, zero_balance_confirmed, recorded_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      '   ', '0', '0', 0, false, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a blank outstanding amount is rejected'
);

-- 5. A negative open-exception count is rejected.
select throws_ok(
  $$insert into public.settlement_checks (
      case_id, case_version, outstanding_principal, outstanding_interest,
      outstanding_fees, open_exception_count, zero_balance_confirmed, recorded_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      '0', '0', '0', -1, true, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a negative open-exception count is rejected'
);

-- 6-7. Settlement checks are append-only.
select throws_ok(
  $$update public.settlement_checks set open_exception_count = 5
      where id = '50000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'settlement checks are append-only (no update)'
);

select throws_ok(
  $$delete from public.settlement_checks
      where id = '50000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'settlement checks are append-only (no delete)'
);

-- 8-9. Both LABELLED MOCK receipt kinds coexist (one of each per check).
insert into public.settlement_receipts (settlement_check_id, kind, recorded_by)
values (
  '50000000-0000-0000-0000-0000000000f1', 'MOCK_CLOSURE',
  '00000000-0000-0000-0000-000000000001'
);
insert into public.settlement_receipts (settlement_check_id, kind, recorded_by)
values (
  '50000000-0000-0000-0000-0000000000f1', 'MOCK_RELEASE',
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.settlement_receipts
   where settlement_check_id = '50000000-0000-0000-0000-0000000000f1'),
  2::bigint,
  'both MOCK_CLOSURE and MOCK_RELEASE receipts coexist on one check'
);

-- 10. A duplicate receipt kind on the same check is rejected.
select throws_ok(
  $$insert into public.settlement_receipts (settlement_check_id, kind, recorded_by)
      values (
        '50000000-0000-0000-0000-0000000000f1', 'MOCK_CLOSURE',
        '00000000-0000-0000-0000-000000000001'
      )$$,
  '23505',
  null,
  'a duplicate receipt kind on the same check is rejected'
);

-- 11. An unknown receipt kind is rejected.
select throws_ok(
  $$insert into public.settlement_receipts (settlement_check_id, kind, recorded_by)
      values (
        '50000000-0000-0000-0000-0000000000f1', 'REAL_RELEASE',
        '00000000-0000-0000-0000-000000000001'
      )$$,
  '23514',
  null,
  'an unknown (non-MOCK) receipt kind is rejected'
);

-- 12-13. Settlement receipts are append-only.
select throws_ok(
  $$update public.settlement_receipts set kind = 'MOCK_RELEASE'
      where settlement_check_id = '50000000-0000-0000-0000-0000000000f1'
        and kind = 'MOCK_CLOSURE'$$,
  '42501',
  null,
  'settlement receipts are append-only (no update)'
);

select throws_ok(
  $$delete from public.settlement_receipts
      where settlement_check_id = '50000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'settlement receipts are append-only (no delete)'
);

-- 14. A recovery case persists (defaults to PREPARING).
insert into public.recovery_cases (
  id, case_id, case_version, trigger_summary_vi, escalated_by,
  escalation_rationale_vi, evidence_refs, options
) values (
  '60000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'Shortfall keo dai nhieu ky (mo phong).',
  '00000000-0000-0000-0000-000000000001',
  'De nghi chuan bi thu hoi (mo phong).',
  '["ref://ledger/exception-1"]'::jsonb,
  '[{"label_vi":"Co cau lai","description_vi":"De xuat","consequences_vi":"He qua"}]'::jsonb
);

select is(
  (select status from public.recovery_cases
   where id = '60000000-0000-0000-0000-0000000000f1'),
  'PREPARING',
  'a recovery case persists and defaults to PREPARING'
);

-- 15. An unknown status is rejected.
select throws_ok(
  $$insert into public.recovery_cases (
      case_id, case_version, trigger_summary_vi, escalated_by,
      escalation_rationale_vi, status, evidence_refs, options
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'trigger',
      '00000000-0000-0000-0000-000000000001', 'ly do', 'WRITTEN_OFF',
      '["ref://x"]'::jsonb, '[{"label_vi":"a","description_vi":"b","consequences_vi":"c"}]'::jsonb
    )$$,
  '23514',
  null,
  'an out-of-scope recovery status (e.g. WRITTEN_OFF) is rejected'
);

-- 16. An empty evidence pack is rejected.
select throws_ok(
  $$insert into public.recovery_cases (
      case_id, case_version, trigger_summary_vi, escalated_by,
      escalation_rationale_vi, evidence_refs, options
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'trigger',
      '00000000-0000-0000-0000-000000000001', 'ly do', '[]'::jsonb,
      '[{"label_vi":"a","description_vi":"b","consequences_vi":"c"}]'::jsonb
    )$$,
  '23514',
  null,
  'an empty evidence pack is rejected'
);

-- 17. An empty options array is rejected.
select throws_ok(
  $$insert into public.recovery_cases (
      case_id, case_version, trigger_summary_vi, escalated_by,
      escalation_rationale_vi, evidence_refs, options
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'trigger',
      '00000000-0000-0000-0000-000000000001', 'ly do', '["ref://x"]'::jsonb, '[]'::jsonb
    )$$,
  '23514',
  null,
  'an empty options array is rejected'
);

-- 18. A non-array evidence_refs value is rejected.
select throws_ok(
  $$insert into public.recovery_cases (
      case_id, case_version, trigger_summary_vi, escalated_by,
      escalation_rationale_vi, evidence_refs, options
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'trigger',
      '00000000-0000-0000-0000-000000000001', 'ly do', '{}'::jsonb,
      '[{"label_vi":"a","description_vi":"b","consequences_vi":"c"}]'::jsonb
    )$$,
  '23514',
  null,
  'a non-array evidence_refs value is rejected'
);

-- 19. The single allowed transition PREPARING -> STRATEGY_APPROVED succeeds
--     (approver differs from escalator).
update public.recovery_cases
  set status = 'STRATEGY_APPROVED',
      approved_by = '00000000-0000-0000-0000-000000000002',
      strategy_approved_at = clock_timestamp()
  where id = '60000000-0000-0000-0000-0000000000f1';

select is(
  (select status from public.recovery_cases
   where id = '60000000-0000-0000-0000-0000000000f1'),
  'STRATEGY_APPROVED',
  'the single allowed recovery transition PREPARING -> STRATEGY_APPROVED is applied'
);

-- 20. Reverting a STRATEGY_APPROVED case is a forbidden transition.
select throws_ok(
  $$update public.recovery_cases
      set status = 'PREPARING', approved_by = null, strategy_approved_at = null
      where id = '60000000-0000-0000-0000-0000000000f1'$$,
  '23514',
  null,
  'reverting an approved recovery case is a forbidden transition'
);

-- A fresh PREPARING recovery case B for the identity / separation-of-duty tests.
insert into public.recovery_cases (
  id, case_id, case_version, trigger_summary_vi, escalated_by,
  escalation_rationale_vi, evidence_refs, options
) values (
  '60000000-0000-0000-0000-0000000000f2',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'Shortfall keo dai (mo phong).',
  '00000000-0000-0000-0000-000000000001',
  'De nghi (mo phong).',
  '["ref://ledger/exception-2"]'::jsonb,
  '[{"label_vi":"a","description_vi":"b","consequences_vi":"c"}]'::jsonb
);

-- 21. Mutating a frozen identity column is rejected.
select throws_ok(
  $$update public.recovery_cases set trigger_summary_vi = 'sua noi dung'
      where id = '60000000-0000-0000-0000-0000000000f2'$$,
  '42501',
  null,
  'only status/approved_by/strategy_approved_at of a recovery case may change'
);

-- 22. The approver must differ from the escalator (separation of duty).
select throws_ok(
  $$update public.recovery_cases
      set status = 'STRATEGY_APPROVED',
          approved_by = '00000000-0000-0000-0000-000000000001',
          strategy_approved_at = clock_timestamp()
      where id = '60000000-0000-0000-0000-0000000000f2'$$,
  '23514',
  null,
  'the recovery strategy approver must differ from the escalator'
);

-- 23. Recovery cases cannot be deleted.
select throws_ok(
  $$delete from public.recovery_cases
      where id = '60000000-0000-0000-0000-0000000000f2'$$,
  '42501',
  null,
  'recovery cases cannot be deleted'
);

-- 24-25. The human_gates registry accepts the two new stage-14 gate types.
insert into public.human_gates (case_id, case_version, gate_type)
values (
  '10000000-0000-0000-0000-0000000000f1', 1, 'HG_SETTLEMENT_CONFIRMED'
);
select is(
  (select count(*) from public.human_gates where gate_type = 'HG_SETTLEMENT_CONFIRMED'),
  1::bigint,
  'HG_SETTLEMENT_CONFIRMED is accepted by the gate registry'
);

insert into public.human_gates (case_id, case_version, gate_type)
values (
  '10000000-0000-0000-0000-0000000000f1', 1, 'HG_RECOVERY_STRATEGY_APPROVED'
);
select is(
  (select count(*) from public.human_gates
   where gate_type = 'HG_RECOVERY_STRATEGY_APPROVED'),
  1::bigint,
  'HG_RECOVERY_STRATEGY_APPROVED is accepted by the gate registry'
);

-- 26. An unknown gate type is still rejected.
select throws_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
      values (
        '10000000-0000-0000-0000-0000000000f1', 1, 'HG_MADE_UP'
      )$$,
  '23514',
  null,
  'an unknown gate type is rejected by the registry'
);

-- 27-29. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.settlement_checks),
  1::bigint,
  'the assigned officer can read the settlement check'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.settlement_checks),
  0::bigint,
  'an unassigned actor cannot read any settlement check'
);

select throws_ok(
  $$insert into public.settlement_checks (
      case_id, case_version, outstanding_principal, outstanding_interest,
      outstanding_fees, open_exception_count, zero_balance_confirmed, recorded_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      '0', '0', '0', 0, true, '00000000-0000-0000-0000-000000000099'
    )$$,
  '42501',
  null,
  'authenticated users cannot write settlement checks (service role only)'
);

reset role;

select * from finish();
rollback;
