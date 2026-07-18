-- pgTAP: human_credit_decisions / approved_term_snapshots append-only stores
-- (the stage-6 human credit decision + frozen approved terms).
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(19);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000e1',
  1,
  'AWAITING_CREDIT_DECISION',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000e1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- 1. A human credit decision persists (null artifact bindings, no conditions).
insert into public.human_credit_decisions (
  id, case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
)
values (
  'd0000000-0000-0000-0000-0000000000e1',
  '10000000-0000-0000-0000-0000000000e1', 1,
  'APPROVED_AS_PROPOSED',
  'Da ra soat ho so (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
);

select is(
  (select count(*) from public.human_credit_decisions),
  1::bigint,
  'a human credit decision row persists'
);

-- 2. Unknown decision value is rejected by the closed synthetic taxonomy.
select throws_ok(
  $$insert into public.human_credit_decisions (
      case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
    ) values (
      '10000000-0000-0000-0000-0000000000e1', 2,
      'APPROVED_BY_AGENT', 'khong hop le',
      '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
    )$$,
  '23514',
  null,
  'an unknown decision value violates the closed synthetic taxonomy'
);

-- 3. One decision per case version: a second decision at the same version fails.
select throws_ok(
  $$insert into public.human_credit_decisions (
      case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
    ) values (
      '10000000-0000-0000-0000-0000000000e1', 1,
      'DECLINED_BY_HUMAN', 'quyet dinh thu hai cung phien ban',
      '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
    )$$,
  '23505',
  null,
  'a second decision for the same case version is rejected (unique)'
);

-- 4-5. Decisions are append-only.
select throws_ok(
  $$update public.human_credit_decisions set decision = 'DECLINED_BY_HUMAN'$$,
  '42501',
  null,
  'human credit decisions are append-only (no update)'
);

select throws_ok(
  $$delete from public.human_credit_decisions$$,
  '42501',
  null,
  'human credit decisions are append-only (no delete)'
);

-- 6. conditions must be a JSON array.
select throws_ok(
  $$insert into public.human_credit_decisions (
      case_id, case_version, decision, rationale_vi, decided_by, decided_by_role,
      conditions
    ) values (
      '10000000-0000-0000-0000-0000000000e1', 3,
      'APPROVED_WITH_CONDITIONS', 'dieu kien sai kieu',
      '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER',
      '{}'::jsonb
    )$$,
  '23514',
  null,
  'conditions must be a JSON array'
);

-- 7. rationale must be non-empty.
select throws_ok(
  $$insert into public.human_credit_decisions (
      case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
    ) values (
      '10000000-0000-0000-0000-0000000000e1', 4,
      'APPROVED_AS_PROPOSED', '   ',
      '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
    )$$,
  '23514',
  null,
  'a blank rationale is rejected'
);

-- 8. A snapshot's composite FK binds the exact (decision, case, version) triple:
--    a mismatched case version has no parent decision.
select throws_ok(
  $$insert into public.approved_term_snapshots (
      decision_id, case_id, case_version, terms, snapshot_hash
    ) values (
      'd0000000-0000-0000-0000-0000000000e1',
      '10000000-0000-0000-0000-0000000000e1', 2,
      '{}'::jsonb, repeat('a', 64)
    )$$,
  '23503',
  null,
  'a snapshot cannot bind a different case version than its decision'
);

-- 9. Snapshot terms must be a JSON object.
select throws_ok(
  $$insert into public.approved_term_snapshots (
      decision_id, case_id, case_version, terms, snapshot_hash
    ) values (
      'd0000000-0000-0000-0000-0000000000e1',
      '10000000-0000-0000-0000-0000000000e1', 1,
      '[]'::jsonb, repeat('a', 64)
    )$$,
  '23514',
  null,
  'snapshot terms must be a JSON object'
);

-- 10. Snapshot hash must be 64 lowercase hex chars.
select throws_ok(
  $$insert into public.approved_term_snapshots (
      decision_id, case_id, case_version, terms, snapshot_hash
    ) values (
      'd0000000-0000-0000-0000-0000000000e1',
      '10000000-0000-0000-0000-0000000000e1', 1,
      '{}'::jsonb, repeat('z', 64)
    )$$,
  '23514',
  null,
  'the snapshot hash must be 64 lowercase hex chars'
);

-- 11. A valid approved-term snapshot persists.
insert into public.approved_term_snapshots (
  id, decision_id, case_id, case_version, terms, snapshot_hash
)
values (
  'a0000000-0000-0000-0000-0000000000e1',
  'd0000000-0000-0000-0000-0000000000e1',
  '10000000-0000-0000-0000-0000000000e1', 1,
  '{"amount": "5000000000", "currency": "VND", "term": null, "rate": null}'::jsonb,
  repeat('a', 64)
);

select is(
  (select count(*) from public.approved_term_snapshots),
  1::bigint,
  'an approved-term snapshot row persists'
);

-- 12. Snapshot is 1:1 with its decision.
select throws_ok(
  $$insert into public.approved_term_snapshots (
      decision_id, case_id, case_version, terms, snapshot_hash
    ) values (
      'd0000000-0000-0000-0000-0000000000e1',
      '10000000-0000-0000-0000-0000000000e1', 1,
      '{}'::jsonb, repeat('b', 64)
    )$$,
  '23505',
  null,
  'at most one approved-term snapshot per decision (1:1)'
);

-- 13-14. Snapshots are append-only.
select throws_ok(
  $$update public.approved_term_snapshots set snapshot_hash = repeat('c', 64)$$,
  '42501',
  null,
  'approved-term snapshots are append-only (no update)'
);

select throws_ok(
  $$delete from public.approved_term_snapshots$$,
  '42501',
  null,
  'approved-term snapshots are append-only (no delete)'
);

-- 15-19. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.human_credit_decisions),
  1::bigint,
  'the assigned officer can read the human credit decision'
);

select is(
  (select count(*) from public.approved_term_snapshots),
  1::bigint,
  'the assigned officer can read the approved-term snapshot'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.human_credit_decisions),
  0::bigint,
  'an unassigned actor cannot read any human credit decision'
);

select throws_ok(
  $$insert into public.human_credit_decisions (
      case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
    ) values (
      '10000000-0000-0000-0000-0000000000e1', 5,
      'APPROVED_AS_PROPOSED', 'khong duoc phep',
      '00000000-0000-0000-0000-000000000099', 'CREDIT_APPROVER'
    )$$,
  '42501',
  null,
  'authenticated users cannot write human credit decisions (service role only)'
);

select throws_ok(
  $$insert into public.approved_term_snapshots (
      decision_id, case_id, case_version, terms, snapshot_hash
    ) values (
      'd0000000-0000-0000-0000-0000000000e1',
      '10000000-0000-0000-0000-0000000000e1', 1,
      '{}'::jsonb, repeat('d', 64)
    )$$,
  '42501',
  null,
  'authenticated users cannot write approved-term snapshots (service role only)'
);

reset role;

select * from finish();
rollback;
