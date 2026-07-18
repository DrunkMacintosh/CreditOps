-- Proves migration 202607180026 closes the MAKER_MUST_REVISE handoff-re-issue
-- boundary: after a case-version bump, the intake handoff can be re-issued at the
-- NEW version anchored on the ORIGINAL intake ingestion task (recorded at the old
-- version), while a handoff still cannot anchor on a task from another case.
--
-- The statements below are exactly those PostgresOrchestrationRepository
-- .bump_case_version issues in one transaction: the optimistic version bump, the
-- CASE_VERSION_BUMPED audit row, and the handoff clone INSERT..SELECT that
-- preserves source_task_id.  All identifiers are synthetic.

begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, pg_catalog;

select plan(6);

-- ---------------------------------------------------------------------------
-- Case A intake evidence at version 1: document -> version -> ingestion task ->
-- intake handoff.  This is the state the revision loop bumps.
-- ---------------------------------------------------------------------------
insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000a1', 1, 'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.documents (id, case_id, created_by)
values (
  '60000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.document_versions (
  id, document_id, case_id, case_version, version,
  storage_bucket, storage_object_key, original_filename,
  declared_content_type, byte_size, content_sha256, created_by
)
values (
  '61000000-0000-0000-0000-0000000000a1',
  '60000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 1, 1,
  'creditops-originals', 'originals/61000000-0000-0000-0000-0000000000a1',
  'synthetic.pdf', 'application/pdf', 1024, repeat('a', 64),
  '00000000-0000-0000-0000-000000000001'
);

insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type,
  status, max_attempts, input_payload, idempotency_key
)
values (
  '40000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 1,
  '61000000-0000-0000-0000-0000000000a1', 'DOCUMENT_INGESTION',
  'SUCCEEDED', 3, '{}'::jsonb, 'intake-ingestion-a1'
);

insert into public.handoffs (
  id, case_id, case_version, source_task_id, state, handoff_data, created_by_type
)
values (
  '80000000-0000-0000-0000-0000000000a1',
  '10000000-0000-0000-0000-0000000000a1', 1,
  '40000000-0000-0000-0000-0000000000a1',
  'READY_FOR_SPECIALIST_REVIEW',
  '{"confirmedFacts":[{"fieldKey":"synthetic.amount","value":100}]}'::jsonb,
  'HUMAN:INTAKE_OFFICER'
);

-- ---------------------------------------------------------------------------
-- Case B, an unrelated case with its own intake ingestion task -- used as the
-- negative control for case-fencing.
-- ---------------------------------------------------------------------------
insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-0000000000b1', 1, 'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.documents (id, case_id, created_by)
values (
  '60000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.document_versions (
  id, document_id, case_id, case_version, version,
  storage_bucket, storage_object_key, original_filename,
  declared_content_type, byte_size, content_sha256, created_by
)
values (
  '61000000-0000-0000-0000-0000000000b1',
  '60000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1, 1,
  'creditops-originals', 'originals/61000000-0000-0000-0000-0000000000b1',
  'synthetic.pdf', 'application/pdf', 1024, repeat('b', 64),
  '00000000-0000-0000-0000-000000000001'
);

insert into public.processing_tasks (
  id, case_id, case_version, document_version_id, task_type,
  status, max_attempts, input_payload, idempotency_key
)
values (
  '40000000-0000-0000-0000-0000000000b1',
  '10000000-0000-0000-0000-0000000000b1', 1,
  '61000000-0000-0000-0000-0000000000b1', 'DOCUMENT_INGESTION',
  'SUCCEEDED', 3, '{}'::jsonb, 'intake-ingestion-b1'
);

-- ---------------------------------------------------------------------------
-- The three statements bump_case_version issues, in order.
-- 1. Optimistic version bump: case A -> version 2.
-- ---------------------------------------------------------------------------
update public.credit_cases
set case_version = case_version + 1, updated_at = clock_timestamp()
where id = '10000000-0000-0000-0000-0000000000a1' and case_version = 1;

select is(
  (select case_version from public.credit_cases
   where id = '10000000-0000-0000-0000-0000000000a1'),
  2,
  'the optimistic bump advances case A to version 2'
);

-- 2. CASE_VERSION_BUMPED audit row at the new version.
select lives_ok(
  $$
    insert into public.audit_events (
      case_id, case_version, event_type, actor_type, actor_id,
      artifact_type, artifact_id, event_data
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 2, 'CASE_VERSION_BUMPED',
      'HUMAN:RISK_REVIEWER', '00000000-0000-0000-0000-000000000001',
      'CREDIT_CASE', '10000000-0000-0000-0000-0000000000a1',
      '{"reason":"synthetic","newVersion":2}'::jsonb
    )
  $$,
  'the CASE_VERSION_BUMPED audit row is written at the new version'
);

-- 3. Re-issue the intake handoff at version 2 by cloning the version-1 handoff,
--    preserving source_task_id (the version-1 ingestion task).  Under the
--    relaxed (source_task_id, case_id) FK this now satisfies a live Postgres.
select lives_ok(
  $$
    insert into public.handoffs (
      id, case_id, case_version, source_task_id, state,
      handoff_schema_version, handoff_data, created_by_type, created_by_id
    )
    select '80000000-0000-0000-0000-0000000000a2', case_id, 2, source_task_id,
           state, handoff_schema_version,
           handoff_data || '{"revisionProvenance":{"reissuedFromVersion":1}}'::jsonb,
           created_by_type, created_by_id
    from public.handoffs
    where case_id = '10000000-0000-0000-0000-0000000000a1' and case_version = 1
      and state = 'READY_FOR_SPECIALIST_REVIEW' and stale_at is null
    order by created_at desc
    limit 1
  $$,
  'the intake handoff re-issues at version 2 anchored on the version-1 ingestion task'
);

select is(
  (select source_task_id from public.handoffs
   where id = '80000000-0000-0000-0000-0000000000a2'),
  '40000000-0000-0000-0000-0000000000a1'::uuid,
  're-issued handoff preserves the original version-1 ingestion source_task_id'
);

select is(
  (select handoff_data -> 'revisionProvenance' ->> 'reissuedFromVersion'
   from public.handoffs where id = '80000000-0000-0000-0000-0000000000a2'),
  '1',
  're-issued handoff carries the frozen evidence snapshot plus a provenance note'
);

-- Negative control: the FK is version-decoupled but still CASE-fenced.  A handoff
-- in case A may not anchor on case B's ingestion task.
select throws_ok(
  $$
    insert into public.handoffs (
      case_id, case_version, source_task_id, state, handoff_data, created_by_type
    ) values (
      '10000000-0000-0000-0000-0000000000a1', 2,
      '40000000-0000-0000-0000-0000000000b1',
      'READY_FOR_SPECIALIST_REVIEW', '{}'::jsonb, 'HUMAN:INTAKE_OFFICER'
    )
  $$,
  '23503',
  null,
  'a handoff still cannot anchor on a processing task from another case'
);

select * from finish();
rollback;
