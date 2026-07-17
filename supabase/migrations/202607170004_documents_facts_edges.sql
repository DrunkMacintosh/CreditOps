-- PROPOSED evidence schema for synthetic demonstrations. It preserves immutable
-- content identity and provenance without encoding official banking decisions.

create table public.documents (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  document_schema_version text not null default '1' check (length(document_schema_version) > 0),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint documents_id_case_key unique (id, case_id)
);

create table public.document_versions (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  version integer not null check (version > 0),
  stage text not null default 'REGISTERED' check (
    stage in (
      'REGISTERED',
      'SECURITY_VALIDATED',
      'PARSED',
      'CLASSIFIED',
      'EXTRACTED',
      'INDEXED',
      'READY_FOR_OFFICER_REVIEW'
    )
  ),
  storage_bucket text not null check (
    storage_bucket in ('creditops-originals', 'creditops-derived')
  ),
  storage_object_key text not null check (length(storage_object_key) > 0),
  original_filename text not null check (length(btrim(original_filename)) > 0),
  declared_content_type text not null check (length(btrim(declared_content_type)) > 0),
  detected_content_type text check (
    detected_content_type is null or length(btrim(detected_content_type)) > 0
  ),
  byte_size bigint not null check (byte_size > 0),
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  document_version_schema_version text not null default '1'
    check (length(document_version_schema_version) > 0),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  stale_at timestamptz,
  constraint document_versions_document_case_fk
    foreign key (document_id, case_id)
    references public.documents(id, case_id)
    on delete restrict,
  constraint document_versions_document_version_key unique (document_id, version),
  constraint document_versions_storage_key unique (storage_bucket, storage_object_key),
  constraint document_versions_id_case_version_key unique (id, case_id, case_version),
  constraint document_versions_stale_after_creation
    check (stale_at is null or stale_at >= created_at)
);

create index document_versions_case_version_idx
  on public.document_versions (case_id, case_version, document_id, version);

create or replace function public.protect_document_version_immutable_content()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'document versions cannot be deleted';
  end if;

  if row(
    new.id,
    new.document_id,
    new.case_id,
    new.case_version,
    new.version,
    new.storage_bucket,
    new.storage_object_key,
    new.original_filename,
    new.declared_content_type,
    new.detected_content_type,
    new.byte_size,
    new.content_sha256,
    new.document_version_schema_version,
    new.created_by,
    new.created_at
  ) is distinct from row(
    old.id,
    old.document_id,
    old.case_id,
    old.case_version,
    old.version,
    old.storage_bucket,
    old.storage_object_key,
    old.original_filename,
    old.declared_content_type,
    old.detected_content_type,
    old.byte_size,
    old.content_sha256,
    old.document_version_schema_version,
    old.created_by,
    old.created_at
  ) then
    raise exception using
      errcode = '42501',
      message = 'document version identity and content are immutable';
  end if;

  return new;
end;
$$;

revoke all on function public.protect_document_version_immutable_content() from public;

create trigger document_versions_immutable_content
before update or delete on public.document_versions
for each row execute function public.protect_document_version_immutable_content();

create table public.page_regions (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  document_version_id uuid not null,
  page_number integer not null check (page_number > 0),
  x double precision not null check (x >= 0 and x <= 1),
  y double precision not null check (y >= 0 and y <= 1),
  width double precision not null check (width > 0 and width <= 1),
  height double precision not null check (height > 0 and height <= 1),
  extraction_method text not null check (length(btrim(extraction_method)) > 0),
  region_schema_version text not null default '1' check (length(region_schema_version) > 0),
  created_at timestamptz not null default clock_timestamp(),
  constraint page_regions_within_page check (x + width <= 1 and y + height <= 1),
  constraint page_regions_document_case_fk
    foreign key (document_version_id, case_id, case_version)
    references public.document_versions(id, case_id, case_version)
    on delete restrict,
  constraint page_regions_id_case_version_document_key
    unique (id, case_id, case_version, document_version_id)
);

