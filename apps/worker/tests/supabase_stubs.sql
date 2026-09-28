-- Just enough of Supabase for supabase/migrations/ to apply to a plain Postgres, so
-- the real claim_job can be tested without the hosted project. Test-only; never
-- applied anywhere but a throwaway database.
do $$
begin
    if not exists (select from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
    if not exists (select from pg_roles where rolname = 'authenticator') then create role authenticator nologin; end if;
    -- What PostgREST becomes for the service-role key: past row-level security, but
    -- still bound by grants. The queue's contract tests run their two statements as it.
    if not exists (select from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
end $$;

create schema auth;
create table auth.users (id uuid primary key, email text);
create function auth.uid() returns uuid language sql stable as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
-- As on Supabase: every API role can call auth.uid().
grant usage on schema auth to anon, authenticated, service_role;

create schema storage;
create table storage.buckets (
    id text primary key, name text, public boolean,
    file_size_limit bigint, allowed_mime_types text[]
);
create table storage.objects (id uuid default gen_random_uuid(), bucket_id text, name text);
alter table storage.objects enable row level security;
create function storage.foldername(name text) returns text[] language sql immutable as $$
    select string_to_array(name, '/')
$$;
