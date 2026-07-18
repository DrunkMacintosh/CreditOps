-- pgTAP: credit_notification_drafts / communication_receipts append-only stores
-- and the HG_CREDIT_NOTIFICATION_APPROVED gate registry extension (stage 7,
-- master design section 5 giai đoạn 7).
--
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(19);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f7',
  1,
  'CUSTOMER_NOTIFICATION',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f7',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- A permitting (APPROVED_AS_PROPOSED) human credit decision the draft binds to.
insert into public.human_credit_decisions (
  id, case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
)
values (
  'd0000000-0000-0000-0000-0000000000f7',
  '10000000-0000-0000-0000-0000000000f7', 1,
  'APPROVED_AS_PROPOSED',
  'Da phe duyet (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
);

-- A second permitting decision at version 2, so the bad-hash draft test below
-- isolates the content_hash CHECK (its composite FK to the decision is valid).
insert into public.human_credit_decisions (
  id, case_id, case_version, decision, rationale_vi, decided_by, decided_by_role
)
values (
  'd0000000-0000-0000-0000-0000000000f8',
  '10000000-0000-0000-0000-0000000000f7', 2,
  'APPROVED_AS_PROPOSED',
  'Da phe duyet ban sua (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000001', 'CREDIT_APPROVER'
);

-- 1. The extended registry accepts the synthetic stage-7 approval gate.
select lives_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-0000000000f7', 1,
      'HG_CREDIT_NOTIFICATION_APPROVED'
    )$$,
  'the extended registry accepts the synthetic notification-approval gate'
);

-- 2. An unknown gate type is still rejected: the registry stays closed.
select throws_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-0000000000f7', 1, 'HG_NOT_A_REAL_GATE'
    )$$,
  '23514',
  null,
  'an unknown gate type is still rejected by the closed registry'
);

-- 3. A valid notification draft persists (bound to the permitting decision).
insert into public.credit_notification_drafts (
  id, case_id, case_version, decision_id, content_vi, content_hash, created_by
)
values (
  'c0000000-0000-0000-0000-0000000000f7',
  '10000000-0000-0000-0000-0000000000f7', 1,
  'd0000000-0000-0000-0000-0000000000f7',
  'THONG BAO TIN DUNG (du lieu mo phong).',
  repeat('a', 64),
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.credit_notification_drafts),
  1::bigint,
  'a credit notification draft row persists'
);

-- 4. One draft per case version: a second draft at the same version fails.
select throws_ok(
  $$insert into public.credit_notification_drafts (
      case_id, case_version, decision_id, content_vi, content_hash, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f7', 1,
      'd0000000-0000-0000-0000-0000000000f7',
      'ban nhap thu hai cung phien ban', repeat('a', 64),
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23505',
  null,
  'a second draft for the same case version is rejected (unique)'
);

-- 5. The draft content_hash must be 64 lowercase hex chars.
select throws_ok(
  $$insert into public.credit_notification_drafts (
      case_id, case_version, decision_id, content_vi, content_hash, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f7', 2,
      'd0000000-0000-0000-0000-0000000000f8',
      'hash sai dinh dang', repeat('z', 64),
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'the draft content_hash must be 64 lowercase hex chars'
);

-- 6-7. Drafts are append-only.
select throws_ok(
  $$update public.credit_notification_drafts set content_vi = 'sua noi dung'$$,
  '42501',
  null,
  'credit notification drafts are append-only (no update)'
);

select throws_ok(
  $$delete from public.credit_notification_drafts$$,
  '42501',
  null,
  'credit notification drafts are append-only (no delete)'
);

-- 8. Delivery is a labelled mock: an unknown channel is rejected.
select throws_ok(
  $$insert into public.communication_receipts (
      draft_id, delivered_via, content_hash, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f7', 'EMAIL', repeat('a', 64),
      '00000000-0000-0000-0000-000000000002'
    )$$,
  '23514',
  null,
  'an unknown delivery channel is rejected (labelled mock only)'
);

-- 9. A receipt whose content_hash differs from its draft's is rejected.
select throws_ok(
  $$insert into public.communication_receipts (
      draft_id, delivered_via, content_hash, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f7', 'MOCK_CHANNEL', repeat('b', 64),
      '00000000-0000-0000-0000-000000000002'
    )$$,
  '23514',
  null,
  'a receipt content_hash that differs from the draft is rejected (trigger)'
);

-- 10. A valid mock receipt persists (matching hash, mock channel).
insert into public.communication_receipts (
  id, draft_id, delivered_via, content_hash, receipt_note_vi, recorded_by
)
values (
  'e0000000-0000-0000-0000-0000000000f7',
  'c0000000-0000-0000-0000-0000000000f7', 'MOCK_CHANNEL', repeat('a', 64),
  'Da giao mock (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000002'
);

select is(
  (select count(*) from public.communication_receipts),
  1::bigint,
  'a communication receipt row persists'
);

-- 11. A receipt is 1:1 with its draft.
select throws_ok(
  $$insert into public.communication_receipts (
      draft_id, delivered_via, content_hash, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f7', 'MOCK_CHANNEL', repeat('a', 64),
      '00000000-0000-0000-0000-000000000002'
    )$$,
  '23505',
  null,
  'at most one communication receipt per draft (1:1)'
);

-- 12-13. Receipts are append-only.
select throws_ok(
  $$update public.communication_receipts set receipt_note_vi = 'sua'$$,
  '42501',
  null,
  'communication receipts are append-only (no update)'
);

select throws_ok(
  $$delete from public.communication_receipts$$,
  '42501',
  null,
  'communication receipts are append-only (no delete)'
);

-- 14-19. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.credit_notification_drafts),
  1::bigint,
  'the assigned officer can read the notification draft'
);

select is(
  (select count(*) from public.communication_receipts),
  1::bigint,
  'the assigned officer can read the communication receipt'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.credit_notification_drafts),
  0::bigint,
  'an unassigned actor cannot read any notification draft'
);

select is(
  (select count(*) from public.communication_receipts),
  0::bigint,
  'an unassigned actor cannot read any communication receipt'
);

select throws_ok(
  $$insert into public.credit_notification_drafts (
      case_id, case_version, decision_id, content_vi, content_hash, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f7', 3,
      'd0000000-0000-0000-0000-0000000000f7',
      'khong duoc phep', repeat('a', 64),
      '00000000-0000-0000-0000-000000000099'
    )$$,
  '42501',
  null,
  'authenticated users cannot write notification drafts (service role only)'
);

select throws_ok(
  $$insert into public.communication_receipts (
      draft_id, delivered_via, content_hash, recorded_by
    ) values (
      'c0000000-0000-0000-0000-0000000000f7', 'MOCK_CHANNEL', repeat('a', 64),
      '00000000-0000-0000-0000-000000000099'
    )$$,
  '42501',
  null,
  'authenticated users cannot write communication receipts (service role only)'
);

reset role;

select * from finish();
rollback;
