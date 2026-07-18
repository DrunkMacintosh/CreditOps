-- PROPOSED Stage 1 store: prospect, prospect screening snapshot, and the
-- human-recorded contact decision (master design section 5 stage 1, section 13).
--
-- Stage 1 is human-owned end to end: the RM / intake officer owns the prospect
-- and the contact decision.  The system never auto-contacts and never scores
-- secretly.  Screening is DESCRIPTIVE only -- every field is a synthetic,
-- labelled configuration observation (``screening_config_version``); NO
-- pass/fail verdict is computed by SQL or by any code path.  There is
-- intentionally no ``score`` / ``verdict`` / ``approved`` / ``rating`` column
-- anywhere here: fail closed -- the verdict, if any, is a human's alone and is
-- recorded only as the free-text ``prospect_contact_decisions.rationale_vi``.
--
-- RLS approach for pre-case data (schema decision):
--   A prospect exists BEFORE any credit case, so there is no
--   ``public.credit_cases`` row and no ``public.case_assignments`` to scope by
--   (the pattern every case-scoped table uses).  There is no tenant column
--   anywhere in this schema, so the isolation boundary for a prospect is its
--   creator: a prospect is OWNED by the intake officer who created it.  Reads
--   are therefore restricted to self-owned rows via ``created_by =
--   auth.uid()`` (mirroring ``credit_cases``'s ``created_by`` insert scope in
--   202607170009), and the child tables inherit ownership through their parent
--   prospect.  All writes remain service-role only; the API additionally
--   scopes EVERY read by ``created_by`` so the service-role write path can
--   never surface another officer's prospect (API-level authorization on top
--   of RLS).
--
-- Append-only-with-supersession: none of these three tables is ever UPDATEd or
-- DELETEd.  A prospect's descriptive picture evolves by appending a NEW
-- ``prospect_screening_snapshots`` version (supersession), never by mutating
-- the prospect row; contact decisions accrete as an immutable, human-authored
-- record.
--
-- All data is synthetic and created solely for demonstration.

-- The prospect: a stable, immutable identity created by an intake officer.
-- Descriptive seed fields are optional; richer screening lives in versioned
-- snapshots.  The row is append-only -- corrections arrive as new snapshots.
create table public.prospects (
  id uuid primary key default gen_random_uuid(),
  name_vi text not null check (length(btrim(name_vi)) > 0),
  industry_vi text,
  years_operating integer check (years_operating is null or years_operating >= 0),
  revenue_band_vi text,
  legal_status_vi text,
  notes_vi text,
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp()
);

create index prospects_owner_created_idx
  on public.prospects (created_by, created_at desc, id);

create trigger prospects_are_append_only
before update or delete on public.prospects
for each row execute function public.reject_append_only_mutation();

alter table public.prospects enable row level security;
alter table public.prospects force row level security;

create policy prospects_select_own
on public.prospects
for select to authenticated using (
  created_by = (select auth.uid())
);

revoke all on public.prospects from public, anon, authenticated;
grant select on public.prospects to authenticated;
grant all on public.prospects to service_role;

-- A descriptive screening snapshot.  ``version`` is per-prospect and starts at
-- 1; a duplicate version is a conflict (23505).  ``screening_config_version``
-- is the labelled synthetic config the observation was made under.  ``details``
-- is a schema-versioned object of additional descriptive observations -- it
-- must NOT carry a verdict; that is enforced in the domain model.  Append-only.
create table public.prospect_screening_snapshots (
  id uuid primary key default gen_random_uuid(),
  prospect_id uuid not null references public.prospects(id) on delete restrict,
  version integer not null check (version >= 1),
  screening_config_version text not null
    check (length(btrim(screening_config_version)) > 0),
  industry_vi text,
  years_operating integer check (years_operating is null or years_operating >= 0),
  revenue_band_vi text,
  legal_status_vi text,
  credit_history_vi text,
  risk_appetite_note_vi text,
  details jsonb not null default '{}'::jsonb
    check (jsonb_typeof(details) = 'object'),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint prospect_screening_snapshots_version_key
    unique (prospect_id, version)
);

create index prospect_screening_snapshots_prospect_idx
  on public.prospect_screening_snapshots (prospect_id, version desc);

create trigger prospect_screening_snapshots_are_append_only
before update or delete on public.prospect_screening_snapshots
for each row execute function public.reject_append_only_mutation();

alter table public.prospect_screening_snapshots enable row level security;
alter table public.prospect_screening_snapshots force row level security;

create policy prospect_screening_snapshots_select_own
on public.prospect_screening_snapshots
for select to authenticated using (
  exists (
    select 1 from public.prospects as p
    where p.id = prospect_screening_snapshots.prospect_id
      and p.created_by = (select auth.uid())
  )
);

revoke all on public.prospect_screening_snapshots
  from public, anon, authenticated;
grant select on public.prospect_screening_snapshots to authenticated;
grant all on public.prospect_screening_snapshots to service_role;

-- The contact decision: a durable record made BY the human RM/intake officer.
-- ``decision`` is a closed set; an unknown value is a check violation (23514).
-- ``rationale_vi`` is required (the human's reasoning), never machine-derived.
-- Recording a decision has NO side effect: it never triggers any contact.
-- Append-only.
create table public.prospect_contact_decisions (
  id uuid primary key default gen_random_uuid(),
  prospect_id uuid not null references public.prospects(id) on delete restrict,
  decision text not null check (
    decision in ('CONTACT', 'DO_NOT_CONTACT', 'DEFER')
  ),
  rationale_vi text not null check (length(btrim(rationale_vi)) > 0),
  decided_by uuid not null,
  created_at timestamptz not null default clock_timestamp()
);

create index prospect_contact_decisions_prospect_idx
  on public.prospect_contact_decisions (prospect_id, created_at desc);

create trigger prospect_contact_decisions_are_append_only
before update or delete on public.prospect_contact_decisions
for each row execute function public.reject_append_only_mutation();

alter table public.prospect_contact_decisions enable row level security;
alter table public.prospect_contact_decisions force row level security;

create policy prospect_contact_decisions_select_own
on public.prospect_contact_decisions
for select to authenticated using (
  exists (
    select 1 from public.prospects as p
    where p.id = prospect_contact_decisions.prospect_id
      and p.created_by = (select auth.uid())
  )
);

revoke all on public.prospect_contact_decisions
  from public, anon, authenticated;
grant select on public.prospect_contact_decisions to authenticated;
grant all on public.prospect_contact_decisions to service_role;
