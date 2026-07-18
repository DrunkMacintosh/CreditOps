-- pgTAP: prospects / prospect_screening_snapshots / prospect_contact_decisions
-- (the Stage 1 pre-case store).  All data below is synthetic and created solely
-- for demonstration; the prospect is the invented SME "Cong ty TNHH Nong San
-- Sach Vinh Phuc Demo".
--
-- These tables are PRE-CASE: there is no credit_cases / case_assignments row.
-- Ownership (and therefore RLS scope) is the prospect's creator alone.

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(18);

-- 1. A prospect persists.
insert into public.prospects (
  id, name_vi, industry_vi, years_operating, revenue_band_vi,
  legal_status_vi, notes_vi, created_by
)
values (
  '10000000-0000-0000-0000-0000000000a1',
  'Cong ty TNHH Nong San Sach Vinh Phuc Demo',
  'Nong nghiep', 7, 'Duoi 100 ty',
  'Dang hoat dong', 'Du lieu mo phong.',
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.prospects),
  1::bigint,
  'a prospect row persists'
);

-- 2. A negative years_operating is rejected.
select throws_ok(
  $$insert into public.prospects (name_vi, years_operating, created_by)
    values ('Demo', -1, '00000000-0000-0000-0000-000000000001')$$,
  '23514',
  null,
  'years_operating cannot be negative'
);

-- 3. Prospects are append-only.
select throws_ok(
  $$update public.prospects set name_vi = 'Doi ten'$$,
  '42501',
  null,
  'prospects are append-only (no update)'
);

select throws_ok(
  $$delete from public.prospects$$,
  '42501',
  null,
  'prospects are append-only (no delete)'
);

-- 4. A screening snapshot persists (descriptive only, no verdict).
insert into public.prospect_screening_snapshots (
  id, prospect_id, version, screening_config_version,
  industry_vi, years_operating, revenue_band_vi, legal_status_vi,
  credit_history_vi, risk_appetite_note_vi, details, created_by
)
values (
  '20000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 1, 'screening-config-synthetic-v1',
  'Nong nghiep', 7, 'Duoi 100 ty', 'Dang hoat dong',
  'Chua co du lieu CIC (mo phong)', 'Ghi chu mo ta, khong phai ket luan',
  '{"nguon": "mo phong"}'::jsonb,
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.prospect_screening_snapshots),
  1::bigint,
  'a descriptive screening snapshot persists'
);

-- 5. A duplicate snapshot version for the same prospect is rejected.
select throws_ok(
  $$insert into public.prospect_screening_snapshots (
      prospect_id, version, screening_config_version, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 1, 'screening-config-synthetic-v1',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23505',
  null,
  'a duplicate (prospect, version) snapshot cannot be created'
);

-- 6. A blank screening_config_version is rejected.
select throws_ok(
  $$insert into public.prospect_screening_snapshots (
      prospect_id, version, screening_config_version, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 2, '   ',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'the labelled screening config version must be non-blank'
);

-- 7. A non-object details payload is rejected.
select throws_ok(
  $$insert into public.prospect_screening_snapshots (
      prospect_id, version, screening_config_version, details, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 3, 'screening-config-synthetic-v1',
      '[1,2,3]'::jsonb, '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'screening details must be a JSON object'
);

-- 8. Screening snapshots are append-only.
select throws_ok(
  $$update public.prospect_screening_snapshots set version = 9$$,
  '42501',
  null,
  'screening snapshots are append-only (no update)'
);

-- 9. A human contact decision persists.
insert into public.prospect_contact_decisions (
  id, prospect_id, decision, rationale_vi, decided_by
)
values (
  '30000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 'CONTACT',
  'RM quyet dinh lien he (du lieu mo phong).',
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.prospect_contact_decisions),
  1::bigint,
  'a human contact decision persists'
);

-- 10. An unknown decision value violates the closed check set.
select throws_ok(
  $$insert into public.prospect_contact_decisions (
      prospect_id, decision, rationale_vi, decided_by
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 'MAYBE',
      'khong hop le', '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'an unknown contact decision violates the closed set'
);

-- 11. A blank rationale is rejected (the human must give a reason).
select throws_ok(
  $$insert into public.prospect_contact_decisions (
      prospect_id, decision, rationale_vi, decided_by
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 'DEFER',
      '   ', '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'a contact decision requires a non-blank rationale'
);

-- 12. Contact decisions are append-only.
select throws_ok(
  $$delete from public.prospect_contact_decisions$$,
  '42501',
  null,
  'contact decisions are append-only (no delete)'
);

-- 13. RLS: the owner reads their own prospect + children; a non-owner sees
-- nothing; writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.prospects),
  1::bigint,
  'the owning officer can read their prospect'
);

select is(
  (select count(*) from public.prospect_screening_snapshots),
  1::bigint,
  'the owning officer can read their screening snapshot'
);

select is(
  (select count(*) from public.prospect_contact_decisions),
  1::bigint,
  'the owning officer can read their contact decision'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.prospects),
  0::bigint,
  'a non-owner cannot read any prospect'
);

select throws_ok(
  $$insert into public.prospects (name_vi, created_by)
    values ('Khong duoc phep', '00000000-0000-0000-0000-000000000099')$$,
  '42501',
  null,
  'authenticated users cannot write prospects (service role only)'
);

reset role;

select * from finish();
rollback;
