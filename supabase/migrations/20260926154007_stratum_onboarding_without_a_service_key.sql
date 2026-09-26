-- Joining an agency without the application ever holding a key that bypasses RLS.
--
-- The obvious way to create an agency for a new signup is a service-role key in the
-- backend. That key can read every row of every table, so if it ever leaks - a log
-- line, an error report, a stray env var in a screenshot - every agency's data goes
-- with it. These two functions do the same job with exactly the privilege needed and
-- no more, which means the app can run without such a key at all.

create table stratum.invites (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    email text not null check (position('@' in email) > 1),
    role text not null default 'designer' check (role in ('owner','designer','viewer')),
    invited_by uuid references auth.users (id) on delete set null,
    accepted_at timestamptz,
    created_at timestamptz not null default now(),
    unique (agency_id, email)
);
alter table stratum.invites enable row level security;
create policy invites_rw on stratum.invites for all to authenticated
    using (agency_id = stratum.auth_agency_id())
    with check (agency_id = stratum.auth_agency_id());
grant select, insert, update, delete on stratum.invites to authenticated;

-- Start a new agency. Refuses if the caller already belongs to one, so this cannot
-- be used to hop tenants.
create or replace function stratum.onboard(agency_name text)
returns uuid language plpgsql security definer set search_path = stratum, public as $$
declare
    new_agency uuid;
    invite stratum.invites%rowtype;
    caller_email text;
begin
    if auth.uid() is null then
        raise exception 'not signed in';
    end if;
    if exists (select 1 from stratum.profiles where user_id = auth.uid()) then
        raise exception 'already a member of an agency';
    end if;

    select email into caller_email from auth.users where id = auth.uid();

    -- An outstanding invitation wins over creating a new agency, so a colleague who
    -- signs up lands in the right place instead of starting an empty tenant.
    select * into invite from stratum.invites
        where lower(email) = lower(caller_email) and accepted_at is null
        order by created_at limit 1;

    if found then
        insert into stratum.profiles (user_id, agency_id, role)
            values (auth.uid(), invite.agency_id, invite.role);
        update stratum.invites set accepted_at = now() where id = invite.id;
        return invite.agency_id;
    end if;

    if agency_name is null or length(trim(agency_name)) = 0 then
        raise exception 'an agency name is required';
    end if;
    insert into stratum.agencies (name) values (trim(agency_name)) returning id into new_agency;
    insert into stratum.profiles (user_id, agency_id, role)
        values (auth.uid(), new_agency, 'owner');
    return new_agency;
end $$;

revoke all on function stratum.onboard(text) from public, anon;
grant execute on function stratum.onboard(text) to authenticated;

-- Who am I, and what may I do. One round trip instead of three.
create or replace function stratum.me()
returns jsonb language sql stable security definer set search_path = stratum, public as $$
    select coalesce(
        (select jsonb_build_object(
            'user_id', p.user_id, 'agency_id', p.agency_id,
            'role', p.role, 'agency_name', a.name)
         from stratum.profiles p
         join stratum.agencies a on a.id = p.agency_id
         where p.user_id = auth.uid()),
        '{}'::jsonb)
$$;
revoke all on function stratum.me() from public, anon;
grant execute on function stratum.me() to authenticated;