create index page_regions_document_page_idx
  on public.page_regions (document_version_id, page_number);

create table public.candidate_facts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  document_version_id uuid not null,
  page_region_id uuid not null,
  field_key text not null check (length(btrim(field_key)) > 0),
  proposed_value jsonb not null check (
    jsonb_typeof(proposed_value) in ('string', 'number', 'boolean')
  ),
  confidence double precision not null check (confidence >= 0 and confidence <= 1),
  extraction_method text not null check (length(btrim(extraction_method)) > 0),
  candidate_schema_version text not null default '1' check (length(candidate_schema_version) > 0),
  created_at timestamptz not null default clock_timestamp(),
  stale_at timestamptz,
  constraint candidate_facts_document_case_fk
    foreign key (document_version_id, case_id, case_version)
    references public.document_versions(id, case_id, case_version)
    on delete restrict,
  constraint candidate_facts_region_case_document_fk
    foreign key (page_region_id, case_id, case_version, document_version_id)
    references public.page_regions(id, case_id, case_version, document_version_id)
    on delete restrict,
  constraint candidate_facts_id_case_version_key unique (id, case_id, case_version),
  constraint candidate_facts_stale_after_creation
    check (stale_at is null or stale_at >= created_at)
);

create index candidate_facts_review_idx
  on public.candidate_facts (case_id, case_version, document_version_id, created_at);

create table public.fact_confirmations (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  candidate_fact_id uuid not null,
  disposition text not null check (
    disposition in ('ACCEPTED', 'CORRECTED', 'ABSENT', 'UNREADABLE')
  ),
  corrected_value jsonb,
  actor_id uuid not null,
  assigned_officer_id uuid not null,
  authority_source text not null check (length(btrim(authority_source)) > 0),
  authority_granted_at timestamptz not null,
  confirmation_schema_version text not null default '1'
    check (length(confirmation_schema_version) > 0),
  confirmed_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint fact_confirmations_candidate_case_version_fk
    foreign key (candidate_fact_id, case_id, case_version)
    references public.candidate_facts(id, case_id, case_version)
    on delete restrict,
  constraint fact_confirmations_assignment_fk
    foreign key (case_id, assigned_officer_id)
    references public.case_assignments(case_id, officer_id)
    on delete restrict,
  constraint fact_confirmations_one_disposition unique (candidate_fact_id),
  constraint fact_confirmations_id_candidate_case_version_key
    unique (id, candidate_fact_id, case_id, case_version),
  constraint fact_confirmations_actor_is_assigned_officer
    check (actor_id = assigned_officer_id),
  constraint fact_confirmations_correction_matches_disposition check (
    (disposition = 'CORRECTED'
      and corrected_value is not null
      and jsonb_typeof(corrected_value) in ('string', 'number', 'boolean'))
    or (disposition <> 'CORRECTED' and corrected_value is null)
  ),
  constraint fact_confirmations_time_order check (
    confirmed_at >= authority_granted_at and created_at >= confirmed_at
  )
);

