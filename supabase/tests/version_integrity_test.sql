begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(4);

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-000000000001',
  2,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.documents (id, case_id, created_by)
values (
  '60000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.document_versions (
  id,
  document_id,
  case_id,
  case_version,
  version,
  storage_bucket,
  storage_object_key,
  original_filename,
  declared_content_type,
  byte_size,
  content_sha256,
  created_by
)
values (
  '61000000-0000-0000-0000-000000000001',
  '60000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001',
  1,
  1,
  'creditops-originals',
  'originals/61000000-0000-0000-0000-000000000001',
  'synthetic.pdf',
  'application/pdf',
  1024,
  repeat('a', 64),
  '00000000-0000-0000-0000-000000000001'
);

select throws_ok(
  $$
    insert into public.page_regions (
      case_id, case_version, document_version_id, page_number,
      x, y, width, height, extraction_method
    ) values (
      '10000000-0000-0000-0000-000000000001', 2,
      '61000000-0000-0000-0000-000000000001', 1,
      0, 0, 0.5, 0.5, 'SYNTHETIC_TEST'
    )
  $$,
  '23503',
  null,
  'a page region cannot bind a document version from another case version'
);

insert into public.page_regions (
  id, case_id, case_version, document_version_id, page_number,
  x, y, width, height, extraction_method
)
values (
  '62000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001', 1,
  '61000000-0000-0000-0000-000000000001', 1,
  0, 0, 0.5, 0.5, 'SYNTHETIC_TEST'
);

select throws_ok(
  $$
    insert into public.processing_tasks (
      case_id, case_version, document_version_id, task_type,
      max_attempts, input_payload, idempotency_key
    ) values (
      '10000000-0000-0000-0000-000000000001', 2,
      '61000000-0000-0000-0000-000000000001', 'SYNTHETIC_TEST',
      1, '{}'::jsonb, 'cross-version-task'
    )
  $$,
  '23503',
  null,
  'a processing task cannot consume a document from another case version'
);

insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type,
  max_attempts, input_payload, idempotency_key
)
values (
  '40000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000001', 1,
  '61000000-0000-0000-0000-000000000001', 'SYNTHETIC_TEST',
  1, '{}'::jsonb, 'same-version-task'
);

select throws_ok(
  $$
    insert into public.task_checkpoints (
      task_id, case_id, case_version, document_version_id, sequence_no,
      checkpoint_type, checkpoint_data
    ) values (
      '40000000-0000-0000-0000-000000000001',
      '10000000-0000-0000-0000-000000000001', 2,
      '61000000-0000-0000-0000-000000000001',
      1, 'SYNTHETIC_TEST', '{}'::jsonb
    )
  $$,
  '23503',
  null,
  'a checkpoint cannot claim a different case version than its task'
);

select throws_ok(
  $$
    insert into public.retrieval_passages (
      case_id, case_version, document_version_id, page_region_id,
      passage_text, extraction_method
    ) values (
      '10000000-0000-0000-0000-000000000001', 2,
      '61000000-0000-0000-0000-000000000001',
      '62000000-0000-0000-0000-000000000001',
      'Synthetic evidence only', 'SYNTHETIC_TEST'
    )
  $$,
  '23503',
  null,
  'a retrieval passage cannot cross document and region case versions'
);

select * from finish();
rollback;
