-- CONFIRMED infrastructure boundary: Supabase owns durable state, queues,
-- private-object metadata, and retrieval indexes. Business rules remain outside SQL.

create schema if not exists extensions;

create extension if not exists pgcrypto with schema extensions;
create extension if not exists vector with schema extensions;
create extension if not exists pgmq;

revoke create on schema public from public;
revoke create on schema public from anon, authenticated;
grant usage on schema public to anon, authenticated, service_role;

create or replace function public.reject_append_only_mutation()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  raise exception using
    errcode = '42501',
    message = replace(tg_table_name, '_', ' ') || ' are append-only';
end;
$$;

revoke all on function public.reject_append_only_mutation() from public;
