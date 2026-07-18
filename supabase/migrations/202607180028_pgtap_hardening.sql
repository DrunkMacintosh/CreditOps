-- Two grant/privilege fixes the first live pgTAP run surfaced.  Both are
-- genuine production defects (the CI Postgres executes real RLS and role
-- privileges that the fake-connection unit tests could never exercise), not
-- test-authoring bugs.  Append-only: this migration redefines a function and
-- restores a stripped grant; it never edits the already-applied migrations.

-- 1. enforce_financing_request_case_version ran SECURITY INVOKER and locks
--    the credit_cases row with `SELECT ... FOR SHARE` to read the current
--    version.  In PostgreSQL a row-share lock additionally requires the
--    caller to hold UPDATE on the locked table; the least-privilege
--    creditops_api role (and any non-owner writer) has SELECT on
--    credit_cases but deliberately NOT UPDATE, so an authorized financing
--    request insert failed with 42501.  The version-match check is a
--    system-internal integrity read, so it runs as the function owner:
--    SECURITY DEFINER decouples the internal lock from the caller's
--    privileges while the locked search_path keeps the definer safe.
create or replace function public.enforce_financing_request_case_version()
returns trigger
language plpgsql
security definer
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

-- 2. Migration 202607170010 blanket-revoked upload_intents from
--    public/anon/authenticated and re-granted only creditops_api, silently
--    stripping the `grant select ... to authenticated` that migration
--    202607170003 established for the Storage row-level policies.  Without
--    it the authenticated (browser) role can no longer evaluate the
--    creditops_insert_with_active_upload_intent policy on storage.objects,
--    so an assigned officer cannot upload at all.  Restore the SELECT; the
--    forced RLS + upload_intents_select_assigned policy still row-filter
--    visibility to the officer's own active intents.
grant select on public.upload_intents to authenticated;
