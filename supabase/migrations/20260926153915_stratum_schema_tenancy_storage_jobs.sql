-- Stratum: agencies, clients, posts, immutable versions, renders, jobs.
--
-- Applied to project piuwwigzpofwrzymmrsw (eu-west-2). This file is the record; the
-- hosted dashboard is not. A migration that exists only in a dashboard cannot be
-- reviewed, cannot be diffed, and cannot rebuild the database from scratch.
--
-- Tenancy rule: every row carries agency_id, and row-level security compares it to
-- the agency of the signed-in user. The API never filters by agency itself - if it
-- did, one forgotten WHERE clause would leak one agency's clients to another.

create schema if not exists stratum;

create table stratum.agencies (
    id uuid primary key default gen_random_uuid(),
    name text not null check (length(name) between 1 and 200),
    created_at timestamptz not null default now()
);

create table stratum.profiles (
    user_id uuid primary key references auth.users (id) on delete cascade,
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    role text not null default 'designer' check (role in ('owner','designer','viewer')),
    created_at timestamptz not null default now()
);
create index profiles_agency_idx on stratum.profiles (agency_id);

-- The agency of the caller. SECURITY DEFINER so it can read profiles whatever that
-- table's own policies say; STABLE so Postgres caches it within a statement;
-- search_path pinned so nobody can shadow "stratum" with a schema of their own and
-- have this read their table instead of ours.
create or replace function stratum.auth_agency_id()
returns uuid language sql stable security definer set search_path = stratum, public as $$
    select agency_id from stratum.profiles where user_id = auth.uid()
$$;

create table stratum.clients (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name text not null check (length(name) between 1 and 200),
    trade text not null default '',
    brand jsonb not null,
    rules jsonb not null,
    archived boolean not null default false,
    created_at timestamptz not null default now(),
    unique (agency_id, slug)
);
create index clients_agency_idx on stratum.clients (agency_id);

create table stratum.assets (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    client_id uuid references stratum.clients (id) on delete cascade,
    sha256 text not null check (sha256 ~ '^[a-f0-9]{64}$'),
    kind text not null default 'photo' check (kind in ('photo','logo','generated')),
    width integer not null check (width > 0),
    height integer not null check (height > 0),
    flags jsonb not null default '[]'::jsonb,
    quality jsonb not null default '{}'::jsonb,
    storage_path text not null,
    created_at timestamptz not null default now(),
    unique (agency_id, sha256)
);
create index assets_client_idx on stratum.assets (client_id);

create table stratum.posts (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    client_id uuid not null references stratum.clients (id) on delete cascade,
    slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{0,78}$'),
    template_id text not null,
    brief text not null check (length(brief) between 1 and 2000),
    occasion text not null default '',
    photo_note text not null default '',
    aspects text[] not null default array['4:5','1:1'],
    status text not null default 'draft' check (status in ('draft','review','approved','archived')),
    words jsonb,
    cost_usd numeric(10,6) not null default 0,
    created_by uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (client_id, slug)
);
create index posts_client_idx on stratum.posts (client_id, created_at desc);

-- Append-only. An edit never mutates a poster, it creates the next version, so
-- "the old one was better" is one click and never a rebuild.
create table stratum.post_versions (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    post_id uuid not null references stratum.posts (id) on delete cascade,
    version integer not null check (version >= 1),
    spec jsonb not null,
    note text not null default '',
    created_by uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now(),
    unique (post_id, version)
);
create index post_versions_post_idx on stratum.post_versions (post_id, version desc);

create table stratum.renders (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    post_version_id uuid not null references stratum.post_versions (id) on delete cascade,
    aspect text not null check (aspect in ('1:1','4:5','9:16','16:9')),
    width integer not null check (width > 0),
    height integer not null check (height > 0),
    bytes integer not null check (bytes > 0),
    storage_path text not null,
    created_at timestamptz not null default now(),
    unique (post_version_id, aspect)
);

create table stratum.flags (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    post_version_id uuid not null references stratum.post_versions (id) on delete cascade,
    code text not null,
    message text not null,
    severity text not null check (severity in ('blocker','warning'))
);
create index flags_version_idx on stratum.flags (post_version_id);

-- Every tweak a human types. Proves "changes are instant", and later becomes the
-- training signal for choosing layouts - collected passively, so nobody is asked
-- to label anything.
create table stratum.edits (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    post_id uuid not null references stratum.posts (id) on delete cascade,
    from_version integer not null,
    message text not null,
    ops jsonb not null default '[]'::jsonb,
    outcome text not null check (outcome in ('applied','needs_human','failed')),
    latency_ms integer not null default 0,
    cost_usd numeric(10,6) not null default 0,
    created_by uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now()
);
create index edits_post_idx on stratum.edits (post_id, created_at desc);

