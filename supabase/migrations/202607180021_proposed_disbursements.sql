-- Stage 11 (master design section 5 giai đoạn 11 "Giải ngân vốn vay",
-- section 6.1 row 11): the proposed disbursement action, its two SEPARATE human
-- gates (HG_DISBURSEMENT_VALIDATED then HG_DISBURSEMENT_AUTHORIZED, satisfied by
-- DIFFERENT actors), and the append-only labelled-mock execution receipts.
--
-- PROPOSED / ASSUMPTION: 'HG_DISBURSEMENT_VALIDATED' and
-- 'HG_DISBURSEMENT_AUTHORIZED' are SYNTHETIC gate names.  They carry NO official
-- SHB role mapping, disbursement authority matrix, or control code and are
-- presented only as demonstration application controls, exactly like the
-- existing G1..G4 and the other HG_ gates.  Additive only: the CHECK re-declared
-- below is a strict SUPERSET of the prior registry (202607180020) plus the two
-- new stage-11 gates, so every existing human_gates row stays valid.
--
-- SPEC CONTRACT encoded here (master design section 5 giai đoạn 11):
--
-- - A proposed_disbursement_actions row is DERIVED from an approved credit
--   decision (composite FK to the exact decision + case + version triple, so an
--   action can never drift onto a different case version than its decision).  The
--   amount is an EXACT decimal stored as TEXT (no float on money); currency is
--   required; beneficiary/account are SYNTHETIC references.  The currency-aware /
--   cap-aware validation against the ApprovedTermSnapshot happens in the
--   application layer before the insert.
-- - Execution runs ONLY through the labelled mock adapter after BOTH gates.  The
--   action's status moves along a DETERMINISTIC edge set (BEFORE UPDATE trigger,
--   the SAME map as domain/disbursements.py::ALLOWED_EXECUTION_TRANSITIONS): a
--   forbidden pair is 23514, an identity mutation is 42501, a delete is 42501.
-- - A simulated timeout / ambiguous result records EXECUTION_UNKNOWN and is NEVER
--   blindly retried; a human reconciliation resolves it (CONFIRMED_EXECUTED or
--   CONFIRMED_NOT_EXECUTED), and only CONFIRMED_NOT_EXECUTED re-opens a new
--   attempt.
-- - disbursement_execution_receipts are append-only; every attempt pins a UNIQUE
--   idempotency_key (a duplicate is 23505) so a duplicate delivery can never move
--   money twice, the fixed adapter label, and a receipt_ref present IFF the
--   attempt confirmed execution.
--
-- All customer data, policies, documents, and banking-system responses in this
-- project are synthetic and created solely for demonstration.

-- 1. Extend the human_gates gate-type registry.  The prior CHECK was last
--    re-declared in 202607180020_contract_packages.sql as the constraint
--    public.human_gates_gate_type_check; dropping and re-adding keeps the
--    additive, one-superset-of-the-other semantics: this list is a strict
--    superset of that one (nothing is removed) plus the two stage-11 gates.
alter table public.human_gates
  drop constraint human_gates_gate_type_check;

