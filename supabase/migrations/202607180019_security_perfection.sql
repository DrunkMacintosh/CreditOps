-- Stage 9 (master design section 5 giai đoạn 9): the per-asset security-
-- perfection ledger and its human confirmation gate.
--
-- When applicable, the bank and the security provider notarise, authenticate and
-- REGISTER the security measure.  Registration is NOT a full freeze; it publicises
-- the secured right and supports third-party effectiveness / priority.  This
-- migration models that ledger; it never calls a real registration authority and
-- never declares a final priority ranking (design section 5 giai đoạn 9).
--
-- SHAPE (spec):
--   * ONE ``security_interests`` row PER ASSET -- never a single case-wide
--     boolean.  Each interest is append-only.
--   * EACH interest carries MANY ``security_perfection_items`` -- one per
--     perfection requirement, tracked individually (evidence, filing reference,
--     effective/expiry date, status).  An item is mutable ONLY through the closed
--     status-transition graph enforced below; it is never deleted.
--   * NO LLM valuation: ``valuation_reference`` is a nullable POINTER to an
--     external source/adapter, never a computed value.
--   * NO ``priority_rank`` computed field anywhere -- only free-text ``notes_vi``.
--
-- PROPOSED / ASSUMPTION: ``HG_SECURITY_PERFECTION_CONFIRMED`` is a SYNTHETIC gate
-- name with NO official SHB role mapping, exactly like the existing G1..G4 and
-- HG_ gates.  The closed ``asset_kind`` set and the ``status`` set are likewise
-- synthetic prototype taxonomies and MUST be reconfigured when an official source
-- is supplied.  An explicit no-collateral path (a case that legitimately needs no
-- security) is OUT OF SCOPE here and deliberately not modelled.
--
-- All customer data, assets, and authorities in this project are synthetic and
-- created solely for demonstration.

-- ---------------------------------------------------------------------------
-- Extend the closed human_gates gate-type registry with the stage-9 gate.
--
-- The prior CHECK was last re-declared in
-- 202607180016_specialist_review_gates.sql as public.human_gates_gate_type_check.
-- Dropping and re-adding it keeps the additive, one-superset-of-the-other
-- semantics: no existing gate type is removed, so every existing human_gates row
-- stays valid.
--
-- NOTE on concurrent siblings (resolved): migrations run in filename order, and
-- THIS file (…19) runs AFTER 202607180017 (HG_CREDIT_NOTIFICATION_APPROVED,
-- stage 7) and 202607180018 (HG_DISBURSEMENT_CONDITIONS_CONFIRMED, stage 10),
-- both of which drop and re-add THIS SAME constraint.  So the re-add below is the
-- true superset of the …16 set PLUS the …17 and …18 gate names PLUS this
-- migration's single new gate -- otherwise …19 would silently drop the …17/…18
-- additions.  A later migration (…20) in turn re-adds a superset that includes
-- HG_SECURITY_PERFECTION_CONFIRMED, so the chain stays consistent forward.
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
      'HG_SECURITY_PERFECTION_CONFIRMED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). '
  'HG_SECURITY_PERFECTION_CONFIRMED is the stage-9 human confirmation that every '
  'perfection requirement of every per-asset security interest is in a terminal-'
  'satisfied state with evidence. Like the other HG_ gates it is human-satisfied '
  'only and, for now, NOT required_gate on any task-graph node -- coupling '
  'downstream readiness to it is a deferred decision. Superset of the '
  '202607180016 set; see the migration header for the 17/18 ordering note.';

-- ---------------------------------------------------------------------------
-- One append-only security interest PER ASSET.
create table public.security_interests (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  -- The asset the interest is taken over (Vietnamese free text, required).
  asset_description_vi text not null
    check (length(btrim(asset_description_vi)) > 0),
  -- Closed PROPOSED synthetic asset taxonomy (see header): reconfigure when an
  -- official source is supplied.
  asset_kind text not null check (
    asset_kind in ('REAL_ESTATE', 'VEHICLE', 'DEPOSIT', 'RECEIVABLE', 'OTHER')
  ),
  owner_name_vi text check (owner_name_vi is null or length(btrim(owner_name_vi)) > 0),
  -- POINTER ONLY to an external valuation source/adapter (nullable). NO LLM
  -- valuation and NO computed monetary value is ever stored here.
  valuation_reference text
    check (valuation_reference is null or length(btrim(valuation_reference)) > 0),
  -- Free-text notes ONLY. There is deliberately NO priority_rank column: the
  -- system never declares a final priority ranking (spec).
  notes_vi text check (notes_vi is null or length(btrim(notes_vi)) > 0),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp()
);

