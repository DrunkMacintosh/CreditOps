-- pgTAP: retrieval_queries / retrieval_hits append-only trace stores (the
-- graph-guided hybrid RAG provenance; master design sections 12, 12.2, 12.3).
-- All data below is synthetic and created solely for demonstration; the case
-- belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pgmq, pg_catalog;

select plan(17);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

insert into public.documents (id, case_id, created_by)
values (
  '60000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.document_versions (
  id, document_id, case_id, case_version, version,
  storage_bucket, storage_object_key, original_filename,
  declared_content_type, byte_size, content_sha256, created_by
)
values (
  '61000000-0000-0000-0000-0000000000f1',
  '60000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1, 1,
  'creditops-originals', 'originals/61000000-0000-0000-0000-0000000000f1',
  'synthetic.pdf', 'application/pdf', 1024, repeat('b', 64),
  '00000000-0000-0000-0000-000000000001'
);

insert into public.page_regions (
  id, case_id, case_version, document_version_id, page_number,
  x, y, width, height, extraction_method
)
values (
  '62000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  '61000000-0000-0000-0000-0000000000f1', 1,
  0, 0, 0.5, 0.5, 'SYNTHETIC_TEST'
);

insert into public.retrieval_passages (
  id, case_id, case_version, document_version_id, page_region_id,
  passage_text, extraction_method
)
values (
  '63000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  '61000000-0000-0000-0000-0000000000f1',
  '62000000-0000-0000-0000-0000000000f1',
  'Doanh thu thuan nam 2025 (du lieu mo phong).', 'SYNTHETIC_TEST'
);

-- 1. A retrieval query persists with typed seed refs and scope filters.
insert into public.retrieval_queries (
  id, case_id, case_version, task_id, query_text_vi, seed_node_refs, filters
)
values (
  '70000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  '71000000-0000-0000-0000-0000000000f1',
  'Doanh thu thuan',
  '[{"kind": "CONFIRMED_FACT", "id": "63000000-0000-0000-0000-0000000000f1"}]'::jsonb,
  '{"caseId": "10000000-0000-0000-0000-0000000000f1", "caseVersion": 1, "mode": "HYBRID"}'::jsonb
);

select is(
  (select count(*) from public.retrieval_queries),
  1::bigint,
  'a retrieval query row persists'
);

-- 2. seed_node_refs must be a JSON array.
select throws_ok(
  $$insert into public.retrieval_queries (
      case_id, case_version, query_text_vi, seed_node_refs
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'x',
      '{"kind": "GAP"}'::jsonb
    )$$,
  '23514',
  null,
  'seed_node_refs must be a JSON array, not an object'
);

-- 3. filters must be a JSON object.
select throws_ok(
  $$insert into public.retrieval_queries (
      case_id, case_version, query_text_vi, filters
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'x', '[]'::jsonb
    )$$,
  '23514',
  null,
  'filters must be a JSON object, not an array'
);

-- 4. Queries are append-only.
select throws_ok(
  $$update public.retrieval_queries set query_text_vi = 'sua doi'$$,
  '42501',
  null,
  'retrieval queries are append-only (no update)'
);

select throws_ok(
  $$delete from public.retrieval_queries$$,
  '42501',
  null,
  'retrieval queries are append-only (no delete)'
);

-- 5. A retrieval hit persists and references the passage + query in scope.
insert into public.retrieval_hits (
  id, query_id, case_id, case_version, passage_id, rank,
  lexical_score, vector_score, passage_hash
)
values (
  '80000000-0000-0000-0000-0000000000f1',
  '70000000-0000-0000-0000-0000000000f1',
  '10000000-0000-0000-0000-0000000000f1', 1,
  '63000000-0000-0000-0000-0000000000f1', 1,
  0.42, 0.87,
  'ccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
);

select is(
  (select count(*) from public.retrieval_hits),
  1::bigint,
  'a retrieval hit row persists'
);

-- 6. At most one hit per (query, passage).
select throws_ok(
  $$insert into public.retrieval_hits (
      query_id, case_id, case_version, passage_id, rank, lexical_score, passage_hash
    ) values (
      '70000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 1,
      '63000000-0000-0000-0000-0000000000f1', 2, 0.1,
      'ccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
    )$$,
  '23505',
  null,
  'a passage appears at most once per query'
);

-- 7. A malformed passage_hash is rejected.
select throws_ok(
  $$insert into public.retrieval_hits (
      query_id, case_id, case_version, passage_id, rank, lexical_score, passage_hash
    ) values (
      '70000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 1,
      '63000000-0000-0000-0000-0000000000f1', 3, 0.1, 'not-a-hash'
    )$$,
  '23514',
  null,
  'the passage hash must be 64 lowercase hex chars'
);

-- 8. A hit with neither score is rejected.
select throws_ok(
  $$insert into public.retrieval_hits (
      query_id, case_id, case_version, passage_id, rank, passage_hash
    ) values (
      '70000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 1,
      '63000000-0000-0000-0000-0000000000f1', 4,
      'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'
    )$$,
  '23514',
  null,
  'a hit must carry at least one of lexical/vector score'
);

-- 9. A hit cannot point at a query in a different case scope (composite FK).
select throws_ok(
  $$insert into public.retrieval_hits (
      query_id, case_id, case_version, passage_id, rank, lexical_score, passage_hash
    ) values (
      '70000000-0000-0000-0000-0000000000f1',
      '10000000-0000-0000-0000-0000000000f1', 2,
      '63000000-0000-0000-0000-0000000000f1', 5, 0.1,
      'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
    )$$,
  '23503',
  null,
  'a hit cannot reference a query outside its case version scope'
);

-- 10. Hits are append-only.
select throws_ok(
  $$update public.retrieval_hits set rank = 9$$,
  '42501',
  null,
  'retrieval hits are append-only (no update)'
);

select throws_ok(
  $$delete from public.retrieval_hits$$,
  '42501',
  null,
  'retrieval hits are append-only (no delete)'
);

-- 11. RLS: the assigned officer reads; an unassigned actor sees nothing; writes
-- remain service-role only.
set local role authenticated;
select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true
);

select is(
  (select count(*) from public.retrieval_queries),
  1::bigint,
  'the assigned officer can read the retrieval query'
);

select is(
  (select count(*) from public.retrieval_hits),
  1::bigint,
  'the assigned officer can read the retrieval hit'
);

select set_config(
  'request.jwt.claim.sub', '00000000-0000-0000-0000-000000000099', true
);

select is(
  (select count(*) from public.retrieval_queries),
  0::bigint,
  'an unassigned actor cannot read any retrieval query'
);

select is(
  (select count(*) from public.retrieval_hits),
  0::bigint,
  'an unassigned actor cannot read any retrieval hit'
);

select throws_ok(
  $$insert into public.retrieval_queries (
      case_id, case_version, query_text_vi
    ) values (
      '10000000-0000-0000-0000-0000000000f1', 1, 'khong duoc phep'
    )$$,
  '42501',
  null,
  'authenticated users cannot write retrieval queries (service role only)'
);

reset role;

select * from finish();
rollback;
