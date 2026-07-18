-- Make public.evidence_edges structurally append-only, closing the one gap the
-- P0-B review found: every sibling provenance table in
-- 202607170004_documents_facts_edges.sql (document_versions, page_regions,
-- candidate_facts, fact_confirmations, confirmed_facts) has a BEFORE UPDATE OR
-- DELETE trigger, but public.evidence_edges shipped without one, so its history
-- was only conventionally immutable (the writer INSERTs with ON CONFLICT DO
-- NOTHING and never UPDATEs/DELETEs). This mirrors
-- public.protect_candidate_fact_provenance exactly: content columns are
-- immutable, DELETE is rejected, and only a one-time stale_at transition
-- (null -> value) is permitted so a future invalidation pass can mark an edge
-- STALE without rewriting lineage or deleting superseded history.
--
-- Append-only forward migration; the sibling triggers are unchanged. Synthetic
-- development schema only; no production banking data is authorized.

create or replace function public.protect_evidence_edge_immutable()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'evidence edges cannot be deleted';
  end if;

  if row(
    new.id,
    new.case_id,
    new.case_version,
    new.edge_type,
    new.source_entity_type,
    new.source_entity_id,
    new.target_entity_type,
    new.target_entity_id,
    new.edge_schema_version,
    new.edge_data,
    new.created_at
  ) is distinct from row(
    old.id,
    old.case_id,
    old.case_version,
    old.edge_type,
    old.source_entity_type,
    old.source_entity_id,
    old.target_entity_type,
    old.target_entity_id,
    old.edge_schema_version,
    old.edge_data,
    old.created_at
  ) then
    raise exception using
      errcode = '42501',
      message = 'evidence edge lineage is immutable';
  end if;

  if old.stale_at is not null and new.stale_at is distinct from old.stale_at then
    raise exception using
      errcode = '42501',
      message = 'evidence edge stale timestamp is immutable once set';
  end if;

  return new;
end;
$$;

revoke all on function public.protect_evidence_edge_immutable() from public;

create trigger evidence_edges_protect_immutable
before update or delete on public.evidence_edges
for each row execute function public.protect_evidence_edge_immutable();
