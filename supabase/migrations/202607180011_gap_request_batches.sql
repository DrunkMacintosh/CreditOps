-- PROPOSED pre-Risk Evidence-Gap request workflow (the G2 gate,
-- HG_OUTBOUND_REQUEST_APPROVED capability; master design section 9).  This
-- REPLACES the removed credit-ops package-derived G2 path: G2 is no longer
-- derived from public.credit_ops_packages / document_request_approvals, and
-- the application-layer _maybe_satisfy_g2 writer is gone.  Breaking that link
-- is what resolves the Risk-waits-on-Credit-Operations cycle.
--
-- A gap-request batch is an append-only, deterministic snapshot of every
-- CURRENT open evidence gap for a (case, case version), keyed by a sha256
-- hash of that open-gap set.  Its drafted items live in the child table
-- public.gap_request_items.  A human records exactly one append-only
-- disposition per batch (APPROVED_ALL / APPROVED_WITH_CHANGES / REJECTED /
-- NO_OUTBOUND_REQUESTS -- a zero-item batch still requires an explicit
-- NO_OUTBOUND_REQUESTS, never silent satisfaction).  Gate satisfaction is
-- DERIVED in application code (domain/gap_request_batches.derive_g2_from_batch)
-- and written only through the orchestration repository -- never in this
-- migration.
--
-- LEGACY: G2 gate rows already SATISFIED via the removed credit-ops path
-- remain valid history -- human_gates rows are immutable and are not
-- rewritten or backfilled here.  Any NEW case version derives G2 solely from a
-- gap-request batch + disposition; a bumped case version makes the old batch
-- stale (its snapshot hash no longer matches) and re-opens the gate.
--
-- All data is synthetic and created solely for demonstration.

create table public.gap_request_batches (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  open_gap_snapshot_hash char(64) not null
    check (open_gap_snapshot_hash ~ '^[0-9a-f]{64}$'),
  batch_schema_version text not null default 'gap-request-batch-v1'
    check (length(btrim(batch_schema_version)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  -- Idempotent assemble-or-get: re-snapshotting the same open-gap set for the
  -- same (case, version) resolves to the existing batch instead of a new one.
  constraint gap_request_batches_snapshot_key
    unique (case_id, case_version, open_gap_snapshot_hash),
  -- Referenced by the child tables' composite FKs below.
  constraint gap_request_batches_id_case_version_key
    unique (id, case_id, case_version)
);

create index gap_request_batches_case_idx
  on public.gap_request_batches (case_id, case_version, created_at desc);

create trigger gap_request_batches_are_append_only
before update or delete on public.gap_request_batches
for each row execute function public.reject_append_only_mutation();

alter table public.gap_request_batches enable row level security;
alter table public.gap_request_batches force row level security;

create policy gap_request_batches_select_assigned
on public.gap_request_batches
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = gap_request_batches.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.gap_request_batches from public, anon, authenticated;
grant select on public.gap_request_batches to authenticated;
grant all on public.gap_request_batches to service_role;

-- One drafted outbound request per open gap.  Append-only; the request text is
-- assembled deterministically (never LLM-authored) from the gap's own missing
-- information + suggested evidence.
create table public.gap_request_items (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  gap_id uuid not null,
  request_text_vi text not null check (length(btrim(request_text_vi)) > 0),
  blocking_level text not null check (
    blocking_level in ('BLOCKING', 'CONDITIONAL', 'CLARIFICATION')
  ),
  created_at timestamptz not null default clock_timestamp(),
  constraint gap_request_items_batch_fk
    foreign key (batch_id, case_id, case_version)
    references public.gap_request_batches(id, case_id, case_version)
    on delete restrict,
  -- At most one drafted request per gap within a batch.
  constraint gap_request_items_batch_gap_key unique (batch_id, gap_id)
);

create index gap_request_items_batch_idx
  on public.gap_request_items (batch_id, created_at);

create trigger gap_request_items_are_append_only
before update or delete on public.gap_request_items
for each row execute function public.reject_append_only_mutation();

alter table public.gap_request_items enable row level security;
alter table public.gap_request_items force row level security;

create policy gap_request_items_select_assigned
on public.gap_request_items
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = gap_request_items.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.gap_request_items from public, anon, authenticated;
grant select on public.gap_request_items to authenticated;
grant all on public.gap_request_items to service_role;

-- One append-only human disposition per batch.  ``disposition_type`` is a
-- closed set; ``NO_OUTBOUND_REQUESTS`` is the explicit "nothing to send"
-- decision required for an empty batch.  ``item_dispositions`` maps item id ->
-- APPROVED/REMOVED/EDITED and ``edited_texts`` carries replacement text for
-- EDITED items (both schema-checked JSON objects).
create table public.gap_request_batch_dispositions (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  disposition_type text not null check (
    disposition_type in (
      'APPROVED_ALL', 'APPROVED_WITH_CHANGES', 'REJECTED', 'NO_OUTBOUND_REQUESTS'
    )
  ),
  item_dispositions jsonb not null default '{}'::jsonb
    check (jsonb_typeof(item_dispositions) = 'object'),
  edited_texts jsonb not null default '{}'::jsonb
    check (jsonb_typeof(edited_texts) = 'object'),
  actor_id uuid not null,
  actor_role text not null check (length(btrim(actor_role)) > 0),
  rationale_vi text not null check (length(btrim(rationale_vi)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  constraint gap_request_batch_dispositions_batch_fk
    foreign key (batch_id, case_id, case_version)
    references public.gap_request_batches(id, case_id, case_version)
    on delete restrict
);

create index gap_request_batch_dispositions_batch_idx
  on public.gap_request_batch_dispositions (batch_id, created_at);

create trigger gap_request_batch_dispositions_are_append_only
before update or delete on public.gap_request_batch_dispositions
for each row execute function public.reject_append_only_mutation();

alter table public.gap_request_batch_dispositions enable row level security;
alter table public.gap_request_batch_dispositions force row level security;

create policy gap_request_batch_dispositions_select_assigned
on public.gap_request_batch_dispositions
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = gap_request_batch_dispositions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.gap_request_batch_dispositions from public, anon, authenticated;
grant select on public.gap_request_batch_dispositions to authenticated;
grant all on public.gap_request_batch_dispositions to service_role;
