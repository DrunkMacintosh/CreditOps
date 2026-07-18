-- pgTAP: stage-8 contract packages / redlines / MOCK signature evidence stores
-- (deterministic contract packages, versioned redlines, mock signing) plus the
-- three new human gates.
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(25);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  1,
  'AWAITING_CONTRACT_SIGNING',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- A permitting credit decision (the contract package's decision_id FK).
insert into public.human_credit_decisions (
  id, case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
)
values (
  'd0000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'APPROVED_AS_PROPOSED',
  'Da phe duyet (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
);

-- 1. A DRAFT contract package persists.
insert into public.contract_packages (
  id, case_id, case_version, decision_id, term_snapshot_hash, content_vi,
  content_hash, package_version, state, created_by
)
values (
  'c0000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'd0000000-0000-0000-0000-0000000000f1',
  repeat('a', 64), 'Hop dong mo phong - ban nhap.', repeat('b', 64),
  1, 'DRAFT', '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.contract_packages),
  1::bigint,
  'a DRAFT contract package row persists'
);

-- 2. Unknown state is rejected by the closed state set.
select throws_ok(
  $$insert into public.contract_packages (
      case_id, case_version, decision_id, term_snapshot_hash, content_vi,
      content_hash, package_version, state, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1',
      repeat('a', 64), 'x', repeat('b', 64), 9, 'SIGNED',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'an unknown package state violates the closed state set'
);

-- 3. content_hash must be 64 lowercase hex chars.
select throws_ok(
  $$insert into public.contract_packages (
      case_id, case_version, decision_id, term_snapshot_hash, content_vi,
      content_hash, package_version, state, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1',
      repeat('a', 64), 'x', repeat('z', 64), 9, 'DRAFT',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'the content hash must be 64 lowercase hex chars'
);

-- 4. Duplicate (case, version, package_version) is rejected.
select throws_ok(
  $$insert into public.contract_packages (
      case_id, case_version, decision_id, term_snapshot_hash, content_vi,
      content_hash, package_version, state, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1',
      repeat('a', 64), 'y', repeat('b', 64), 1, 'REDLINED',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23505',
  null,
  'a duplicate package_version for the same case version is rejected (unique)'
);

-- 5-6. Packages are append-only.
select throws_ok(
  $$update public.contract_packages set state = 'REDLINED'$$,
  '42501',
  null,
  'contract packages are append-only (no update)'
);

select throws_ok(
  $$delete from public.contract_packages$$,
  '42501',
  null,
  'contract packages are append-only (no delete)'
);

-- 7. The redline + new REDLINED package version pair (the "single transaction"
--    shape): a redline row references the base package, and a new package
--    version 2 in state REDLINED coexists with it.
insert into public.contract_redlines (
  id, package_id, case_id, case_version, redline_version, change_note_vi,
  changed_content_vi, changed_content_hash, created_by
)
values (
  'e0000000-0000-0000-0000-0000000000f1',
  'c0000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1, 1,
  'Sua dieu khoan lai suat.', 'Hop dong mo phong - ban redline.', repeat('c', 64),
  '00000000-0000-0000-0000-000000000001'
);

insert into public.contract_packages (
  id, case_id, case_version, decision_id, term_snapshot_hash, content_vi,
  content_hash, package_version, state, created_by
)
values (
  'c0000000-0000-0000-0000-0000000000f2',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'd0000000-0000-0000-0000-0000000000f1',
  repeat('a', 64), 'Hop dong mo phong - ban redline.', repeat('c', 64),
  2, 'REDLINED', '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.contract_redlines
    where package_id = 'c0000000-0000-0000-0000-0000000000f1'),
  1::bigint,
  'a redline row persists against its base package'
);

select is(
  (select state from public.contract_packages
    where case_id = '10000000-0000-0000-0000-0000000000f1'
      and package_version = 2),
  'REDLINED',
  'the redline appends a new REDLINED package version'
);

-- 8. A redline referencing a package on a different case version is rejected by
--    the composite FK.
select throws_ok(
  $$insert into public.contract_redlines (
      package_id, case_id, case_version, redline_version, change_note_vi,
      changed_content_vi, changed_content_hash, created_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 2, 1,
      'sai phien ban', 'x', repeat('c', 64),
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23503',
  null,
  'a redline cannot bind a different case version than its package'
);

-- 9. Duplicate redline_version per package is rejected.
select throws_ok(
  $$insert into public.contract_redlines (
      package_id, case_id, case_version, redline_version, change_note_vi,
      changed_content_vi, changed_content_hash, created_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 1, 1,
      'trung redline_version', 'x', repeat('c', 64),
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23505',
  null,
  'a duplicate redline_version per package is rejected (unique)'
);

-- 10-11. Redlines are append-only.
select throws_ok(
  $$update public.contract_redlines set change_note_vi = 'x'$$,
  '42501',
  null,
  'contract redlines are append-only (no update)'
);

select throws_ok(
  $$delete from public.contract_redlines$$,
  '42501',
  null,
  'contract redlines are append-only (no delete)'
);

-- 12. MOCK signature evidence persists against the READY_FOR_SIGNATURE version.
insert into public.contract_packages (
  id, case_id, case_version, decision_id, term_snapshot_hash, content_vi,
  content_hash, package_version, state, created_by
)
values (
  'c0000000-0000-0000-0000-0000000000f3',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'd0000000-0000-0000-0000-0000000000f1',
  repeat('a', 64), 'Hop dong mo phong - ban ky.', repeat('c', 64),
  3, 'READY_FOR_SIGNATURE', '00000000-0000-0000-0000-000000000001'
);

insert into public.contract_signature_evidence (
  id, package_id, case_id, case_version, kind, signer_names, evidence_note_vi,
  recorded_by
)
values (
  '50000000-0000-0000-0000-0000000000f1',
  'c0000000-0000-0000-0000-0000000000f3',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'MOCK_SIGNATURE', '["Nguyen Van A (mo phong)"]'::jsonb, 'Bang chung ky mo phong.',
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.contract_signature_evidence),
  1::bigint,
  'a MOCK signature-evidence row persists'
);

-- 13. Unknown signing kind is rejected (real e-sign is out of scope).
select throws_ok(
  $$insert into public.contract_signature_evidence (
      package_id, case_id, case_version, kind, signer_names, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f3',
      '10000000-0000-0000-0000-0000000000f1', 1,
      'REAL_ESIGN', '["x"]'::jsonb, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a non-MOCK signing kind is rejected (only MOCK_SIGNATURE)'
);

-- 14. An empty signer_names array is rejected.
select throws_ok(
  $$insert into public.contract_signature_evidence (
      package_id, case_id, case_version, kind, signer_names, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f2',
      '10000000-0000-0000-0000-0000000000f1', 1,
      'MOCK_SIGNATURE', '[]'::jsonb, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'signer_names must be a non-empty JSON array'
);

-- 15. signer_names must be a JSON array, not an object.
select throws_ok(
  $$insert into public.contract_signature_evidence (
      package_id, case_id, case_version, kind, signer_names, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f2',
      '10000000-0000-0000-0000-0000000000f1', 1,
      'MOCK_SIGNATURE', '{}'::jsonb, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'signer_names must be a JSON array'
);

-- 16. Signature evidence is 1:1 with its package version.
select throws_ok(
  $$insert into public.contract_signature_evidence (
      package_id, case_id, case_version, kind, signer_names, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f3',
      '10000000-0000-0000-0000-0000000000f1', 1,
      'MOCK_SIGNATURE', '["x"]'::jsonb, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23505',
  null,
  'at most one signature-evidence row per package version (1:1)'
);

-- 17-18. Signature evidence is append-only.
select throws_ok(
  $$update public.contract_signature_evidence set evidence_note_vi = 'x'$$,
  '42501',
  null,
  'signature evidence is append-only (no update)'
);

select throws_ok(
  $$delete from public.contract_signature_evidence$$,
  '42501',
  null,
  'signature evidence is append-only (no delete)'
);

-- 19. The three new stage-8 gate types are accepted by the extended registry.
insert into public.human_gates (case_id, case_version, gate_type)
values
  ('10000000-0000-0000-0000-0000000000f1', 1, 'HG_CONTRACT_PACKAGE_APPROVED'),
  ('10000000-0000-0000-0000-0000000000f1', 1, 'HG_SIGNATURE_AUTHORITY_CONFIRMED'),
  ('10000000-0000-0000-0000-0000000000f1', 1, 'HG_CONTRACTS_SIGNED');

select is(
  (select count(*) from public.human_gates
    where gate_type in (
      'HG_CONTRACT_PACKAGE_APPROVED', 'HG_SIGNATURE_AUTHORITY_CONFIRMED',
      'HG_CONTRACTS_SIGNED'
    )),
  3::bigint,
  'the three stage-8 gate types are accepted by the extended registry'
);

-- 20. The union superset still accepts the prior migrations'' gate types (the
--     ...18 disbursement gate and the ...19 perfection gate both stay valid).
insert into public.human_gates (case_id, case_version, gate_type)
values
  ('10000000-0000-0000-0000-0000000000f1', 1, 'HG_DISBURSEMENT_CONDITIONS_CONFIRMED'),
  ('10000000-0000-0000-0000-0000000000f1', 1, 'HG_SECURITY_PERFECTION_CONFIRMED');

select is(
  (select count(*) from public.human_gates
    where gate_type in (
      'HG_DISBURSEMENT_CONDITIONS_CONFIRMED', 'HG_SECURITY_PERFECTION_CONFIRMED'
    )),
  2::bigint,
  'the union superset keeps the prior migrations gate types valid'
);

-- 21-24. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.contract_packages),
  3::bigint,
  'the assigned officer can read the contract packages'
);

select is(
  (select count(*) from public.contract_signature_evidence),
  1::bigint,
  'the assigned officer can read the signature evidence'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.contract_packages),
  0::bigint,
  'an unassigned actor cannot read any contract package'
);

select throws_ok(
  $$insert into public.contract_packages (
      case_id, case_version, decision_id, term_snapshot_hash, content_vi,
      content_hash, package_version, state, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1',
      repeat('a', 64), 'x', repeat('b', 64), 9, 'DRAFT',
      '00000000-0000-0000-0000-000000000099'
    )$$,
  '42501',
  null,
  'authenticated users cannot write contract packages (service role only)'
);

reset role;

select * from finish();
rollback;
