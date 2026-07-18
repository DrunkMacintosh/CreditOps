-- pgTAP: security_interests / security_perfection_items (the stage-9 per-asset
-- security-perfection ledger).  Covers the closed asset_kind + status sets, the
-- guarded status-transition graph, the COMPLETED-needs-evidence CHECK, the
-- append-only interest + no-delete item rules, and RLS.
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
  'AWAITING_SECURITY_PERFECTION',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- 1. A per-asset security interest persists.
insert into public.security_interests (
  id, case_id, case_version, asset_description_vi, asset_kind, owner_name_vi,
  valuation_reference, created_by
)
values (
  'a1000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'Quyen su dung dat tai Vinh Phuc (mo phong).',
  'REAL_ESTATE', 'Ben bao dam Demo',
  'valuation-adapter://demo/asset-1',
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.security_interests),
  1::bigint,
  'a per-asset security interest row persists'
);

-- 2. Unknown asset_kind is rejected by the closed synthetic set.
select throws_ok(
  $$insert into public.security_interests (
      case_id, case_version, asset_description_vi, asset_kind, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'X',
      'SPACESHIP', '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'an unknown asset_kind violates the closed synthetic set'
);

-- 3-4. Interests are append-only.
select throws_ok(
  $$update public.security_interests set asset_kind = 'VEHICLE'$$,
  '42501',
  null,
  'security interests are append-only (no update)'
);

select throws_ok(
  $$delete from public.security_interests$$,
  '42501',
  null,
  'security interests are append-only (no delete)'
);

-- 5. A perfection item persists (defaults to PENDING).
insert into public.security_perfection_items (id, interest_id, requirement_vi)
values (
  'b1000000-0000-0000-0000-0000000000f1',
  'a1000000-0000-0000-0000-0000000000f1',
  'Dang ky bien phap bao dam (mo phong).'
);

select is(
  (select count(*) from public.security_perfection_items),
  1::bigint,
  'a perfection item row persists'
);

-- 6. Unknown status is rejected by the closed synthetic set.
select throws_ok(
  $$insert into public.security_perfection_items (interest_id, requirement_vi, status)
    values (
      'a1000000-0000-0000-0000-0000000000f1', 'X', 'FILED_SOMEWHERE'
    )$$,
  '23514',
  null,
  'an unknown perfection status violates the closed synthetic set'
);

-- 7. A forbidden transition (PENDING -> EXPIRED) is rejected by the trigger.
select throws_ok(
  $$update public.security_perfection_items
      set status = 'EXPIRED'
      where id = 'b1000000-0000-0000-0000-0000000000f1'$$,
  '23514',
  null,
  'a forbidden status transition is rejected by the trigger'
);

-- 8. A permitted transition (PENDING -> EVIDENCE_ATTACHED) is allowed.
select lives_ok(
  $$update public.security_perfection_items
      set status = 'EVIDENCE_ATTACHED',
          evidence_refs = '["storage://demo/receipt-1"]'::jsonb
      where id = 'b1000000-0000-0000-0000-0000000000f1'$$,
  'PENDING -> EVIDENCE_ATTACHED is a permitted transition'
);

-- 9. COMPLETED without evidence/completion metadata is rejected by the CHECK.
--    (Transition EVIDENCE_ATTACHED -> COMPLETED is graph-valid, so the CHECK,
--    not the trigger, is what fails here.)
select throws_ok(
  $$update public.security_perfection_items
      set status = 'COMPLETED', evidence_refs = '[]'::jsonb
      where id = 'b1000000-0000-0000-0000-0000000000f1'$$,
  '23514',
  null,
  'a COMPLETED item without evidence is rejected by the CHECK'
);

-- 10. A COMPLETED item with evidence + completion metadata is allowed.
select lives_ok(
  $$update public.security_perfection_items
      set status = 'COMPLETED',
          completed_by = '00000000-0000-0000-0000-000000000001',
          completed_at = clock_timestamp()
      where id = 'b1000000-0000-0000-0000-0000000000f1'$$,
  'EVIDENCE_ATTACHED -> COMPLETED with evidence is permitted'
);

-- 11. COMPLETED -> EXPIRED is permitted (evidence metadata is retained).
select lives_ok(
  $$update public.security_perfection_items
      set status = 'EXPIRED'
      where id = 'b1000000-0000-0000-0000-0000000000f1'$$,
  'COMPLETED -> EXPIRED is a permitted transition'
);

-- 12. Items are never deleted.
select throws_ok(
  $$delete from public.security_perfection_items$$,
  '42501',
  null,
  'perfection items cannot be deleted'
);

-- 13-18. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.security_interests),
  1::bigint,
  'the assigned officer can read the security interest'
);

select is(
  (select count(*) from public.security_perfection_items),
  1::bigint,
  'the assigned officer can read the perfection item'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.security_interests),
  0::bigint,
  'an unassigned actor cannot read any security interest'
);

select is(
  (select count(*) from public.security_perfection_items),
  0::bigint,
  'an unassigned actor cannot read any perfection item'
);

select throws_ok(
  $$insert into public.security_interests (
      case_id, case_version, asset_description_vi, asset_kind, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'X', 'OTHER',
      '00000000-0000-0000-0000-000000000099'
    )$$,
  '42501',
  null,
  'authenticated users cannot write security interests (service role only)'
);

select throws_ok(
  $$insert into public.security_perfection_items (interest_id, requirement_vi)
    values ('a1000000-0000-0000-0000-0000000000f1', 'X')$$,
  '42501',
  null,
  'authenticated users cannot write perfection items (service role only)'
);

reset role;

select * from finish();
rollback;
