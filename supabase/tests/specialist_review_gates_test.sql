-- Stage 4 & 5 (master design section 5 giai đoạn 4-5, section 6.1 rows 4-5): the
-- three new synthetic gates -- HG_UNDERWRITING_ASSESSMENT_REVIEWED,
-- HG_LEGAL_ASSESSMENT_REVIEWED and HG_MAKER_SUBMISSION_CONFIRMED -- join the
-- closed registry additively, while unknown gate types are still rejected.
--
-- All identifiers below are synthetic and created solely for demonstration.

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(5);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

-- The stage-4 specialist-review gates are accepted by the extended registry.
select lives_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-000000000001', 1,
      'HG_UNDERWRITING_ASSESSMENT_REVIEWED'
    )$$,
  'the extended registry accepts the synthetic underwriting-review gate'
);

select lives_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-000000000001', 1,
      'HG_LEGAL_ASSESSMENT_REVIEWED'
    )$$,
  'the extended registry accepts the synthetic legal-review gate'
);

-- The stage-5 maker-submission gate is accepted too.
select lives_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-000000000001', 1,
      'HG_MAKER_SUBMISSION_CONFIRMED'
    )$$,
  'the extended registry accepts the synthetic maker-submission gate'
);

-- The prior registry stays valid: a pre-existing synthetic gate still inserts.
select lives_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-000000000001', 1, 'HG_FINANCING_NEED_CONFIRMED'
    )$$,
  'a pre-existing synthetic gate remains valid (additive superset)'
);

-- An unknown gate type is still rejected: the registry stays closed.
select throws_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-000000000001', 1, 'HG_NOT_A_REAL_GATE'
    )$$,
  '23514',
  null,
  'an unknown gate type is still rejected by the closed registry'
);

select * from finish();
rollback;
