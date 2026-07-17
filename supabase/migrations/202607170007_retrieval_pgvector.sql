-- CONFIRMED retrieval boundary: authorize by case and current document version
-- before ranking. Embedding dimension/model remain benchmark-gated, so this
-- migration deliberately creates no fixed-dimension ANN index.

set search_path = public, extensions, pg_catalog;

create table public.retrieval_passages (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  document_version_id uuid not null,
  page_region_id uuid,
  passage_text text not null check (length(btrim(passage_text)) > 0),
  extraction_method text not null check (length(btrim(extraction_method)) > 0),
  lexical_document tsvector generated always as (
    to_tsvector('simple', passage_text)
  ) stored,
  embedding vector,
  embedding_model text,
  embedding_version text,
  passage_schema_version text not null default '1'
    check (length(passage_schema_version) > 0),
  created_at timestamptz not null default clock_timestamp(),
  stale_at timestamptz,
  constraint retrieval_passages_document_case_fk
    foreign key (document_version_id, case_id, case_version)
    references public.document_versions(id, case_id, case_version)
    on delete restrict,
  constraint retrieval_passages_region_case_document_fk
    foreign key (page_region_id, case_id, case_version, document_version_id)
    references public.page_regions(id, case_id, case_version, document_version_id)
    on delete restrict,
  constraint retrieval_passages_embedding_metadata check (
    (embedding is null and embedding_model is null and embedding_version is null)
    or (
      embedding is not null
      and embedding_model is not null
      and length(btrim(embedding_model)) > 0
      and embedding_version is not null
      and length(btrim(embedding_version)) > 0
    )
  ),
  constraint retrieval_passages_stale_after_creation
    check (stale_at is null or stale_at >= created_at)
);

create index retrieval_passages_case_document_idx
  on public.retrieval_passages (
    case_id,
    case_version,
    document_version_id,
    page_region_id
  )
  where stale_at is null;

create index retrieval_passages_lexical_idx
  on public.retrieval_passages using gin (lexical_document);

alter table public.retrieval_passages enable row level security;
alter table public.retrieval_passages force row level security;

create policy retrieval_passages_select_assigned on public.retrieval_passages
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = retrieval_passages.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.retrieval_passages from public, anon, authenticated;
grant select on public.retrieval_passages to authenticated;
grant all on public.retrieval_passages to service_role;
