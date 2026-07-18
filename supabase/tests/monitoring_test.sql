-- pgTAP: the stage-12 post-credit monitoring store (monitoring_obligations,
-- monitoring_observations, covenants, covenant_tests, early_warning_alerts +
-- alert_dispositions).  Covers the closed frequency/operator/rule/status sets,
-- the append-only rules, the temporal-separation invariant (effective_at <=
-- observed_at), the covenant-test denominator guard, the alert source-shape
-- CHECK, the per-source alert dedup indexes, the guarded alert lifecycle, the
-- mandatory disposition rationale, and RLS.
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(28);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  1,
  'POST_CREDIT_MONITORING',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- 1. An obligation persists.
insert into public.monitoring_obligations (
  id, case_id, case_version, sequence, frequency, due_date, requirement_text_vi
) values (
  'b1000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1, 1, 'MONTHLY', '2026-02-28',
  'Nop bao cao tai chinh hang thang (mo phong).'
);

select is(
  (select count(*) from public.monitoring_obligations),
  1::bigint,
  'a monitoring obligation row persists'
);

-- 2. Unknown frequency is rejected by the closed set.
select throws_ok(
  $$insert into public.monitoring_obligations (
      case_id, case_version, sequence, frequency, due_date, requirement_text_vi
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 2, 'FORTNIGHTLY', '2026-03-31', 'X'
    )$$,
  '23514',
  null,
  'an unknown obligation frequency violates the closed synthetic set'
);

-- 3-4. Obligations are append-only.
select throws_ok(
  $$update public.monitoring_obligations set due_date = '2026-01-01'$$,
  '42501',
  null,
  'monitoring obligations are append-only (no update)'
);

select throws_ok(
  $$delete from public.monitoring_obligations$$,
  '42501',
  null,
  'monitoring obligations are append-only (no delete)'
);

-- 5. An observation persists with the three separated timestamps.
insert into public.monitoring_observations (
  id, case_id, case_version, obligation_id, observation_type_vi, body_vi,
  effective_at, observed_at, recorded_by, recorded_by_role
) values (
  'c1000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'b1000000-0000-0000-0000-0000000000f1',
  'Bao cao tai chinh', 'Da nop (mo phong).',
  '2026-02-20T00:00:00Z', '2026-02-25T09:00:00Z',
  '00000000-0000-0000-0000-000000000001', 'MONITORING_OFFICER'
);

select is(
  (select count(*) from public.monitoring_observations),
  1::bigint,
  'a monitoring observation row persists'
);

-- 6. effective_at after observed_at is rejected (the one temporal invariant).
select throws_ok(
  $$insert into public.monitoring_observations (
      case_id, case_version, observation_type_vi, body_vi, effective_at,
      observed_at, recorded_by, recorded_by_role
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'X', 'Y',
      '2026-03-10T00:00:00Z', '2026-03-05T00:00:00Z',
      '00000000-0000-0000-0000-000000000001', 'MONITORING_OFFICER'
    )$$,
  '23514',
  null,
  'effective_at must not be after observed_at'
);

-- 7. Observations are append-only.
select throws_ok(
  $$update public.monitoring_observations set body_vi = 'edited'$$,
  '42501',
  null,
  'monitoring observations are append-only (no update)'
);

-- 8. A covenant persists (carrying its own declared threshold).
insert into public.covenants (
  id, case_id, case_version, name_vi, metric_key, operator, threshold_value,
  threshold_version, created_by
) values (
  'd1000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'He so bao phu no (mo phong).', 'DSCR', 'GTE', 1.20, 1,
  '00000000-0000-0000-0000-000000000001'
);

select is(
  (select count(*) from public.covenants),
  1::bigint,
  'a covenant row persists'
);

-- 9. Unknown operator is rejected by the closed set.
select throws_ok(
  $$insert into public.covenants (
      case_id, case_version, name_vi, metric_key, operator, threshold_value,
      threshold_version, created_by
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'X', 'Y', 'APPROX', 1, 1,
      '00000000-0000-0000-0000-000000000001'
    )$$,
  '23514',
  null,
  'an unknown covenant operator violates the closed synthetic set'
);

-- 10. Covenants are append-only.
select throws_ok(
  $$update public.covenants set threshold_value = 9.99$$,
  '42501',
  null,
  'covenants are append-only (a restated threshold is a new versioned row)'
);

-- 11. A covenant test persists (failing: 1000/900 < 1.20).
insert into public.covenant_tests (
  id, covenant_id, case_id, case_version, metric_key, operator, numerator,
  denominator, threshold_value, threshold_version, comparison_lhs,
  comparison_rhs, passed, tested_by, tested_by_role
) values (
  'e1000000-0000-0000-0000-0000000000f1',
  'd1000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1, 'DSCR', 'GTE', 1000, 900, 1.20, 1,
  1000, 1080.00, false,
  '00000000-0000-0000-0000-000000000001', 'MONITORING_OFFICER'
);

select is(
  (select count(*) from public.covenant_tests),
  1::bigint,
  'a covenant test row persists'
);

