-- Stage 14 (master design section 5 giai đoạn 14 "Tất toán hoặc xử lý nợ"):
-- the two mutually exclusive post-repayment branches and their human gates.
--
--   * 14A SETTLEMENT -- public.settlement_checks records a deterministic ledger
--     check (outstanding principal/interest/fees + open-exception count) with a
--     computed zero_balance_confirmed flag; public.settlement_receipts holds the
--     LABELLED MOCK closure / release receipts a confirmed settlement produces.
--     The human gate is HG_SETTLEMENT_CONFIRMED.
--   * 14B RECOVERY -- public.recovery_cases records a recovery case opened ONLY
--     from a deterministic trigger PLUS an explicit human escalation (escalator +
--     rationale), carrying an evidence pack of REFERENCES and structured options.
--     The single in-scope human gate is HG_RECOVERY_STRATEGY_APPROVED.
--
-- PROPOSED / ASSUMPTION: 'HG_SETTLEMENT_CONFIRMED' and
-- 'HG_RECOVERY_STRATEGY_APPROVED' are SYNTHETIC gate names.  They carry NO
-- official SHB role mapping, control code, or authority matrix and are presented
-- only as demonstration application controls, exactly like the existing G1..G4
-- and the other HG_ gates.
--
-- GATE-REGISTRY SEAM (concurrent stages).  The CHECK re-declared below is the
-- UNION of the registry last declared in 202607180021_proposed_disbursements.sql
-- (the 16-name superset: the 202607180020 registry plus the two stage-11
-- disbursement gates HG_DISBURSEMENT_VALIDATED / HG_DISBURSEMENT_AUTHORIZED) PLUS
-- the two new stage-14 gates.  Stages 11/12/13 were concurrent at authoring time:
-- 202607180021 (stage 11) is the latest migration that re-declares this
-- constraint; 202607180023 (stage 13 repayment ledger) does NOT touch it, and
-- there is no 202607180022.  This drop/re-add therefore stays a strict SUPERSET
-- of every prior registry -- no existing gate type is removed.  If a later
-- concurrent migration adds further gate names, reconcile THIS list to remain a
-- superset (add their names here) so a plain drop/re-add never drops one.
--
-- OUT OF SCOPE: real closure, registry release, enforcement, litigation and
-- write-off.  settlement_receipts are LABELLED MOCK (like the stage-7
-- communication_receipts MOCK_CHANNEL); recovery has no enforcement state.
--
-- All customer data, policies, documents, and banking-system responses in this
-- project are synthetic and created solely for demonstration.

-- 1. Extend the human_gates gate-type registry.  Strict SUPERSET of the
--    202607180021 registry (all 16 prior names retained) plus the two new
--    stage-14 gates; no existing gate type is removed.
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
      'HG_DISBURSEMENT_AUTHORIZED',
      'HG_SETTLEMENT_CONFIRMED',
      'HG_RECOVERY_STRATEGY_APPROVED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). Union superset of '
  'the 202607180021 registry plus the two stage-14 gates: HG_SETTLEMENT_CONFIRMED '
  '(independent OPS checker confirms a zero-balance settlement) and '
  'HG_RECOVERY_STRATEGY_APPROVED (a different human authority approves a recovery '
  'strategy). Both human-satisfied only and NOT required_gate on any task-graph '
  'node -- coupling orchestration readiness to them is a deferred decision. If '
  'concurrent stage-11/12/13 migrations add gates, reconcile this to a superset.';

-- 2. The settlement ledger check (14A).  Append-only.  Recorded only when the
--    deterministic eligibility derivation is True, so a persisted row always has
--    zero_balance_confirmed = true.  The three outstanding totals are text
--    decimal strings CANONICALIZED in the domain so a decimal-zero amount is the
--    single token '0'; that keeps the zero_balance_confirmed text CHECK below
--    SOUND (it never spuriously fails on '0.00').
create table public.settlement_checks (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  outstanding_principal text not null check (length(btrim(outstanding_principal)) > 0),
  outstanding_interest text not null check (length(btrim(outstanding_interest)) > 0),
  outstanding_fees text not null check (length(btrim(outstanding_fees)) > 0),
  open_exception_count integer not null check (open_exception_count >= 0),
  -- Computed in the domain (Decimal comparison) and mirrored here.  Sound
  -- because a decimal-zero amount is stored canonically as exactly '0'.
  zero_balance_confirmed boolean not null,
  recorded_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint settlement_checks_zero_balance_consistent check (
    zero_balance_confirmed = (
      outstanding_principal = '0'
      and outstanding_interest = '0'
      and outstanding_fees = '0'
    )
  )
);

create index settlement_checks_case_idx
  on public.settlement_checks (case_id, case_version, created_at desc);

create trigger settlement_checks_are_append_only
before update or delete on public.settlement_checks
for each row execute function public.reject_append_only_mutation();

alter table public.settlement_checks enable row level security;
alter table public.settlement_checks force row level security;

create policy settlement_checks_select_assigned
on public.settlement_checks
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = settlement_checks.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.settlement_checks from public, anon, authenticated;
grant select on public.settlement_checks to authenticated;
grant all on public.settlement_checks to service_role;