alter table public.human_gates
  add constraint human_gates_gate_type_check check (
    gate_type in (
      'G1_INTAKE_COMPLETE',
      'G2_GAP_REQUEST_APPROVAL',
      'G3_RISK_DISPOSITION',
      'G4_OPS_AUTHORIZATION',
      'HG_FINANCING_NEED_CONFIRMED',
      'HG_UNDERWRITING_ASSESSMENT_REVIEWED',
      'HG_LEGAL_ASSESSMENT_REVIEWED',
      'HG_MAKER_SUBMISSION_CONFIRMED',
      'HG_CREDIT_NOTIFICATION_APPROVED',
      'HG_DISBURSEMENT_CONDITIONS_CONFIRMED',
      'HG_SECURITY_PERFECTION_CONFIRMED',
      'HG_CONTRACT_PACKAGE_APPROVED',
      'HG_SIGNATURE_AUTHORITY_CONFIRMED',
      'HG_CONTRACTS_SIGNED',
      'HG_DISBURSEMENT_VALIDATED',
      'HG_DISBURSEMENT_AUTHORIZED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). Union superset of '
  'all prior registries plus the two stage-11 disbursement gates: '
  'HG_DISBURSEMENT_VALIDATED (maker-checker validation) then '
  'HG_DISBURSEMENT_AUTHORIZED (authority), which MUST be satisfied by different '
  'actors before the labelled mock execution adapter may run. Both human-'
  'satisfied only and NOT required_gate on any task-graph node -- coupling '
  'orchestration readiness to them is a deferred decision.';

-- 2. The proposed disbursement action, one per case version.  Amount is an EXACT
--    decimal stored as TEXT (no float on money); status is one of the CLOSED
--    synthetic execution-lifecycle values and defaults to PROPOSED.
create table public.proposed_disbursement_actions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  decision_id uuid not null,
  -- EXACT decimal money as text: a valid positive decimal literal (no float).
  amount_text text not null check (
    amount_text ~ '^[0-9]+(\.[0-9]+)?$' and (amount_text)::numeric > 0
  ),
  currency text not null check (length(btrim(currency)) between 1 and 8),
  -- SYNTHETIC references: nothing here is a real bank account / beneficiary.
  beneficiary_ref_vi text not null check (length(btrim(beneficiary_ref_vi)) > 0),
  account_ref_vi text not null check (length(btrim(account_ref_vi)) > 0),
  -- CLOSED PROPOSED synthetic execution-lifecycle taxonomy (design giai đoạn 11).
  status text not null default 'PROPOSED' check (
    status in (
      'PROPOSED',
      'EXECUTION_REQUESTED',
      'EXECUTION_UNKNOWN',
      'CONFIRMED_EXECUTED',
      'CONFIRMED_NOT_EXECUTED'
    )
  ),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  -- The action binds the EXACT source decision + case + version triple, so it can
  -- never drift onto a different case version than the decision that sourced it.
  constraint proposed_disbursement_actions_decision_fk
    foreign key (decision_id, case_id, case_version)
    references public.human_credit_decisions(id, case_id, case_version)
    on delete restrict,
  -- ONE proposed disbursement action per case version: a duplicate insert is
  -- rejected and the application layer resolves it to the existing action
  -- (idempotent record-or-get).  A revision bumps the case version.
  constraint proposed_disbursement_actions_case_version_key
    unique (case_id, case_version)
);

create index proposed_disbursement_actions_case_idx
  on public.proposed_disbursement_actions (case_id, case_version, created_at desc);

create index proposed_disbursement_actions_decision_idx
  on public.proposed_disbursement_actions (decision_id);

-- DETERMINISTIC transition enforcement.  On UPDATE: only status may change (else
-- 42501); the status pair must be an allowed edge in the SAME map as
-- domain/disbursements.py::ALLOWED_EXECUTION_TRANSITIONS (else 23514).  On
-- DELETE: forbidden (42501).  There is NO implicit edge and no self-transition.
create or replace function public.enforce_disbursement_execution_transition()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  allowed text[];
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'proposed disbursement actions cannot be deleted';
  end if;

  -- Only status may ever change; identity / money / binding is frozen.
  if row(
    new.id, new.case_id, new.case_version, new.decision_id, new.amount_text,
    new.currency, new.beneficiary_ref_vi, new.account_ref_vi, new.created_by,
    new.created_at
  ) is distinct from row(
    old.id, old.case_id, old.case_version, old.decision_id, old.amount_text,
    old.currency, old.beneficiary_ref_vi, old.account_ref_vi, old.created_by,
    old.created_at
  ) then
    raise exception using
      errcode = '42501',
      message = 'only the status of a proposed disbursement action may change';
  end if;

  allowed := case old.status
    when 'PROPOSED' then array['EXECUTION_REQUESTED']
    when 'EXECUTION_REQUESTED' then
      array['CONFIRMED_EXECUTED', 'EXECUTION_UNKNOWN', 'CONFIRMED_NOT_EXECUTED']
    when 'EXECUTION_UNKNOWN' then array['CONFIRMED_EXECUTED', 'CONFIRMED_NOT_EXECUTED']
    when 'CONFIRMED_EXECUTED' then array[]::text[]
    when 'CONFIRMED_NOT_EXECUTED' then array['EXECUTION_REQUESTED']
    else array[]::text[]
  end;

  if not (new.status = any(allowed)) then
    raise exception using
      errcode = '23514',
      message = format(
        'forbidden disbursement execution transition %s -> %s',
        old.status, new.status
      );
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_disbursement_execution_transition() from public;

create trigger proposed_disbursement_actions_enforce_transition
before update or delete on public.proposed_disbursement_actions
for each row execute function public.enforce_disbursement_execution_transition();

alter table public.proposed_disbursement_actions enable row level security;
alter table public.proposed_disbursement_actions force row level security;

create policy proposed_disbursement_actions_select_assigned
on public.proposed_disbursement_actions
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = proposed_disbursement_actions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.proposed_disbursement_actions from public, anon, authenticated;
grant select on public.proposed_disbursement_actions to authenticated;
grant all on public.proposed_disbursement_actions to service_role;

-- 3. The append-only labelled-mock execution receipts.  Every execution attempt
--    pins a UNIQUE idempotency_key (a duplicate is 23505) so a duplicate delivery
--    can never move money twice, the fixed adapter label, the adapter result
--    status, and a receipt_ref present IFF the attempt confirmed execution.  A
--    reconciliation of an unresolved execution is recorded on the append-only
--    audit trail (rationale + actor), not here.
create table public.disbursement_execution_receipts (
  id uuid primary key default gen_random_uuid(),
  action_id uuid not null
    references public.proposed_disbursement_actions(id) on delete restrict,
  -- UNIQUE across all attempts: the idempotency key makes a duplicate delivery a
  -- no-op instead of a second money movement.
  idempotency_key text not null check (length(btrim(idempotency_key)) > 0),
  -- The single labelled mock adapter: nothing runs against a real system.
  adapter_label text not null
    check (adapter_label = 'MOCK_DISBURSEMENT_EXECUTION_ADAPTER'),
  -- Only the adapter's two possible results are recorded here; a clean
  -- "not executed" comes from a HUMAN reconciliation, never the adapter.
  result_status text not null
    check (result_status in ('CONFIRMED_EXECUTED', 'EXECUTION_UNKNOWN')),
  -- A receipt reference is present IFF the attempt confirmed execution; a
  -- timeout / unknown carries none.
  receipt_ref text,
  recorded_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint disbursement_execution_receipts_idempotency_key
    unique (idempotency_key),
  constraint disbursement_execution_receipts_receipt_ref_matches_result check (
    (result_status = 'CONFIRMED_EXECUTED'
      and receipt_ref is not null and length(btrim(receipt_ref)) > 0)
    or (result_status = 'EXECUTION_UNKNOWN' and receipt_ref is null)
  )
);

create index disbursement_execution_receipts_action_idx
  on public.disbursement_execution_receipts (action_id, created_at desc);

create trigger disbursement_execution_receipts_are_append_only
before update or delete on public.disbursement_execution_receipts
for each row execute function public.reject_append_only_mutation();

alter table public.disbursement_execution_receipts enable row level security;
alter table public.disbursement_execution_receipts force row level security;

-- RLS joins through the parent action to the active case assignment (the receipts
-- table carries no case_id of its own).
create policy disbursement_execution_receipts_select_assigned
on public.disbursement_execution_receipts
for select to authenticated using (
  exists (
    select 1
    from public.proposed_disbursement_actions as action
    join public.case_assignments as assignment
      on assignment.case_id = action.case_id
    where action.id = disbursement_execution_receipts.action_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.disbursement_execution_receipts from public, anon, authenticated;
grant select on public.disbursement_execution_receipts to authenticated;
grant all on public.disbursement_execution_receipts to service_role;