-- 12. A non-positive denominator is rejected by the CHECK.
select throws_ok(
  $$insert into public.covenant_tests (
      covenant_id, case_id, case_version, metric_key, operator, numerator,
      denominator, threshold_value, threshold_version, comparison_lhs,
      comparison_rhs, passed, tested_by, tested_by_role
    ) values (
      'd1000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 1, 'DSCR', 'GTE', 1000, 0, 1.20, 1,
      1000, 0, false, '00000000-0000-0000-0000-000000000001', 'MONITORING_OFFICER'
    )$$,
  '23514',
  null,
  'a non-positive covenant-test denominator is rejected by the CHECK'
);

-- 13. A COVENANT_BREACH alert bound to the failed test persists.
insert into public.early_warning_alerts (
  id, case_id, case_version, rule, detail_vi, source_covenant_test_id
) values (
  'f1000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1, 'COVENANT_BREACH',
  'Vi pham cam ket DSCR (mo phong).',
  'e1000000-0000-0000-0000-0000000000f1'
);

select is(
  (select count(*) from public.early_warning_alerts),
  1::bigint,
  'a COVENANT_BREACH early-warning alert row persists (status defaults OPEN)'
);

-- 14. Dedup: a second breach alert for the same covenant test is rejected.
select throws_ok(
  $$insert into public.early_warning_alerts (
      case_id, case_version, rule, detail_vi, source_covenant_test_id
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'COVENANT_BREACH', 'X',
      'e1000000-0000-0000-0000-0000000000f1'
    )$$,
  '23505',
  null,
  'a covenant test raises at most one COVENANT_BREACH alert (partial unique index)'
);

-- 15. Source shape: an OVERDUE_OBLIGATION alert missing its observation is rejected.
select throws_ok(
  $$insert into public.early_warning_alerts (
      case_id, case_version, rule, detail_vi, source_obligation_id
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'OVERDUE_OBLIGATION', 'X',
      'b1000000-0000-0000-0000-0000000000f1'
    )$$,
  '23514',
  null,
  'an OVERDUE_OBLIGATION alert must bind both the obligation and the observation'
);

-- 16. A forbidden alert transition (OPEN -> OPEN self-move) is rejected.
select throws_ok(
  $$update public.early_warning_alerts set status = 'OPEN'
      where id = 'f1000000-0000-0000-0000-0000000000f1'$$,
  '23514',
  null,
  'a forbidden alert transition is rejected by the trigger'
);

-- 17. Changing any non-status column is rejected (append-only identity).
select throws_ok(
  $$update public.early_warning_alerts set detail_vi = 'tampered'
      where id = 'f1000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'only the status of an early warning alert may change'
);

-- 18. A permitted disposition (OPEN -> ACKNOWLEDGED) is allowed.
select lives_ok(
  $$update public.early_warning_alerts set status = 'ACKNOWLEDGED'
      where id = 'f1000000-0000-0000-0000-0000000000f1'$$,
  'OPEN -> ACKNOWLEDGED is a permitted alert transition'
);

-- 19. Alerts are never deleted.
select throws_ok(
  $$delete from public.early_warning_alerts$$,
  '42501',
  null,
  'early warning alerts cannot be deleted'
);

-- 20. A disposition with a rationale persists.
select lives_ok(
  $$insert into public.alert_dispositions (
      alert_id, from_status, to_status, rationale_vi, actor_id, actor_role
    ) values (
      'f1000000-0000-0000-0000-0000000000f1', 'OPEN', 'ACKNOWLEDGED',
      'Da xem xet, theo doi tiep (mo phong).',
      '00000000-0000-0000-0000-000000000001', 'MONITORING_REVIEWER'
    )$$,
  'an alert disposition with a rationale persists'
);

-- 21. A disposition WITHOUT a rationale is rejected (rationale is mandatory).
select throws_ok(
  $$insert into public.alert_dispositions (
      alert_id, from_status, to_status, rationale_vi, actor_id, actor_role
    ) values (
      'f1000000-0000-0000-0000-0000000000f1', 'ACKNOWLEDGED', 'ESCALATED', null,
      '00000000-0000-0000-0000-000000000001', 'MONITORING_REVIEWER'
    )$$,
  '23502',
  null,
  'every alert disposition requires a rationale (the human authority record)'
);

-- 22. Dispositions are append-only.
select throws_ok(
  $$update public.alert_dispositions set actor_role = 'other'$$,
  '42501',
  null,
  'alert dispositions are append-only (no update)'
);

-- 23-28. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.monitoring_obligations),
  1::bigint,
  'the assigned officer can read the monitoring obligation'
);

select is(
  (select count(*) from public.early_warning_alerts),
  1::bigint,
  'the assigned officer can read the early warning alert'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.monitoring_obligations),
  0::bigint,
  'an unassigned actor cannot read any monitoring obligation'
);

select is(
  (select count(*) from public.early_warning_alerts),
  0::bigint,
  'an unassigned actor cannot read any early warning alert'
);

select throws_ok(
  $$insert into public.monitoring_obligations (
      case_id, case_version, sequence, frequency, due_date, requirement_text_vi
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 9, 'MONTHLY', '2026-09-30', 'X'
    )$$,
  '42501',
  null,
  'authenticated users cannot write monitoring obligations (service role only)'
);

select throws_ok(
  $$insert into public.early_warning_alerts (
      case_id, case_version, rule, detail_vi, source_covenant_test_id
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'COVENANT_BREACH', 'X',
      'e1000000-0000-0000-0000-0000000000f1'
    )$$,
  '42501',
  null,
  'authenticated users cannot write early warning alerts (service role only)'
);

reset role;

select * from finish();
rollback;
