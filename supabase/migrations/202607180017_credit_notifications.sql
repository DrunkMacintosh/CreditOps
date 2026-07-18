-- Stage 7 (master design section 5 giai đoạn 7, section 6.1 row 7): the credit
-- notification draft + its LABELLED MOCK delivery receipt, plus the
-- HG_CREDIT_NOTIFICATION_APPROVED human gate.
--
-- PROPOSED / ASSUMPTION: 'HG_CREDIT_NOTIFICATION_APPROVED' is a SYNTHETIC gate
-- name.  It has NO official SHB role mapping, approval delegation, or control
-- code and is presented only as a demonstration application control, exactly
-- like the existing G1..G4 and HG_ synthetic gates
-- (202607180001_orchestration_graph_gates.sql,
-- 202607180016_specialist_review_gates.sql).  Additive only: the new gate is a
-- superset of the prior CHECK set, so every existing human_gates row stays valid.
--
-- SPEC CONTRACT encoded here (master design section 5 giai đoạn 7):
--
-- - A credit_notification_drafts row is derived ONLY from a recorded
--   human_credit_decisions row whose decision permits a notification (the
--   application layer enforces the APPROVED_* precondition; the composite FK here
--   binds the exact (decision, case, version) triple so a draft can never drift
--   onto a different case version than its decision).  The content is a
--   deterministic template render; no agent sends anything.
-- - Both tables are append-only (immutable trigger) and one draft exists per case
--   version -- a revision bumps the case version, never edits a draft.
-- - Delivery is a LABELLED MOCK: a communication_receipts row records the delivery
--   through the single synthetic channel 'MOCK_CHANNEL' and pins the EXACT content
--   sha256.  A receipt whose content_hash differs from its draft's is rejected by
--   a trigger -- the receipt can never claim to have delivered drifted content.
--
-- All customer data, policies, documents, and banking-system responses in this
-- project are synthetic and created solely for demonstration.

-- 1. Extend the human_gates gate-type registry.  The prior CHECK was last
--    re-declared in 202607180016_specialist_review_gates.sql as the constraint
--    public.human_gates_gate_type_check; dropping and re-adding keeps the
--    additive, one-superset-of-the-other semantics: no existing gate type is
--    removed.
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
      'HG_CREDIT_NOTIFICATION_APPROVED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). '
  'HG_CREDIT_NOTIFICATION_APPROVED is the stage-7 credit-notification approval '
  'gate; it is human-satisfied only and gates the (mock) notification delivery, '
  'never an existing task-graph node.';

-- 2. The deterministic credit notification draft, one per case version.
create table public.credit_notification_drafts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  -- The exact human credit decision this draft derives from.
  decision_id uuid not null,
  -- The deterministic Vietnamese notification body (template render, no LLM).
  -- The application layer guarantees it embeds the synthetic-data notice and the
  -- fixed 'Thông báo tín dụng không phải xác nhận giải ngân.' disclaimer.
  content_vi text not null check (length(btrim(content_vi)) > 0),
  -- Canonical sha256 (hex) of content_vi, computed in the domain.
  content_hash char(64) not null check (content_hash ~ '^[0-9a-f]{64}$'),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  -- Composite FK: the draft binds the exact (decision, case, version) triple, so
  -- it can never reference a decision from a different case version.
  constraint credit_notification_drafts_decision_fk
    foreign key (decision_id, case_id, case_version)
    references public.human_credit_decisions(id, case_id, case_version)
    on delete restrict,
  -- ONE draft per case version: a duplicate insert for the same (case, version)
  -- is rejected, and the application layer resolves it to the existing draft
  -- (idempotent record-or-get).  A revision bumps the case version.
  constraint credit_notification_drafts_case_version_key
    unique (case_id, case_version)
);

create index credit_notification_drafts_case_idx
  on public.credit_notification_drafts (case_id, case_version, created_at desc);

create trigger credit_notification_drafts_are_append_only
before update or delete on public.credit_notification_drafts
for each row execute function public.reject_append_only_mutation();

alter table public.credit_notification_drafts enable row level security;
alter table public.credit_notification_drafts force row level security;

create policy credit_notification_drafts_select_assigned
on public.credit_notification_drafts
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = credit_notification_drafts.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.credit_notification_drafts from public, anon, authenticated;
grant select on public.credit_notification_drafts to authenticated;
grant all on public.credit_notification_drafts to service_role;

-- 3. Reject a communication receipt whose content_hash differs from its draft's.
--    A BEFORE INSERT trigger comparing to the draft is the simplest sound
--    mechanism: a CHECK cannot reference another row, and a composite FK would
--    require materialising the hash into both tables' keys.  An unknown draft is
--    left to the foreign key below (which fires after this BEFORE trigger).
create or replace function public.reject_receipt_hash_mismatch()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  draft_hash char(64);
begin
  select content_hash into draft_hash
  from public.credit_notification_drafts
  where id = new.draft_id;
  if draft_hash is not null and new.content_hash <> draft_hash then
    raise exception using
      errcode = '23514',
      message = 'communication receipt content_hash must equal the draft content_hash';
  end if;
  return new;
end;
$$;

revoke all on function public.reject_receipt_hash_mismatch() from public;

-- 4. The LABELLED MOCK delivery receipt, 1:1 with its draft.
create table public.communication_receipts (
  id uuid primary key default gen_random_uuid(),
  draft_id uuid not null
    references public.credit_notification_drafts(id) on delete restrict,
  -- The single synthetic delivery channel: nothing is ever sent.
  delivered_via text not null check (delivered_via = 'MOCK_CHANNEL'),
  -- Must equal the draft's content_hash (enforced by the trigger above).
  content_hash char(64) not null check (content_hash ~ '^[0-9a-f]{64}$'),
  receipt_note_vi text
    check (receipt_note_vi is null or length(btrim(receipt_note_vi)) > 0),
  recorded_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  -- 1:1 with the draft: one mock delivery per draft.
  constraint communication_receipts_draft_key unique (draft_id)
);

create index communication_receipts_draft_idx
  on public.communication_receipts (draft_id, created_at desc);

create trigger communication_receipts_reject_hash_mismatch
before insert on public.communication_receipts
for each row execute function public.reject_receipt_hash_mismatch();

create trigger communication_receipts_are_append_only
before update or delete on public.communication_receipts
for each row execute function public.reject_append_only_mutation();

alter table public.communication_receipts enable row level security;
alter table public.communication_receipts force row level security;

create policy communication_receipts_select_assigned
on public.communication_receipts
for select to authenticated using (
  exists (
    select 1
    from public.credit_notification_drafts as draft
    join public.case_assignments as assignment
      on assignment.case_id = draft.case_id
    where draft.id = communication_receipts.draft_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.communication_receipts from public, anon, authenticated;
grant select on public.communication_receipts to authenticated;
grant all on public.communication_receipts to service_role;
