-- pgTAP: facilities / repayment_events / collection_notes (the stage-13
-- deterministic RepaymentLedger's DURABLE source-of-truth tables).  Proves the
-- append-only facility, the idempotent (facility, external_reference) repayment
-- event key, the positive-amount and reversal-reference invariants, the closed
-- kind taxonomy, the append-only event history, the free-text proposed-action
-- collection notes, and RLS.  All data below is synthetic and created solely for
-- demonstration; the case belongs to the invented SME "Cong ty TNHH Nong San
-- Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(16);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f3',
  1,
  'IN_REPAYMENT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f3',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- The source (permitting) human credit decision the facility binds to.
insert into public.human_credit_decisions (
  id, case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
)
values (
  'd0000000-0000-0000-0000-0000000000f3',
  '10000000-0000-0000-0000-0000000000f3', 1,
  'APPROVED_AS_PROPOSED',
  'Da duyet (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
);

-- 1. A facility persists.
insert into public.facilities (
  id, case_id, case_version, decision_id, principal, annual_rate_percent,
  term_months, repayment_style, first_payment_date
)
values (
  'fac00000-0000-0000-0000-0000000000f3',
  '10000000-0000-0000-0000-0000000000f3', 1,
  'd0000000-0000-0000-0000-0000000000f3',
  '120000.00', '12', 3, 'EQUAL_PRINCIPAL', '2026-08-01'
);

select is(
  (select term_months from public.facilities
   where id = 'fac00000-0000-0000-0000-0000000000f3'),
  3,
  'a facility persists'
);

-- 2. A zero / negative principal is rejected.
select throws_ok(
  $$insert into public.facilities (
      case_id, case_version, decision_id, principal, annual_rate_percent,
      term_months, repayment_style, first_payment_date
    ) values (
      '10000000-0000-0000-0000-0000000000f3', 1,
      'd0000000-0000-0000-0000-0000000000f3',
      '0', '12', 3, 'EQUAL_PRINCIPAL', '2026-08-01'
    )$$,
  '23514',
  null,
  'a non-positive principal is rejected'
);

-- 3. The composite FK binds the exact (decision, case, version) triple.
select throws_ok(
  $$insert into public.facilities (
      case_id, case_version, decision_id, principal, annual_rate_percent,
      term_months, repayment_style, first_payment_date
    ) values (
      '10000000-0000-0000-0000-0000000000f3', 2,
      'd0000000-0000-0000-0000-0000000000f3',
      '120000.00', '12', 3, 'EQUAL_PRINCIPAL', '2026-08-01'
    )$$,
  '23503',
  null,
  'a facility cannot bind a different case version than its source decision'
);

-- 4. Facilities are append-only (no update).
select throws_ok(
  $$update public.facilities set principal = '999'
      where id = 'fac00000-0000-0000-0000-0000000000f3'$$,
  '42501',
  null,
  'facilities are append-only (no update)'
);

-- 5. A repayment PAYMENT event persists.
insert into public.repayment_events (
  id, facility_id, kind, amount, external_reference, effective_date
)
values (
  'e0000000-0000-0000-0000-0000000000f1',
  'fac00000-0000-0000-0000-0000000000f3',
  'PAYMENT', '41200.00', 'BANKREF-0001', '2026-08-01'
);

select is(
  (select kind from public.repayment_events
   where id = 'e0000000-0000-0000-0000-0000000000f1'),
  'PAYMENT',
  'a repayment payment event persists'
);

-- 6. A duplicate (facility, external_reference) is a unique violation.
select throws_ok(
  $$insert into public.repayment_events (
      facility_id, kind, amount, external_reference, effective_date
    ) values (
      'fac00000-0000-0000-0000-0000000000f3',
      'PAYMENT', '41200.00', 'BANKREF-0001', '2026-08-02'
    )$$,
  '23505',
  null,
  'a duplicate external reference for a facility is rejected (idempotency key)'
);

-- 7. An unknown kind is rejected by the closed taxonomy.
select throws_ok(
  $$insert into public.repayment_events (
      facility_id, kind, amount, external_reference, effective_date
    ) values (
      'fac00000-0000-0000-0000-0000000000f3',
      'WRITE_OFF', '10.00', 'BANKREF-BADKIND', '2026-08-02'
    )$$,
  '23514',
  null,
  'an unknown repayment event kind is rejected'
);

-- 8. A non-positive amount is rejected (the sign lives in `kind`, never here).
select throws_ok(
  $$insert into public.repayment_events (
      facility_id, kind, amount, external_reference, effective_date
    ) values (
      'fac00000-0000-0000-0000-0000000000f3',
      'PAYMENT', '-5.00', 'BANKREF-NEG', '2026-08-02'
    )$$,
  '23514',
  null,
  'a non-positive amount is rejected'
);

-- 9. A PAYMENT may not reference a reversed event.
select throws_ok(
  $$insert into public.repayment_events (
      facility_id, kind, amount, external_reference, effective_date, reversed_event_id
    ) values (
      'fac00000-0000-0000-0000-0000000000f3',
      'PAYMENT', '5.00', 'BANKREF-BADREF', '2026-08-02',
      'e0000000-0000-0000-0000-0000000000f1'
    )$$,
  '23514',
  null,
  'a PAYMENT cannot reference a reversed event'
);

-- 10. A REVERSAL must reference the original event it undoes.
select throws_ok(
  $$insert into public.repayment_events (
      facility_id, kind, amount, external_reference, effective_date
    ) values (
      'fac00000-0000-0000-0000-0000000000f3',
      'REVERSAL', '41200.00', 'BANKREF-REV-ORPHAN', '2026-08-03'
    )$$,
  '23514',
  null,
  'a REVERSAL without a referenced original event is rejected'
);

-- 11. A well-formed REVERSAL referencing the original persists.
insert into public.repayment_events (
  facility_id, kind, amount, external_reference, effective_date, reversed_event_id
)
values (
  'fac00000-0000-0000-0000-0000000000f3',
  'REVERSAL', '41200.00', 'BANKREF-REV-0001', '2026-08-03',
  'e0000000-0000-0000-0000-0000000000f1'
);

select is(
  (select count(*) from public.repayment_events
   where facility_id = 'fac00000-0000-0000-0000-0000000000f3'
     and kind = 'REVERSAL'),
  1::bigint,
  'a well-formed reversal referencing the original persists'
);

-- 12-13. Repayment events are append-only.
select throws_ok(
  $$update public.repayment_events set amount = '1.00'
      where id = 'e0000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'repayment events are append-only (no update)'
);

select throws_ok(
  $$delete from public.repayment_events
      where id = 'e0000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'repayment events are append-only (no delete)'
);

-- 14. A PROPOSED_ACTION collection note must name its proposed action.
select throws_ok(
  $$insert into public.collection_notes (
      facility_id, case_id, case_version, note_kind, note_text_vi, author_id, author_role
    ) values (
      'fac00000-0000-0000-0000-0000000000f3',
      '10000000-0000-0000-0000-0000000000f3', 1,
      'PROPOSED_ACTION', 'De xuat siet dong tien.',
      '00000000-0000-0000-0000-000000000001', 'OPS_OFFICER'
    )$$,
  '23514',
  null,
  'a proposed-action note must name its proposed action'
);

-- 15. A free-text proposed action persists (a PROPOSAL only; nothing executed).
insert into public.collection_notes (
  facility_id, case_id, case_version, note_kind, note_text_vi,
  proposed_action_vi, author_id, author_role
)
values (
  'fac00000-0000-0000-0000-0000000000f3',
  '10000000-0000-0000-0000-0000000000f3', 1,
  'PROPOSED_ACTION', 'Khach hang tre han ky 1; de xuat lien he va siet dong tien.',
  'TIGHTEN_CASHFLOW_CONTROL',
  '00000000-0000-0000-0000-000000000001', 'OPS_OFFICER'
);

select is(
  (select proposed_action_vi from public.collection_notes
   where facility_id = 'fac00000-0000-0000-0000-0000000000f3'),
  'TIGHTEN_CASHFLOW_CONTROL',
  'a free-text proposed collection action persists'
);

-- 16. RLS: an unassigned actor reads no facility.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.facilities),
  0::bigint,
  'an unassigned actor cannot read any facility'
);

reset role;

select * from finish();
rollback;
