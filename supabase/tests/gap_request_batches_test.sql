-- pgTAP: gap_request_batches / gap_request_items /
-- gap_request_batch_dispositions append-only stores (the pre-Risk G2 gate).
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(18);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- 1. A gap-request batch persists with a valid 64-hex snapshot hash.
insert into public.gap_request_batches (
  id, case_id, case_version, open_gap_snapshot_hash
)
values (
  '50000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
);

select is(
  (select count(*) from public.gap_request_batches),
  1::bigint,
  'a gap-request batch row persists'
);

-- 2. Idempotency key: duplicate (case, version, hash) is rejected.
select throws_ok(
  $$insert into public.gap_request_batches (
      case_id, case_version, open_gap_snapshot_hash
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    )$$,
  '23505',
  null,
  'duplicate (case, version, hash) cannot create a second batch'
);

-- 3. A non-hex / wrong-length snapshot hash is rejected.
select throws_ok(
  $$insert into public.gap_request_batches (
      case_id, case_version, open_gap_snapshot_hash
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 2, 'not-a-valid-hash'
    )$$,
  '23514',
  null,
  'the open-gap snapshot hash must be 64 lowercase hex chars'
);

-- 4. Batches are append-only.
select throws_ok(
  $$update public.gap_request_batches set case_version = 2$$,
  '42501',
  null,
  'gap-request batches are append-only (no update)'
);

select throws_ok(
  $$delete from public.gap_request_batches$$,
  '42501',
  null,
  'gap-request batches are append-only (no delete)'
);

-- 5. A drafted item persists and is append-only.
insert into public.gap_request_items (
  id, batch_id, case_id, case_version, gap_id, request_text_vi, blocking_level
)
values (
  '60000000-0000-0000-0000-0000000000f1',
  '50000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  '61000000-0000-0000-0000-0000000000f1',
  'De nghi bo sung bao cao tai chinh (du lieu mo phong).',
  'CONDITIONAL'
);

select is(
  (select count(*) from public.gap_request_items),
  1::bigint,
  'a drafted gap-request item persists'
);

select throws_ok(
  $$update public.gap_request_items set request_text_vi = 'sua doi'$$,
  '42501',
  null,
  'gap-request items are append-only (no update)'
);

select throws_ok(
  $$delete from public.gap_request_items$$,
  '42501',
  null,
  'gap-request items are append-only (no delete)'
);

-- 6. A human disposition persists.
insert into public.gap_request_batch_dispositions (
  id, batch_id, case_id, case_version, disposition_type,
  item_dispositions, edited_texts, actor_id, actor_role, rationale_vi
)
values (
  '70000000-0000-0000-0000-0000000000f1',
  '50000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'APPROVED_WITH_CHANGES',
  '{"60000000-0000-0000-0000-0000000000f1": "APPROVED"}'::jsonb,
  '{}'::jsonb,
  '00000000-0000-0000-0000-000000000001', 'INTAKE_OFFICER',
  'Phe duyet co dieu chinh (du lieu mo phong).'
);

select is(
  (select count(*) from public.gap_request_batch_dispositions),
  1::bigint,
  'a human batch disposition persists'
);

-- 7. Unknown disposition type is rejected by the closed check set.
select throws_ok(
  $$insert into public.gap_request_batch_dispositions (
      batch_id, case_id, case_version, disposition_type,
      actor_id, actor_role, rationale_vi
    ) values (
      '50000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 1,
      'MAYBE_LATER',
      '00000000-0000-0000-0000-000000000001', 'INTAKE_OFFICER', 'khong hop le'
    )$$,
  '23514',
  null,
  'an unknown disposition type violates the closed set'
);

-- 8. Dispositions are append-only.
select throws_ok(
  $$update public.gap_request_batch_dispositions set rationale_vi = 'sua doi'$$,
  '42501',
  null,
  'batch dispositions are append-only (no update)'
);

select throws_ok(
  $$delete from public.gap_request_batch_dispositions$$,
  '42501',
  null,
  'batch dispositions are append-only (no delete)'
);

-- 9. RLS: the assigned officer reads; an unassigned actor sees nothing; writes
-- remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.gap_request_batches),
  1::bigint,
  'the assigned officer can read the gap-request batch'
);

select is(
  (select count(*) from public.gap_request_items),
  1::bigint,
  'the assigned officer can read gap-request items'
);

select is(
  (select count(*) from public.gap_request_batch_dispositions),
  1::bigint,
  'the assigned officer can read batch dispositions'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.gap_request_batches),
  0::bigint,
  'an unassigned actor cannot read any gap-request batch'
);

select throws_ok(
  $$insert into public.gap_request_batches (
      case_id, case_version, open_gap_snapshot_hash
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 3,
      'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
    )$$,
  '42501',
  null,
  'authenticated users cannot write gap-request batches (service role only)'
);

select throws_ok(
  $$insert into public.gap_request_batch_dispositions (
      batch_id, case_id, case_version, disposition_type,
      actor_id, actor_role, rationale_vi
    ) values (
      '50000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 1, 'REJECTED',
      '00000000-0000-0000-0000-000000000099', 'INTAKE_OFFICER', 'khong duoc phep'
    )$$,
  '42501',
  null,
  'authenticated users cannot write dispositions directly (service role only)'
);

reset role;

select * from finish();
rollback;