create table stratum.steps (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    post_id uuid not null references stratum.posts (id) on delete cascade,
    step text not null,
    provider text not null default '',
    model text not null default '',
    cached boolean not null default false,
    cost_usd numeric(10,6) not null default 0,
    latency_ms integer not null default 0,
    run_id text not null default '',
    created_at timestamptz not null default now()
);
create index steps_post_idx on stratum.steps (post_id);

-- Durable work. A crashed worker's job reappears when its lease lapses, so "0 lost
-- jobs" stops being an assertion.
create table stratum.jobs (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    kind text not null,
    payload jsonb not null,
    idempotency_key text,
    status text not null default 'queued' check (status in ('queued','running','done','failed')),
    attempts integer not null default 0,
    max_attempts integer not null default 3,
    last_error text not null default '',
    run_after timestamptz not null default now(),
    leased_until timestamptz,
    leased_by text,
    result jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (agency_id, idempotency_key)
);
create index jobs_claimable_idx on stratum.jobs (status, run_after)
    where status in ('queued','running');

alter table stratum.agencies enable row level security;
alter table stratum.profiles enable row level security;
alter table stratum.clients enable row level security;
alter table stratum.assets enable row level security;
alter table stratum.posts enable row level security;
alter table stratum.post_versions enable row level security;
alter table stratum.renders enable row level security;
alter table stratum.flags enable row level security;
alter table stratum.edits enable row level security;
alter table stratum.steps enable row level security;
alter table stratum.jobs enable row level security;

-- Every policy has the same shape: the row's agency must be the caller's agency.
-- USING decides what is visible, WITH CHECK decides what may be written, and both
-- are needed - USING alone lets a caller INSERT into someone else's agency.
create policy agencies_read on stratum.agencies for select to authenticated
    using (id = stratum.auth_agency_id());
create policy profiles_read on stratum.profiles for select to authenticated
    using (agency_id = stratum.auth_agency_id());

do $$
declare target text;
begin
    foreach target in array array['clients','assets','posts','post_versions','renders',
                                  'flags','edits','steps','jobs']
    loop
        execute format($f$
            create policy %I_rw on stratum.%I for all to authenticated
                using (agency_id = stratum.auth_agency_id())
                with check (agency_id = stratum.auth_agency_id())
        $f$, target, target);
    end loop;
end $$;

-- Restrictive policies AND with the permissive ones above: only an owner may delete
-- a client, and a viewer may never create a post.
create policy clients_owner_deletes_only on stratum.clients
    as restrictive for delete to authenticated
    using (exists (select 1 from stratum.profiles p
                   where p.user_id = auth.uid() and p.role = 'owner'));

create policy posts_viewers_cannot_write on stratum.posts
    as restrictive for insert to authenticated
    with check (exists (select 1 from stratum.profiles p
                        where p.user_id = auth.uid() and p.role in ('owner','designer')));

grant usage on schema stratum to authenticated;
grant select, insert, update, delete on all tables in schema stratum to authenticated;
grant execute on function stratum.auth_agency_id() to authenticated;
alter default privileges in schema stratum
    grant select, insert, update, delete on tables to authenticated;

-- Private bucket. These are other people's business photos; a public bucket is a
-- permanent unauthenticated URL to every one of them.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('stratum','stratum',false,52428800,
        array['image/png','image/jpeg','image/webp','image/tiff','application/zip'])
on conflict (id) do update set public = false;

-- Isolation is a property of the path, checked by the database, not of the
-- application remembering to prefix correctly.
create policy stratum_objects_rw on storage.objects for all to authenticated
    using (bucket_id = 'stratum'
           and (storage.foldername(name))[1] = stratum.auth_agency_id()::text)
    with check (bucket_id = 'stratum'
                and (storage.foldername(name))[1] = stratum.auth_agency_id()::text);

-- FOR UPDATE SKIP LOCKED is the whole trick: two workers cannot take the same row,
-- because the second steps over the locked row instead of waiting. A lease rather
-- than a "running" flag means a worker that dies does not strand its job.
create or replace function stratum.claim_job(worker text, lease_seconds integer default 120)
returns setof stratum.jobs language plpgsql security definer
set search_path = stratum, public as $$
begin
    return query
    update stratum.jobs j
    set status = 'running', attempts = j.attempts + 1, leased_by = worker,
        leased_until = now() + make_interval(secs => lease_seconds), updated_at = now()
    where j.id = (
        select c.id from stratum.jobs c
        where c.run_after <= now()
          and (c.status = 'queued' or (c.status = 'running' and c.leased_until < now()))
          and c.attempts < c.max_attempts
        order by c.run_after
        for update skip locked
        limit 1
    )
    returning j.*;
end $$;

revoke all on function stratum.claim_job(text, integer) from public, anon, authenticated;
