-- Brand kits keep their history, so an approved poster never changes colour.
--
-- Every poster records the brand-kit version it was made with
-- (spec.brand_kit_version). Until now only the CURRENT kit was stored, on
-- clients.brand, so changing it - which reading the colours from a logo makes a
-- one-click thing - would have recoloured every earlier poster the next time it was
-- drawn: a preview falling out of the disk cache, a download, a Canva export. The
-- client approved navy; they must receive navy.
--
-- So every kit a client has ever had is kept here, and the renderer asks for the
-- version the poster names.
--
-- - The history is written by the database, from triggers on clients, never by the
--   application. Every path that changes clients.brand - the API, the SQL editor, a
--   direct REST call - bumps the version and records the kit. A history the
--   application had to remember to write would have its first gap the first time
--   someone forgot.
-- - The version number is the database's, not the caller's. A new client starts at 1,
--   and every change of content is the previous version + 1 whatever the request
--   said, so a kit cannot be saved over an old version number. Saving the same
--   colours again is not a change and makes no new version.
-- - History is read-only to everyone signed in. Only the trigger writes it, as the
--   table owner, and only for the client row the caller was already allowed to write.

create table stratum.brand_kits (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    client_id uuid not null references stratum.clients (id) on delete cascade,
    version integer not null check (version >= 1),
    kit jsonb not null check (jsonb_typeof(kit) = 'object'),
    created_by uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now(),
    unique (client_id, version)
);
create index brand_kits_agency_idx on stratum.brand_kits (agency_id);

-- Kits that exist today become version 1 of their history (or whatever version they
-- already claim), so posters drafted before this migration still find their kit.
insert into stratum.brand_kits (agency_id, client_id, version, kit, created_at)
select agency_id, id, coalesce((brand ->> 'version')::integer, 1), brand, created_at
from stratum.clients
where jsonb_typeof(brand) = 'object'
on conflict (client_id, version) do nothing;

create or replace function stratum.stamp_brand_version()
returns trigger language plpgsql
set search_path = stratum, public as $$
begin
    if jsonb_typeof(new.brand) <> 'object' then
        raise exception 'a brand kit must be a JSON object';
    end if;
    if tg_op = 'INSERT' then
        new.brand := jsonb_set(new.brand, '{version}', '1'::jsonb);
    elsif (new.brand - 'version') is distinct from (old.brand - 'version') then
        new.brand := jsonb_set(
            new.brand, '{version}', to_jsonb(coalesce((old.brand ->> 'version')::integer, 1) + 1)
        );
    else
        new.brand := old.brand;
    end if;
    return new;
end $$;

-- SECURITY DEFINER because signed-in users cannot write brand_kits at all. It takes
-- agency_id and client_id from the clients row itself, which row-level security has
-- already checked the caller may write, so it cannot be pointed at another agency.
create or replace function stratum.record_brand_kit()
returns trigger language plpgsql security definer
set search_path = stratum, public as $$
begin
    if tg_op = 'INSERT' or new.brand is distinct from old.brand then
        insert into stratum.brand_kits (agency_id, client_id, version, kit, created_by)
        values (new.agency_id, new.id, (new.brand ->> 'version')::integer, new.brand, auth.uid());
    end if;
    return null;
end $$;

create trigger clients_brand_version
    before insert or update of brand on stratum.clients
    for each row execute function stratum.stamp_brand_version();

create trigger clients_brand_history
    after insert or update of brand on stratum.clients
    for each row execute function stratum.record_brand_kit();

alter table stratum.brand_kits enable row level security;

create policy brand_kits_read on stratum.brand_kits for select to authenticated
    using (agency_id = (select stratum.auth_agency_id()));

revoke all on stratum.brand_kits from public, anon;
grant select on stratum.brand_kits to authenticated;
revoke insert, update, delete, truncate on stratum.brand_kits from authenticated;
revoke all on function stratum.stamp_brand_version() from public, anon, authenticated;
revoke all on function stratum.record_brand_kit() from public, anon, authenticated;

notify pgrst, 'reload schema';
