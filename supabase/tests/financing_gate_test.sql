-- Stage 2 (master design section 5 stage 2, section 13): the new synthetic
-- HG_FINANCING_NEED_CONFIRMED gate joins the closed registry additively, unknown
-- gate types are still rejected, and the versioned financing_requests table keeps
-- its append-only + (case, version) uniqueness guarantees for the new fields.
--
-- All identifiers below are synthetic and created solely for demonstration.

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(6);

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

-- The financing-need gate is accepted by the extended registry.
select lives_ok(
  $$insert into public.human_gates (case_id, case_version, gate_type)
    values (
      '10000000-0000-0000-0000-000000000001', 1, 'HG_FINANCING_NEED_CONFIRMED'
    )$$,
  'the extended registry accepts the synthetic financing-need gate'
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

-- A financing-request version carrying the new stage-2 fields, all optional.
insert into public.financing_requests (
  id, case_id, case_version, request_version, requested_amount, purpose_vi,
  currency, product_vi, term_months, expected_use_date, repayment_source_vi,
  customer_own_funds, working_capital_cycle_vi, created_by
)
values (
  '20000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  1,
  1,
  5000000000,
  'Bổ sung vốn lưu động',
  'VND',
  'Hạn mức tín dụng',
  12,
  '2026-08-01',
  'Dòng tiền từ hoạt động kinh doanh',
  1000000000,
  'Khoảng 90 ngày',
  '00000000-0000-0000-0000-000000000001'
);

-- Append-only: a stored financing-request version cannot be mutated in place.
-- Run under the privileged migration role (which HOLDS update/delete privilege)
-- so the append-only TRIGGER is what raises, not a permission error -- exactly
-- as financing_requests_test.sql exercises it.
select throws_ok(
  $$update public.financing_requests set currency = 'USD'
    where id = '20000000-0000-0000-0000-000000000001'$$,
  '42501',
  'financing requests are append-only',
  'a financing-request version cannot be updated in place'
);

select throws_ok(
  $$delete from public.financing_requests
    where id = '20000000-0000-0000-0000-000000000001'$$,
  '42501',
  'financing requests are append-only',
  'a financing-request version cannot be deleted'
);

-- A new edit is a NEW row at the next version; (case, version) is unique.
select lives_ok(
  $$insert into public.financing_requests (
      case_id, case_version, request_version, requested_amount, purpose_vi,
      currency, created_by
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, 2, 5000000000,
      'Bổ sung vốn lưu động (điều chỉnh)', 'VND',
      '00000000-0000-0000-0000-000000000001'
    )$$,
  'an edit appends a new financing-request version'
);

select throws_ok(
  $$insert into public.financing_requests (
      case_id, case_version, request_version, requested_amount, purpose_vi,
      created_by
    ) values (
      '10000000-0000-0000-0000-000000000001', 1, 2, 5000000000,
      'Trùng phiên bản', '00000000-0000-0000-0000-000000000001'
    )$$,
  '23505',
  null,
  'a duplicate (case, version) financing-request row is rejected'
);

select * from finish();
rollback;
