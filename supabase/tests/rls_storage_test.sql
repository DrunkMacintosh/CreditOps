begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions, storage, pg_catalog;

select plan(6);

insert into storage.buckets (id, name, public)
values ('creditops-incoming', 'creditops-incoming', false)
on conflict (id) do update set public = excluded.public;

insert into public.credit_cases (id, case_version, workflow_state, created_by)
values (
  '10000000-0000-0000-0000-000000000001',
  1,
  'INTAKE_DRAFT',
  '00000000-0000-0000-0000-000000000001'
);

insert into public.case_assignments (case_id, officer_id, assigned_by)
values (
  '10000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000010'
);

insert into public.upload_intents (
  id,
  case_id,
  case_version,
  assigned_officer_id,
  bucket_id,
  object_key,
  accepted_content_type,
  size_ceiling,
  expires_at
)
values
  (
    '20000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    1,
    '00000000-0000-0000-0000-000000000001',
    'creditops-incoming',
    'incoming/10000000-0000-0000-0000-000000000001/20000000-0000-0000-0000-000000000001',
    'application/pdf',
    1048576,
    clock_timestamp() + interval '5 minutes'
  ),
  (
    '20000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    1,
    '00000000-0000-0000-0000-000000000001',
    'creditops-incoming',
    'incoming/10000000-0000-0000-0000-000000000001/20000000-0000-0000-0000-000000000002',
    'application/pdf',
    1048576,
    clock_timestamp() - interval '1 minute'
  );

select is(
  (
    select count(*)
    from pg_policies
    where schemaname = 'storage'
      and tablename = 'objects'
      and policyname = 'creditops_insert_with_active_upload_intent'
      and cmd = 'INSERT'
  ),
  1::bigint,
  'Storage has one authenticated insert policy backed by upload intents'
);

select is(
  (
    select count(*)
    from pg_policies
    where schemaname = 'storage'
      and tablename = 'objects'
      and policyname like 'creditops_%'
      and cmd = 'UPDATE'
  ),
  0::bigint,
  'Storage exposes no CreditOps update policy, so authenticated upsert is unavailable'
);

set local role authenticated;
select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select lives_ok(
  $$
    insert into storage.objects (bucket_id, name, owner_id)
    values (
      'creditops-incoming',
      'incoming/10000000-0000-0000-0000-000000000001/20000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000001'
    )
  $$,
  'the assigned officer can upload to the exact active-intent path'
);

select throws_ok(
  $$
    insert into storage.objects (bucket_id, name, owner_id)
    values (
      'creditops-incoming',
      'incoming/10000000-0000-0000-0000-000000000001/not-the-intent',
      '00000000-0000-0000-0000-000000000001'
    )
  $$,
  '42501',
  null,
  'the assigned officer cannot upload outside an active intent path'
);

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000002',
  true
);

select throws_ok(
  $$
    insert into storage.objects (bucket_id, name, owner_id)
    values (
      'creditops-incoming',
      'incoming/10000000-0000-0000-0000-000000000001/20000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000002'
    )
  $$,
  '42501',
  null,
  'another officer cannot use the upload intent'
);

select set_config(
  'request.jwt.claim.sub',
  '00000000-0000-0000-0000-000000000001',
  true
);

select throws_ok(
  $$
    insert into storage.objects (bucket_id, name, owner_id)
    values (
      'creditops-incoming',
      'incoming/10000000-0000-0000-0000-000000000001/20000000-0000-0000-0000-000000000002',
      '00000000-0000-0000-0000-000000000001'
    )
  $$,
  '42501',
  null,
  'an expired upload intent cannot authorize a Storage insert'
);

select * from finish();
rollback;
