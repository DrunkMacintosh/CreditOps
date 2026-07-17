-- PROPOSED structured financing-request state for the synthetic intake slice.
-- This is not an official SHB field set or workflow rule.

create table public.financing_requests (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null,
  case_version integer not null check (case_version > 0),
  request_version integer not null check (request_version > 0),
  requested_amount numeric(30, 0) not null check (requested_amount > 0),
  purpose_vi text not null check (length(btrim(purpose_vi)) > 0),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint financing_requests_case_fk
    foreign key (case_id)
    references public.credit_cases(id)
    on delete restrict,
  constraint financing_requests_version_key
    unique (case_id, request_version)
);

create index financing_requests_case_current_idx
  on public.financing_requests (case_id, case_version, request_version desc);

create or replace function public.enforce_financing_request_case_version()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  current_case_version integer;
begin
  select credit_cases.case_version
    into current_case_version
  from public.credit_cases
  where credit_cases.id = new.case_id
  for share;

  if current_case_version is not null
     and current_case_version <> new.case_version then
    raise exception using
      errcode = '23514',
      message = 'financing request case_version must match the current credit case version';
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_financing_request_case_version() from public;

create trigger financing_requests_require_current_case_version
before insert on public.financing_requests
for each row execute function public.enforce_financing_request_case_version();

create trigger financing_requests_are_immutable
before update or delete on public.financing_requests
for each row execute function public.reject_append_only_mutation();

alter table public.financing_requests enable row level security;
alter table public.financing_requests force row level security;

create policy financing_requests_select_assigned
on public.financing_requests
for select
to authenticated
using (
  exists (
    select 1
    from public.case_assignments as assignment
    where assignment.case_id = financing_requests.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.financing_requests from public, anon, authenticated;
grant select on public.financing_requests to authenticated;
grant select, insert on public.financing_requests to service_role;
