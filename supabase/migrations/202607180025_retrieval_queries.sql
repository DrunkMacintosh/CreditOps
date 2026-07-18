-- Graph-guided hybrid RAG trace stores (master design sections 12, 12.2, 12.3).
-- These are the append-only PROVENANCE of every retrieval run: a
-- public.retrieval_queries row records the authorized task/query, the typed
-- seed nodes it started from and the tenant/case/version + effective-date
-- filters that scoped it; each public.retrieval_hits row records one passage the
-- run returned, its merge rank and the lexical/vector scores, plus the sha256
-- passage_hash of the immutable source passage a downstream citation validator
-- checks against.  Neither table can confirm a fact, satisfy a gate, or record a
-- credit decision -- retrieval only narrows scope and returns ORIGINAL passages.
--
-- A hit references public.retrieval_passages(id); the query carries the (case,
-- case_version) scope and every hit repeats it, so a retrieval trace is never
-- cross-case.  Both tables are append-only (immutable trace); a new retrieval
-- run appends a new query + hits rather than mutating an old one.
--
-- All data is synthetic and created solely for demonstration.

set search_path = public, extensions, pg_catalog;

create table public.retrieval_queries (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  task_id uuid,
  query_text_vi text not null check (length(btrim(query_text_vi)) > 0),
  -- The typed seed nodes the traversal started from: a JSON array of
  -- {"kind": ..., "id": ...} objects (kind in CONFIRMED_FACT / DOCUMENT_VERSION
  -- / GAP / CONFLICT).  Stored as an opaque snapshot for audit/replay.
  seed_node_refs jsonb not null default '[]'::jsonb
    check (jsonb_typeof(seed_node_refs) = 'array'),
  -- The tenant/case/version/effective-date filters applied before ranking.
  filters jsonb not null default '{}'::jsonb
    check (jsonb_typeof(filters) = 'object'),
  query_schema_version text not null default 'retrieval-query-v1'
    check (length(btrim(query_schema_version)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  -- Referenced by the child hits' composite FK below so a hit can never point at
  -- a query in a different case scope.
  constraint retrieval_queries_id_case_version_key
    unique (id, case_id, case_version)
);

create index retrieval_queries_case_idx
  on public.retrieval_queries (case_id, case_version, created_at desc);

create trigger retrieval_queries_are_append_only
before update or delete on public.retrieval_queries
for each row execute function public.reject_append_only_mutation();

create table public.retrieval_hits (
  id uuid primary key default gen_random_uuid(),
  query_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  passage_id uuid not null
    references public.retrieval_passages(id) on delete restrict,
  rank integer not null check (rank >= 1),
  lexical_score numeric,
  vector_score numeric,
  -- sha256 of the immutable source passage text; the citation validator only
  -- accepts a claim citation whose hash is in this retrieved set.
  passage_hash char(64) not null check (passage_hash ~ '^[0-9a-f]{64}$'),
  hit_schema_version text not null default 'retrieval-hit-v1'
    check (length(btrim(hit_schema_version)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  constraint retrieval_hits_query_fk
    foreign key (query_id, case_id, case_version)
    references public.retrieval_queries(id, case_id, case_version)
    on delete restrict,
  -- A passage appears at most once per query (the pipeline dedupes by passage
  -- before persisting the merged ranking).
  constraint retrieval_hits_query_passage_key unique (query_id, passage_id),
  -- A hit must originate from at least one search leg (lexical, vector, or both).
  constraint retrieval_hits_has_a_score
    check (lexical_score is not null or vector_score is not null)
);

create index retrieval_hits_query_rank_idx
  on public.retrieval_hits (query_id, rank);

create trigger retrieval_hits_are_append_only
before update or delete on public.retrieval_hits
for each row execute function public.reject_append_only_mutation();

alter table public.retrieval_queries enable row level security;
alter table public.retrieval_queries force row level security;
alter table public.retrieval_hits enable row level security;
alter table public.retrieval_hits force row level security;

create policy retrieval_queries_select_assigned on public.retrieval_queries
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = retrieval_queries.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy retrieval_hits_select_assigned on public.retrieval_hits
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = retrieval_hits.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.retrieval_queries from public, anon, authenticated;
revoke all on public.retrieval_hits from public, anon, authenticated;
grant select on public.retrieval_queries to authenticated;
grant select on public.retrieval_hits to authenticated;
grant all on public.retrieval_queries to service_role;
grant all on public.retrieval_hits to service_role;
