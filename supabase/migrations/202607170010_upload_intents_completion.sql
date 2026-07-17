-- Task 5: direct private Storage upload registration.
-- The browser receives only a short-lived signed operation.  All database
-- writes below are actor-bound to the active intake assignment and are made
-- by the server's bounded creditops_api role.

do $$
declare
  bucket_record record;
  existing_public boolean;
  existing_limit bigint;
  existing_mimes text[];
begin
  for bucket_record in
    select * from (values
      ('creditops-incoming'::text, 104857600::bigint,
       array['application/pdf', 'image/png', 'image/jpeg',
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']::text[]),
      ('creditops-originals'::text, null::bigint, null::text[]),
      ('creditops-derived'::text, null::bigint, null::text[])
    ) as configured(id, file_size_limit, allowed_mime_types)
  loop
    select public, file_size_limit, allowed_mime_types
      into existing_public, existing_limit, existing_mimes
      from storage.buckets
      where id = bucket_record.id;
    if found then
      if existing_public
        or (bucket_record.file_size_limit is not null and existing_limit is distinct from bucket_record.file_size_limit)
        or (bucket_record.allowed_mime_types is not null and existing_mimes is distinct from bucket_record.allowed_mime_types) then
        raise exception using errcode = '23514',
          message = 'CreditOps Storage bucket configuration is not private or bounded';
      end if;
    else
      insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
      values (
        bucket_record.id,
        bucket_record.id,
        false,
        bucket_record.file_size_limit,
        bucket_record.allowed_mime_types
      );
    end if;
  end loop;
end;
$$;

alter table public.upload_intents
  add column if not exists original_filename text default 'document',
  add column if not exists declared_size_bytes bigint,
  add column if not exists status text not null default 'OPEN',
  add column if not exists completion_idempotency_record_id uuid;

update public.upload_intents
set declared_size_bytes = size_ceiling
where declared_size_bytes is null;

alter table public.upload_intents
  alter column declared_size_bytes set not null,
  add constraint upload_intents_status_check check (status in ('OPEN', 'CONSUMED')),
  add constraint upload_intents_consumed_status_check check (
    (status = 'OPEN' and consumed_at is null)
    or (status = 'CONSUMED' and consumed_at is not null
        and completion_idempotency_record_id is not null)
  ),
  add constraint upload_intents_completion_idempotency_fk
    foreign key (completion_idempotency_record_id)
    references public.idempotency_records(id)
    on delete restrict,
  add constraint upload_intents_completion_idempotency_unique
    unique (completion_idempotency_record_id),
  add constraint upload_intents_original_filename_check check (
    length(btrim(original_filename)) between 1 and 255
    and original_filename !~ '[\\/\x00-\x1F\x7F]'
  ),
  add constraint upload_intents_declared_size_exact check (
    declared_size_bytes = size_ceiling
    and declared_size_bytes > 0
    and declared_size_bytes <= 104857600
  );

create or replace function public.protect_upload_intent_identity()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', message = 'upload intents cannot be deleted';
  end if;
  if row(
    new.id, new.case_id, new.case_version, new.assigned_officer_id,
    new.bucket_id, new.object_key, new.original_filename,
    new.accepted_content_type, new.size_ceiling, new.declared_size_bytes,
    new.expires_at, new.created_at
  ) is distinct from row(
    old.id, old.case_id, old.case_version, old.assigned_officer_id,
    old.bucket_id, old.object_key, old.original_filename,
    old.accepted_content_type, old.size_ceiling, old.declared_size_bytes,
    old.expires_at, old.created_at
  ) then
    raise exception using errcode = '42501', message = 'upload intent identity is immutable';
  end if;
  if old.status = 'CONSUMED' or old.consumed_at is not null then
    if new.status is distinct from old.status
      or new.consumed_at is distinct from old.consumed_at
      or new.completion_idempotency_record_id is distinct from old.completion_idempotency_record_id then
      raise exception using errcode = '42501', message = 'consumed upload intent is immutable';
    end if;
  elsif new.status = 'OPEN' then
    if new.consumed_at is not null or new.completion_idempotency_record_id is not null then
      raise exception using errcode = '23514', message = 'invalid upload intent consumption';
    end if;
  elsif new.status = 'CONSUMED' then
    if new.consumed_at is null or new.completion_idempotency_record_id is null
      or new.consumed_at < old.created_at then
      raise exception using errcode = '23514', message = 'invalid upload intent consumption';
    end if;
  else
    raise exception using errcode = '23514', message = 'invalid upload intent consumption';
  end if;
  if new.status = 'CONSUMED' and not exists (
    select 1
    from public.idempotency_records as record
    where record.id = new.completion_idempotency_record_id
      and record.case_id = new.case_id
      and record.actor_id = new.assigned_officer_id
      and record.operation = 'COMPLETE_UPLOAD_INTENT_V1'
      and record.completed_at is not null
  ) then
    raise exception using errcode = '23514', message = 'consumed upload intent lacks a completed idempotency record';
  end if;
  return new;
end;
$$;

revoke all on function public.protect_upload_intent_identity() from public;
drop trigger if exists upload_intents_protect_identity on public.upload_intents;
create trigger upload_intents_protect_identity
before update or delete on public.upload_intents
for each row execute function public.protect_upload_intent_identity();

-- A document is an append-only identity.  New versions carry corrections;
-- mutating or deleting the identity would sever provenance.
drop trigger if exists documents_are_immutable on public.documents;
create trigger documents_are_immutable
before update or delete on public.documents
for each row execute function public.reject_append_only_mutation();

create unique index if not exists document_versions_case_content_hash_uq
  on public.document_versions (case_id, content_sha256)
  where stale_at is null;

alter table public.idempotency_records
  add column if not exists lease_owner uuid,
  add column if not exists lease_until timestamptz;

alter table public.idempotency_records
  add constraint idempotency_records_lease_pair check (
    (lease_owner is null and lease_until is null)
    or (lease_owner is not null and lease_until is not null)
  );

create index if not exists idempotency_records_reclaim_idx
  on public.idempotency_records (lease_until)
  where completed_at is null;

create or replace function public.protect_upload_idempotency_identity()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', message = 'idempotency records cannot be deleted';
  end if;
  if row(new.id, new.case_id, new.actor_id, new.operation,
         new.idempotency_key, new.request_sha256, new.created_at)
     is distinct from
     row(old.id, old.case_id, old.actor_id, old.operation,
         old.idempotency_key, old.request_sha256, old.created_at) then
    raise exception using errcode = '42501', message = 'idempotency request identity is immutable';
  end if;
  if old.completed_at is not null and row(new.response_status, new.response_schema_version,
      new.response_data, new.completed_at) is distinct from row(old.response_status,
      old.response_schema_version, old.response_data, old.completed_at) then
    raise exception using errcode = '42501', message = 'completed idempotency response is immutable';
  end if;
  return new;
end;
$$;

revoke all on function public.protect_upload_idempotency_identity() from public;
drop trigger if exists idempotency_records_protect_identity on public.idempotency_records;
create trigger idempotency_records_protect_identity
before update or delete on public.idempotency_records
for each row execute function public.protect_upload_idempotency_identity();

-- No browser role gets table writes.  creditops_api is only usable after the
-- API transaction sets auth.uid() and SET LOCAL ROLE creditops_api.
revoke all on
  public.upload_intents,
  public.idempotency_records,
  public.documents,
  public.document_versions,
  public.processing_tasks
from public, anon, authenticated;

grant select, insert, update on public.upload_intents to creditops_api;
grant select, insert, update on public.idempotency_records to creditops_api;
grant select, insert on public.documents, public.document_versions, public.processing_tasks
to creditops_api;

drop policy if exists upload_intents_api_select on public.upload_intents;
create policy upload_intents_api_select
on public.upload_intents
for select to creditops_api
using (
  assigned_officer_id = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = upload_intents.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

drop policy if exists upload_intents_api_insert on public.upload_intents;
create policy upload_intents_api_insert
on public.upload_intents
for insert to creditops_api
with check (
  assigned_officer_id = (select auth.uid())
  and status = 'OPEN'
  and consumed_at is null
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = upload_intents.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

drop policy if exists upload_intents_api_update on public.upload_intents;
create policy upload_intents_api_update
on public.upload_intents
for update to creditops_api
using (
  assigned_officer_id = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = upload_intents.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
)
with check (status in ('OPEN', 'CONSUMED'));

create policy idempotency_records_api_select
on public.idempotency_records
for select to creditops_api
using (
  actor_id = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = idempotency_records.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy idempotency_records_api_insert
on public.idempotency_records
for insert to creditops_api
with check (
  actor_id = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = idempotency_records.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy idempotency_records_api_update
on public.idempotency_records
for update to creditops_api
using (
  actor_id = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = idempotency_records.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
)
with check (
  actor_id = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = idempotency_records.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy documents_api_select
on public.documents
for select to creditops_api
using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = documents.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy documents_api_insert
on public.documents
for insert to creditops_api
with check (
  created_by = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = documents.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy document_versions_api_select
on public.document_versions
for select to creditops_api
using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = document_versions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy document_versions_api_insert
on public.document_versions
for insert to creditops_api
with check (
  created_by = (select auth.uid())
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = document_versions.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy processing_tasks_api_select
on public.processing_tasks
for select to creditops_api
using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = processing_tasks.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

create policy processing_tasks_api_insert
on public.processing_tasks
for insert to creditops_api
with check (
  status = 'PENDING'
  and attempt_count = 0
  and lease_token is null
  and lease_until is null
  and completed_at is null
  and exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = processing_tasks.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
  and exists (
    select 1 from public.document_versions as version
    where version.id = processing_tasks.document_version_id
      and version.case_id = processing_tasks.case_id
      and version.case_version = processing_tasks.case_version
  )
);
