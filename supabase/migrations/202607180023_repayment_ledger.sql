-- Stage 13 (master design section 5 giai đoạn 13 "Thu nợ gốc, lãi và phí"):
-- the deterministic RepaymentLedger's DURABLE state -- disbursed facilities and
-- their append-only repayment-event history.  The ledger STATE (outstanding
-- principal / interest / fees, per-period status, collections exceptions) is
-- NEVER stored: it is recomputed on demand as a PURE FOLD over the ordered event
-- history plus the exact-decimal schedule (see
-- services/api/src/creditops/domain/repayments.py::apply_events).  This migration
-- therefore stores only the two append-only source-of-truth tables:
--
--   * public.facilities -- one row per disbursed facility, bound to its SOURCE
--     permitting human credit decision (composite FK to the exact decision +
--     case + version triple, exactly like disbursement_conditions).  The
--     amortisation / balloon schedule is DERIVED from (principal, rate, term,
--     style) via the shared deterministic calculator; nothing about the schedule
--     is stored here.  Fully append-only: restructuring is OUT OF SCOPE and never
--     mutates a facility in place -- it returns to stages 4-6 and (later) writes a
--     new facility / case version.
--   * public.repayment_events -- the append-only payment / reversal history.
--     Every event is idempotent on (facility_id, external_reference): a duplicate
--     external delivery is a unique violation (23505) the adapter turns into a
--     return of the existing row, never a second economic effect.
--
-- AMOUNT SIGN CONVENTION (documented, single choice): amount is ALWAYS a positive
-- Decimal-as-text (check amount::numeric > 0).  The economic SIGN comes from
-- `kind`, NOT the stored number: a PAYMENT adds +amount, a REVERSAL removes
-- -amount.  A REVERSAL never mutates the payment it undoes; it is an independent
-- append-only row REFERENCING the original via reversed_event_id.
--
-- COLLECTIONS ARE READ-ONLY + PROPOSED-ONLY: public.collection_notes captures a
-- collections officer's FREE-TEXT observation or PROPOSED action (tighten
-- cash-flow control, freeze the undrawn limit, demand further security, ...).
-- These are proposals awaiting HUMAN AUTHORITY only; NO execution, no cash-flow
-- control, no limit freeze, no security demand is performed anywhere in this
-- system.  Restructuring is likewise OUT OF SCOPE (a note only; the real path is
-- stages 4-6 -> risk review -> human decision).
--
-- PROPOSED / SYNTHETIC: the allocation policy (fees -> interest -> principal,
-- oldest installment first), the collections-exception taxonomy, and the
-- collections role mapping are a prototype configuration with NO official SHB
-- control code, allocation rule, or authority matrix.  Reconfigure when an
-- official source exists.
--
-- All customer data, policies, documents, and banking-system responses in this
-- project are synthetic and created solely for demonstration.

-- 1. Disbursed facilities.  Every facility binds its SOURCE permitting credit
--    decision (composite FK to the exact decision + case + version triple), so a
--    facility can never drift onto a different case version than the decision
--    that authorised it (mirrors disbursement_conditions_decision_fk).  All money
--    figures are exact-decimal-as-text; term is a whole positive number of
--    months; the schedule is DERIVED, never stored.
create table public.facilities (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  decision_id uuid not null,
  -- Exact-decimal-as-text money inputs (house convention, e.g. requested_amount).
  principal text not null check (principal::numeric > 0),
  annual_rate_percent text not null check (annual_rate_percent::numeric >= 0),
  term_months integer not null check (term_months > 0),
  -- PROPOSED synthetic flat servicing fee charged each installment period; the
  -- fees bucket that the fees -> interest -> principal allocation fills first.
  periodic_fee text not null default '0' check (periodic_fee::numeric >= 0),
  repayment_style text not null check (
    repayment_style in ('EQUAL_PRINCIPAL', 'BALLOON')
  ),
  -- The anchor for deterministic due dates: period i is due
  -- first_payment_date + (i - 1) months.  Overdue is derived by comparing these
  -- to an explicit as-of observation date, never stored.
  first_payment_date date not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint facilities_decision_fk
    foreign key (decision_id, case_id, case_version)
    references public.human_credit_decisions(id, case_id, case_version)
    on delete restrict
);

create index facilities_case_idx
  on public.facilities (case_id, case_version, created_at desc);

create index facilities_decision_idx
  on public.facilities (decision_id);

-- Facilities are fully append-only: no in-place update, no delete.  A change of
-- terms is a NEW facility (restructuring returns to stages 4-6), never a mutation.
create trigger facilities_are_append_only
before update or delete on public.facilities
for each row execute function public.reject_append_only_mutation();

alter table public.facilities enable row level security;
alter table public.facilities force row level security;