create table public.confirmed_facts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  candidate_fact_id uuid not null,
  confirmation_id uuid not null,
  document_version_id uuid not null,
  page_region_id uuid not null,
  field_key text not null check (length(btrim(field_key)) > 0),
  value jsonb not null check (jsonb_typeof(value) in ('string', 'number', 'boolean')),
  candidate_value jsonb not null check (
    jsonb_typeof(candidate_value) in ('string', 'number', 'boolean')
  ),
  fact_schema_version text not null default '1' check (length(fact_schema_version) > 0),
  confirmed_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  stale_at timestamptz,
  constraint confirmed_facts_candidate_case_version_fk
    foreign key (candidate_fact_id, case_id, case_version)
    references public.candidate_facts(id, case_id, case_version)
    on delete restrict,
  constraint confirmed_facts_confirmation_candidate_case_version_fk
    foreign key (confirmation_id, candidate_fact_id, case_id, case_version)
    references public.fact_confirmations(id, candidate_fact_id, case_id, case_version)
    on delete restrict,
  constraint confirmed_facts_document_case_fk
    foreign key (document_version_id, case_id, case_version)
    references public.document_versions(id, case_id, case_version)
    on delete restrict,
  constraint confirmed_facts_region_case_document_fk
    foreign key (page_region_id, case_id, case_version, document_version_id)
    references public.page_regions(id, case_id, case_version, document_version_id)
    on delete restrict,
  constraint confirmed_facts_id_case_version_key unique (id, case_id, case_version),
  constraint confirmed_facts_one_per_confirmation unique (confirmation_id),
  constraint confirmed_facts_stale_after_creation
    check (stale_at is null or stale_at >= created_at)
);

create or replace function public.derive_and_protect_confirmed_fact()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  candidate_record public.candidate_facts%rowtype;
  confirmation_record public.fact_confirmations%rowtype;
  expected_value jsonb;
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'confirmed facts cannot be deleted';
  end if;

  if tg_op = 'UPDATE' then
    if row(
      new.id,
      new.case_id,
      new.case_version,
      new.candidate_fact_id,
      new.confirmation_id,
      new.document_version_id,
      new.page_region_id,
      new.field_key,
      new.value,
      new.candidate_value,
      new.fact_schema_version,
      new.confirmed_at,
      new.created_at
    ) is distinct from row(
      old.id,
      old.case_id,
      old.case_version,
      old.candidate_fact_id,
      old.confirmation_id,
      old.document_version_id,
      old.page_region_id,
      old.field_key,
      old.value,
      old.candidate_value,
      old.fact_schema_version,
      old.confirmed_at,
      old.created_at
    ) then
      raise exception using
        errcode = '42501',
        message = 'confirmed fact authoritative fields are immutable';
    end if;
    return new;
  end if;

  select * into candidate_record
  from public.candidate_facts
  where id = new.candidate_fact_id;
  if not found then
    raise exception using
      errcode = '23503',
      message = 'confirmed fact candidate does not exist';
  end if;

  select * into confirmation_record
  from public.fact_confirmations
  where id = new.confirmation_id
    and candidate_fact_id = new.candidate_fact_id;
  if not found then
    raise exception using
      errcode = '23503',
      message = 'confirmed fact confirmation does not match candidate';
  end if;

  if confirmation_record.disposition = 'ACCEPTED' then
    expected_value := candidate_record.proposed_value;
  elsif confirmation_record.disposition = 'CORRECTED' then
    expected_value := confirmation_record.corrected_value;
  else
    raise exception using
      errcode = '23514',
      message = 'confirmation disposition does not support a confirmed fact';
  end if;

  if confirmation_record.case_id <> candidate_record.case_id
    or confirmation_record.case_version <> candidate_record.case_version then
    raise exception using
      errcode = '23514',
      message = 'confirmation and candidate case version mismatch';
  end if;

  if (new.case_id is not null and new.case_id is distinct from candidate_record.case_id)
    or (
      new.case_version is not null
      and new.case_version is distinct from candidate_record.case_version
    )
    or (
      new.document_version_id is not null
      and new.document_version_id is distinct from candidate_record.document_version_id
    )
    or (
      new.page_region_id is not null
      and new.page_region_id is distinct from candidate_record.page_region_id
    )
    or (new.field_key is not null and new.field_key is distinct from candidate_record.field_key)
    or (
      new.candidate_value is not null
      and new.candidate_value is distinct from candidate_record.proposed_value
    )
    or (new.value is not null and new.value is distinct from expected_value)
    or (
      new.confirmed_at is not null
      and new.confirmed_at is distinct from confirmation_record.confirmed_at
    ) then
    raise exception using
      errcode = '23514',
      message = 'caller-supplied confirmed fact fields do not match authoritative evidence';
  end if;

  if new.stale_at is not null then
    raise exception using
      errcode = '23514',
      message = 'new confirmed facts cannot start stale';
  end if;

  new.case_id := candidate_record.case_id;
  new.case_version := candidate_record.case_version;
  new.document_version_id := candidate_record.document_version_id;
  new.page_region_id := candidate_record.page_region_id;
  new.field_key := candidate_record.field_key;
  new.candidate_value := candidate_record.proposed_value;
  new.value := expected_value;
  new.confirmed_at := confirmation_record.confirmed_at;
  return new;
