-- CONFIRMED upload boundary: the browser may write only to an exact private
-- Storage path authorized by a backend-created, unexpired intent.

create table public.upload_intents (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  assigned_officer_id uuid not null,
  bucket_id text not null default 'creditops-incoming'
    check (bucket_id = 'creditops-incoming'),
  object_key text not null,
  accepted_content_type text not null check (length(btrim(accepted_content_type)) > 0),
  size_ceiling bigint not null check (size_ceiling > 0),
  upload_schema_version text not null default '1' check (length(upload_schema_version) > 0),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  constraint upload_intents_bucket_object_key unique (bucket_id, object_key),
  constraint upload_intents_exact_object_key check (
    object_key = 'incoming/' || case_id::text || '/' || id::text
  ),
  constraint upload_intents_expire_after_creation check (expires_at > created_at),
  constraint upload_intents_consumed_after_creation check (
    consumed_at is null or consumed_at >= created_at
  )
);

create index upload_intents_active_lookup_idx
  on public.upload_intents (
    assigned_officer_id,
    bucket_id,
    object_key,
    expires_at
  )
  where consumed_at is null;

alter table public.upload_intents enable row level security;
alter table public.upload_intents force row level security;

create policy upload_intents_select_assigned
on public.upload_intents
for select
to authenticated
using (
  assigned_officer_id = (select auth.uid())
  and exists (
    select 1
    from public.case_assignments as assignment
    where assignment.case_id = upload_intents.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy creditops_insert_with_active_upload_intent
on storage.objects
for insert
to authenticated
with check (
  bucket_id = 'creditops-incoming'
  and owner_id = (select auth.uid())::text
  and exists (
    select 1
    from public.upload_intents as intent
    where intent.bucket_id = storage.objects.bucket_id
      and intent.object_key = storage.objects.name
      and intent.assigned_officer_id = (select auth.uid())
      and intent.expires_at > statement_timestamp()
      and intent.consumed_at is null
  )
);

-- INSERT ... RETURNING from the Storage service needs SELECT. There is
-- deliberately no UPDATE policy: authenticated upsert remains unavailable.
create policy creditops_select_active_upload_intent_object
on storage.objects
for select
to authenticated
using (
  bucket_id = 'creditops-incoming'
  and owner_id = (select auth.uid())::text
  and exists (
    select 1
    from public.upload_intents as intent
    where intent.bucket_id = storage.objects.bucket_id
      and intent.object_key = storage.objects.name
      and intent.assigned_officer_id = (select auth.uid())
      and intent.expires_at > statement_timestamp()
      and intent.consumed_at is null
  )
);

-- Upsert requires UPDATE in addition to INSERT and SELECT. Keep browser uploads
-- create-only even if another Storage policy is introduced later.
revoke update on storage.objects from anon, authenticated;

revoke all on public.upload_intents from public, anon, authenticated;
grant select on public.upload_intents to authenticated;
grant all on public.upload_intents to service_role;
