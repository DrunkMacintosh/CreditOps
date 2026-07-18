-- pgTAP: disbursement_conditions / condition_status_events (the stage-10
-- disbursement ConditionLedger).  Proves the CLOSED status set, the
-- deterministic transition trigger (allowed edge succeeds; forbidden edge and
-- identity mutation rejected), the append-only status-event trail, and RLS.
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(17);

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

-- The source (permitting) human credit decision the conditions bind to.
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

-- 1. A disbursement condition persists (defaults to PENDING).
insert into public.disbursement_conditions (
  id, case_id, case_version, decision_id, condition_text_vi, owner_vi
)
values (
  'c0000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  'd0000000-0000-0000-0000-0000000000f1',
  'Hop dong bao dam da ky.', 'CV van hanh'
);

select is(
  (select status from public.disbursement_conditions
   where id = 'c0000000-0000-0000-0000-0000000000f1'),
  'PENDING',
  'a disbursement condition persists and defaults to PENDING'
);

-- 2. Unknown status is rejected by the closed synthetic taxonomy.
select throws_ok(
  $$insert into public.disbursement_conditions (
      case_id, case_version, decision_id, condition_text_vi, status
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', 'trang thai la', 'CONFIRMED_BY_AGENT'
    )$$,
  '23514',
  null,
  'an unknown status violates the closed synthetic taxonomy'
);

-- 3. evidence_refs must be a JSON array.
select throws_ok(
  $$insert into public.disbursement_conditions (
      case_id, case_version, decision_id, condition_text_vi, evidence_refs
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', 'evidence sai kieu', '{}'::jsonb
    )$$,
  '23514',
  null,
  'evidence_refs must be a JSON array'
);

-- 4. condition_text must be non-empty.
select throws_ok(
  $$insert into public.disbursement_conditions (
      case_id, case_version, decision_id, condition_text_vi
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', '   '
    )$$,
  '23514',
  null,
  'a blank condition text is rejected'
);

-- 5. The composite FK binds the exact (decision, case, version) triple: a
--    mismatched case version has no parent decision.
select throws_ok(
  $$insert into public.disbursement_conditions (
      case_id, case_version, decision_id, condition_text_vi
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 2,
      'd0000000-0000-0000-0000-0000000000f1', 'sai phien ban'
    )$$,
  '23503',
  null,
  'a condition cannot bind a different case version than its source decision'
);

-- 6. An ALLOWED transition (PENDING -> EVIDENCE_SUBMITTED) succeeds.
update public.disbursement_conditions
  set status = 'EVIDENCE_SUBMITTED',
      evidence_refs = '["doc://hop-dong"]'::jsonb
  where id = 'c0000000-0000-0000-0000-0000000000f1';

select is(
  (select status from public.disbursement_conditions
   where id = 'c0000000-0000-0000-0000-0000000000f1'),
  'EVIDENCE_SUBMITTED',
  'an allowed transition (with evidence) is applied'
);

-- 7. A FORBIDDEN transition (EVIDENCE_SUBMITTED -> WAIVED_BY_HUMAN) is rejected.
select throws_ok(
  $$update public.disbursement_conditions
      set status = 'WAIVED_BY_HUMAN'
      where id = 'c0000000-0000-0000-0000-0000000000f1'$$,
  '23514',
  null,
  'a forbidden status transition is rejected by the trigger'
);

-- 8. Only status/evidence_refs may change; mutating identity is rejected.
select throws_ok(
  $$update public.disbursement_conditions
      set condition_text_vi = 'sua noi dung'
      where id = 'c0000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'only status and evidence_refs of a condition may change'
);

-- 9. Conditions cannot be deleted.
select throws_ok(
  $$delete from public.disbursement_conditions
      where id = 'c0000000-0000-0000-0000-0000000000f1'$$,
  '42501',
  null,
  'disbursement conditions cannot be deleted'
);

-- 10. A status event persists (the creation event: null -> PENDING).
insert into public.condition_status_events (
  id, condition_id, from_status, to_status, actor_id, actor_role
)
values (
  'e0000000-0000-0000-0000-0000000000f1',
  'c0000000-0000-0000-0000-0000000000f1',
  null, 'PENDING',
  '00000000-0000-0000-0000-000000000001', 'OPS_OFFICER'
);

select is(
  (select count(*) from public.condition_status_events),
  1::bigint,
  'a condition status event persists'
);

-- 11. Unknown to_status in an event is rejected.
select throws_ok(
  $$insert into public.condition_status_events (
      condition_id, to_status, actor_id, actor_role
    ) values (
      'c0000000-0000-0000-0000-0000000000f1', 'CONFIRMED_BY_AGENT',
      '00000000-0000-0000-0000-000000000001', 'OPS_OFFICER'
    )$$,
  '23514',
  null,
  'an unknown event to_status is rejected'
);

-- 11b. A waiver / not-applicable event MUST carry an authority rationale.
select throws_ok(
  $$insert into public.condition_status_events (
      condition_id, from_status, to_status, actor_id, actor_role
    ) values (
      'c0000000-0000-0000-0000-0000000000f1', 'WAIVER_REQUESTED', 'WAIVED_BY_HUMAN',
      '00000000-0000-0000-0000-000000000001', 'OPS_CHECKER'
    )$$,
  '23514',
  null,
  'a waiver event without an authority rationale is rejected'
);

-- 12-13. Status events are append-only.
select throws_ok(
  $$update public.condition_status_events set to_status = 'VERIFIED'$$,
  '42501',
  null,
  'condition status events are append-only (no update)'
);

select throws_ok(
  $$delete from public.condition_status_events$$,
  '42501',
  null,
  'condition status events are append-only (no delete)'
);

-- 14-16. RLS: the assigned officer reads; an unassigned actor sees nothing;
-- writes remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.disbursement_conditions),
  1::bigint,
  'the assigned officer can read the disbursement condition'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.disbursement_conditions),
  0::bigint,
  'an unassigned actor cannot read any disbursement condition'
);

select throws_ok(
  $$insert into public.disbursement_conditions (
      case_id, case_version, decision_id, condition_text_vi
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1,
      'd0000000-0000-0000-0000-0000000000f1', 'khong duoc phep'
    )$$,
  '42501',
  null,
  'authenticated users cannot write disbursement conditions (service role only)'
);

reset role;

select * from finish();
rollback;