end;
$$;

revoke all on function public.derive_and_protect_confirmed_fact() from public;

create trigger confirmed_facts_derive_and_protect
before insert or update or delete on public.confirmed_facts
for each row execute function public.derive_and_protect_confirmed_fact();

create index confirmed_facts_case_field_idx
  on public.confirmed_facts (case_id, case_version, field_key)
  where stale_at is null;

create table public.evidence_edges (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  edge_type text not null check (length(btrim(edge_type)) > 0),
  source_entity_type text not null check (length(btrim(source_entity_type)) > 0),
  source_entity_id uuid not null,
  target_entity_type text not null check (length(btrim(target_entity_type)) > 0),
  target_entity_id uuid not null,
  edge_schema_version text not null default '1' check (length(edge_schema_version) > 0),
  edge_data jsonb not null default '{}'::jsonb check (jsonb_typeof(edge_data) = 'object'),
  created_at timestamptz not null default clock_timestamp(),
  stale_at timestamptz,
  constraint evidence_edges_unique_typed_edge unique (
    case_id,
    case_version,
    edge_type,
    source_entity_type,
    source_entity_id,
    target_entity_type,
    target_entity_id
  ),
  constraint evidence_edges_stale_after_creation
    check (stale_at is null or stale_at >= created_at)
);

create index evidence_edges_source_idx
  on public.evidence_edges (case_id, source_entity_type, source_entity_id, edge_type);
create index evidence_edges_target_idx
  on public.evidence_edges (case_id, target_entity_type, target_entity_id, edge_type);

alter table public.documents enable row level security;
alter table public.documents force row level security;
alter table public.document_versions enable row level security;
alter table public.document_versions force row level security;
alter table public.page_regions enable row level security;
alter table public.page_regions force row level security;
alter table public.candidate_facts enable row level security;
alter table public.candidate_facts force row level security;
alter table public.fact_confirmations enable row level security;
alter table public.fact_confirmations force row level security;
alter table public.confirmed_facts enable row level security;
alter table public.confirmed_facts force row level security;
alter table public.evidence_edges enable row level security;
alter table public.evidence_edges force row level security;

create policy documents_select_assigned on public.documents
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = documents.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy document_versions_select_assigned on public.document_versions
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = document_versions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy page_regions_select_assigned on public.page_regions
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = page_regions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy candidate_facts_select_assigned on public.candidate_facts
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = candidate_facts.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy fact_confirmations_select_assigned on public.fact_confirmations
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = fact_confirmations.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy confirmed_facts_select_assigned on public.confirmed_facts
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = confirmed_facts.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy evidence_edges_select_assigned on public.evidence_edges
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = evidence_edges.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on
  public.documents,
  public.document_versions,
  public.page_regions,
  public.candidate_facts,
  public.fact_confirmations,
  public.confirmed_facts,
  public.evidence_edges
from public, anon, authenticated;

grant select on
  public.documents,
  public.document_versions,
  public.page_regions,
  public.candidate_facts,
  public.fact_confirmations,
  public.confirmed_facts,
  public.evidence_edges
to authenticated;

grant all on
  public.documents,
  public.document_versions,
  public.page_regions,
  public.candidate_facts,
  public.fact_confirmations,
  public.confirmed_facts,
  public.evidence_edges
to service_role;