create policy facilities_select_assigned
on public.facilities
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = facilities.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.facilities from public, anon, authenticated;
grant select on public.facilities to authenticated;
grant all on public.facilities to service_role;

-- 2. The append-only repayment-event history.  Payments and reversals only; the
--    ledger derives all state from these.  Idempotent on
--    (facility_id, external_reference): a duplicate delivery is a 23505 the
--    adapter maps to a return of the existing row.
create table public.repayment_events (
  id uuid primary key default gen_random_uuid(),
  facility_id uuid not null
    references public.facilities(id) on delete restrict,
  -- CLOSED PROPOSED synthetic kind set.  A REVERSAL is the ONLY negative-effect
  -- event; there is no negative amount and no mutation of the reversed row.
  kind text not null check (kind in ('PAYMENT', 'REVERSAL')),
  -- Exact-decimal-as-text, ALWAYS POSITIVE; the effect sign comes from `kind`.
  amount text not null check (amount::numeric > 0),
  external_reference text not null check (length(btrim(external_reference)) > 0),
  -- A REVERSAL REFERENCES the original event it undoes; a PAYMENT never does.
  reversed_event_id uuid references public.repayment_events(id) on delete restrict,
  effective_date date not null,
  recorded_at timestamptz not null default clock_timestamp(),
  -- Exactly the REVERSAL rows carry a reference; PAYMENT rows never do.
  constraint repayment_events_reversal_reference check (
    (kind = 'REVERSAL') = (reversed_event_id is not null)
  ),
  -- Idempotency: one economic event per external reference per facility.
  constraint repayment_events_external_reference_key
    unique (facility_id, external_reference)
);

create index repayment_events_facility_order_idx
  on public.repayment_events (facility_id, effective_date, recorded_at, id);

create index repayment_events_reversed_idx
  on public.repayment_events (reversed_event_id)
  where reversed_event_id is not null;

create trigger repayment_events_are_append_only
before update or delete on public.repayment_events
for each row execute function public.reject_append_only_mutation();

alter table public.repayment_events enable row level security;
alter table public.repayment_events force row level security;

-- RLS joins through the parent facility to the active case assignment (the event
-- row carries no case_id of its own).
create policy repayment_events_select_assigned
on public.repayment_events
for select to authenticated using (
  exists (
    select 1
    from public.facilities as facility
    join public.case_assignments as assignment
      on assignment.case_id = facility.case_id
    where facility.id = repayment_events.facility_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.repayment_events from public, anon, authenticated;
grant select on public.repayment_events to authenticated;
grant all on public.repayment_events to service_role;

-- 3. Collections notes: FREE-TEXT observations and PROPOSED actions ONLY.  A
--    collections officer records what they observe and what they PROPOSE (tighten
--    cash-flow control, freeze the undrawn limit, demand security, restructure).
--    Every row is a proposal awaiting HUMAN AUTHORITY later; NOTHING here is
--    executed.  Append-only.
create table public.collection_notes (
  id uuid primary key default gen_random_uuid(),
  facility_id uuid not null
    references public.facilities(id) on delete restrict,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  -- PROPOSED synthetic note kinds: a plain observation, or a proposed action
  -- that requires human authority and separate execution (NOT performed here).
  note_kind text not null check (note_kind in ('OBSERVATION', 'PROPOSED_ACTION')),
  note_text_vi text not null check (length(btrim(note_text_vi)) > 0),
  -- Free-text proposed action label (e.g. 'TIGHTEN_CASHFLOW_CONTROL',
  -- 'FREEZE_UNDRAWN_LIMIT', 'DEMAND_SECURITY', 'RESTRUCTURE_TO_STAGE_4').  Free
  -- text on purpose: it is a PROPOSAL, never an executable command.
  proposed_action_vi text
    check (proposed_action_vi is null or length(btrim(proposed_action_vi)) > 0),
  -- A PROPOSED_ACTION must name the action it proposes; an OBSERVATION must not.
  constraint collection_notes_action_requires_label check (
    (note_kind = 'PROPOSED_ACTION') = (proposed_action_vi is not null)
  ),
  author_id uuid not null,
  author_role text not null check (length(btrim(author_role)) > 0),
  created_at timestamptz not null default clock_timestamp()
);

create index collection_notes_facility_idx
  on public.collection_notes (facility_id, created_at desc);

create index collection_notes_case_idx
  on public.collection_notes (case_id, case_version, created_at desc);

create trigger collection_notes_are_append_only
before update or delete on public.collection_notes
for each row execute function public.reject_append_only_mutation();

alter table public.collection_notes enable row level security;
alter table public.collection_notes force row level security;

create policy collection_notes_select_assigned
on public.collection_notes
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = collection_notes.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.collection_notes from public, anon, authenticated;
grant select on public.collection_notes to authenticated;
grant all on public.collection_notes to service_role;