create index security_interests_case_idx
  on public.security_interests (case_id, case_version, created_at desc);

-- Interests are append-only: an interest is never edited or deleted (a change is
-- a new interest / new case version), mirroring 202607180014.
create trigger security_interests_are_append_only
before update or delete on public.security_interests
for each row execute function public.reject_append_only_mutation();

alter table public.security_interests enable row level security;
alter table public.security_interests force row level security;

create policy security_interests_select_assigned
on public.security_interests
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = security_interests.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.security_interests from public, anon, authenticated;
grant select on public.security_interests to authenticated;
grant all on public.security_interests to service_role;

-- ---------------------------------------------------------------------------
-- MANY perfection requirements per interest, each tracked individually.
--
-- Status is a closed PROPOSED synthetic set advanced ONLY through the transition
-- graph enforced by the trigger below:
--   PENDING -> EVIDENCE_ATTACHED -> COMPLETED
--   PENDING -> NOT_REQUIRED_BY_HUMAN
--   COMPLETED -> EXPIRED
-- Items are never deleted; they only transition.
create table public.security_perfection_items (
  id uuid primary key default gen_random_uuid(),
  interest_id uuid not null
    references public.security_interests(id) on delete restrict,
  requirement_vi text not null check (length(btrim(requirement_vi)) > 0),
  status text not null default 'PENDING' check (
    status in (
      'PENDING',
      'EVIDENCE_ATTACHED',
      'COMPLETED',
      'NOT_REQUIRED_BY_HUMAN',
      'EXPIRED'
    )
  ),
  -- Array of opaque evidence pointers (storage keys / document ids); never a
  -- document body. Shape enforced here; the >=1-evidence-for-COMPLETED coupling
  -- is enforced by the CHECK below and re-checked in the domain/adapter.
  evidence_refs jsonb not null default '[]'::jsonb
    check (jsonb_typeof(evidence_refs) = 'array'),
  filing_reference text
    check (filing_reference is null or length(btrim(filing_reference)) > 0),
  effective_date date,
  expiry_date date,
  completed_by uuid,
  completed_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  -- A COMPLETED item MUST carry evidence and record who/when completed it. Other
  -- statuses (including EXPIRED, which retains its prior completion metadata) are
  -- unconstrained here. Defense in depth: the adapter enforces the same rule.
  constraint security_perfection_items_completed_has_evidence check (
    status <> 'COMPLETED'
    or (
      jsonb_array_length(evidence_refs) >= 1
      and completed_by is not null
      and completed_at is not null
    )
  )
);

create index security_perfection_items_interest_idx
  on public.security_perfection_items (interest_id, created_at);

-- Guard the closed status-transition graph. A status change outside the graph is
-- rejected with a check_violation (23514); non-status column updates (e.g.
-- attaching evidence while advancing status) pass through.
create or replace function public.enforce_security_perfection_item_transition()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if new.status = old.status then
    return new;
  end if;
  if not (
    (old.status = 'PENDING' and new.status = 'EVIDENCE_ATTACHED')
    or (old.status = 'EVIDENCE_ATTACHED' and new.status = 'COMPLETED')
    or (old.status = 'PENDING' and new.status = 'NOT_REQUIRED_BY_HUMAN')
    or (old.status = 'COMPLETED' and new.status = 'EXPIRED')
  ) then
    raise exception using
      errcode = '23514',
      message = 'forbidden security perfection item transition '
        || old.status || ' -> ' || new.status;
  end if;
  return new;
end;
$$;

revoke all on function public.enforce_security_perfection_item_transition()
  from public;

create trigger security_perfection_items_transition_guard
before update on public.security_perfection_items
for each row execute function public.enforce_security_perfection_item_transition();

-- Items are never deleted (they EXPIRE, they do not disappear).
create trigger security_perfection_items_no_delete
before delete on public.security_perfection_items
for each row execute function public.reject_append_only_mutation();

alter table public.security_perfection_items enable row level security;
alter table public.security_perfection_items force row level security;

create policy security_perfection_items_select_assigned
on public.security_perfection_items
for select to authenticated using (
  exists (
    select 1
    from public.security_interests as interest
    join public.case_assignments as assignment
      on assignment.case_id = interest.case_id
    where interest.id = security_perfection_items.interest_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.security_perfection_items from public, anon, authenticated;
grant select on public.security_perfection_items to authenticated;
grant all on public.security_perfection_items to service_role;