-- 3. The LABELLED MOCK settlement receipts (14A).  Append-only, at most one of
--    each kind per check (a confirmed settlement produces one MOCK_CLOSURE and
--    one MOCK_RELEASE).  Nothing here performs a real closure or registry
--    release -- kind is the closed {MOCK_CLOSURE, MOCK_RELEASE} set.
create table public.settlement_receipts (
  id uuid primary key default gen_random_uuid(),
  settlement_check_id uuid not null
    references public.settlement_checks(id) on delete restrict,
  kind text not null check (kind in ('MOCK_CLOSURE', 'MOCK_RELEASE')),
  note_vi text check (note_vi is null or length(btrim(note_vi)) > 0),
  recorded_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  -- One receipt of each kind per check (both kinds coexist; each once).
  constraint settlement_receipts_check_kind_key unique (settlement_check_id, kind)
);

create index settlement_receipts_check_idx
  on public.settlement_receipts (settlement_check_id, created_at desc);

create trigger settlement_receipts_are_append_only
before update or delete on public.settlement_receipts
for each row execute function public.reject_append_only_mutation();

alter table public.settlement_receipts enable row level security;
alter table public.settlement_receipts force row level security;

-- RLS joins through the parent check to the active case assignment (the receipt
-- carries no case_id of its own).
create policy settlement_receipts_select_assigned
on public.settlement_receipts
for select to authenticated using (
  exists (
    select 1
    from public.settlement_checks as check_row
    join public.case_assignments as assignment
      on assignment.case_id = check_row.case_id
    where check_row.id = settlement_receipts.settlement_check_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.settlement_receipts from public, anon, authenticated;
grant select on public.settlement_receipts to authenticated;
grant all on public.settlement_receipts to service_role;

-- 4. The recovery case (14B).  Opened only from a deterministic trigger PLUS an
--    explicit human escalation (escalated_by + escalation_rationale_vi).  Status
--    starts PREPARING; the ONLY mutation allowed is the single status change
--    PREPARING -> STRATEGY_APPROVED (setting approved_by), enforced by the
--    trigger below.  evidence_refs is a non-empty JSON array of REFERENCES;
--    options is a non-empty JSON array of structured option objects.
create table public.recovery_cases (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  trigger_summary_vi text not null check (length(btrim(trigger_summary_vi)) > 0),
  escalated_by uuid not null,
  escalation_rationale_vi text not null
    check (length(btrim(escalation_rationale_vi)) > 0),
  status text not null default 'PREPARING'
    check (status in ('PREPARING', 'STRATEGY_APPROVED')),
  evidence_refs jsonb not null
    check (jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1),
  options jsonb not null
    check (jsonb_typeof(options) = 'array' and jsonb_array_length(options) >= 1),
  -- Set only by the single PREPARING -> STRATEGY_APPROVED update; must differ
  -- from the escalator (separation of duty, also enforced at the API).
  approved_by uuid,
  strategy_approved_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  constraint recovery_cases_approved_shape check (
    (status = 'PREPARING' and approved_by is null and strategy_approved_at is null)
    or (status = 'STRATEGY_APPROVED' and approved_by is not null
        and strategy_approved_at is not null)
  ),
  constraint recovery_cases_approver_differs check (
    approved_by is null or approved_by <> escalated_by
  )
);

create index recovery_cases_case_idx
  on public.recovery_cases (case_id, case_version, created_at desc);

-- The ONLY allowed mutation is PREPARING -> STRATEGY_APPROVED (approving the
-- recovery strategy).  Everything else is frozen; deletes are forbidden.  This
-- is defence in depth over the application layer.
create or replace function public.enforce_recovery_case_transition()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'recovery cases cannot be deleted';
  end if;

  -- Only status, approved_by and strategy_approved_at may ever change; every
  -- identity / evidence / escalation column is frozen after creation.
  if row(
    new.id, new.case_id, new.case_version, new.trigger_summary_vi,
    new.escalated_by, new.escalation_rationale_vi, new.evidence_refs,
    new.options, new.created_at
  ) is distinct from row(
    old.id, old.case_id, old.case_version, old.trigger_summary_vi,
    old.escalated_by, old.escalation_rationale_vi, old.evidence_refs,
    old.options, old.created_at
  ) then
    raise exception using
      errcode = '42501',
      message = 'only status/approved_by/strategy_approved_at of a recovery case may change';
  end if;

  -- The single allowed status edge is PREPARING -> STRATEGY_APPROVED.
  if not (old.status = 'PREPARING' and new.status = 'STRATEGY_APPROVED') then
    raise exception using
      errcode = '23514',
      message = format(
        'forbidden recovery case transition %s -> %s', old.status, new.status
      );
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_recovery_case_transition() from public;

create trigger recovery_cases_enforce_transition
before update or delete on public.recovery_cases
for each row execute function public.enforce_recovery_case_transition();

alter table public.recovery_cases enable row level security;
alter table public.recovery_cases force row level security;

create policy recovery_cases_select_assigned
on public.recovery_cases
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = recovery_cases.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.recovery_cases from public, anon, authenticated;
grant select on public.recovery_cases to authenticated;
grant all on public.recovery_cases to service_role;
