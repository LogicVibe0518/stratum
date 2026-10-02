-- Taste Lab (taste plan, step 1): screens of designs for one real post, shown to a
-- person who picks the one they would send the client. Critic v1 learned to tell a
-- broken design from a fine one; taste between good designs only comes from people
-- choosing, and this table holds what they were shown. Their choices go in
-- decision_log (kind 'designer_pick', detail.source = 'lab'), which already exists
-- and already lets any member of the agency append their own decisions.
--
-- FOR LINE-BY-LINE REVIEW (AGENTS.md): new table, row-level security, grants.
-- The same rules as stratum.post_options (20261001081217), which were reviewed and
-- smoke-tested live on 1 Oct.

create table stratum.lab_screens (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    post_id uuid not null references stratum.posts (id) on delete cascade,
    -- Shown to every rater, so agreement between people (the ceiling any model can
    -- reach) can be measured. The rest are shown to as few raters as possible.
    shared boolean not null default false,
    -- The designs, in their stored order: design id, family, bucket, rank, score and
    -- the filled poster spec. 2 to 6 of them.
    designs jsonb not null check (
        jsonb_typeof(designs) = 'array'
        and jsonb_array_length(designs) between 2 and 6
    ),
    detail jsonb not null default '{}'::jsonb check (jsonb_typeof(detail) = 'object'),
    created_by uuid default auth.uid() references auth.users (id) on delete set null,
    created_at timestamptz not null default now()
);
create index lab_screens_agency_idx on stratum.lab_screens (agency_id, created_at);
create index lab_screens_post_idx on stratum.lab_screens (post_id);

alter table stratum.lab_screens enable row level security;

-- The agency's own screens, and only for the agency's own posts.
create policy lab_screens_read on stratum.lab_screens for select to authenticated
    using (agency_id = stratum.auth_agency_id());
create policy lab_screens_insert on stratum.lab_screens for insert to authenticated
    with check (
        agency_id = stratum.auth_agency_id()
        and exists (
            select 1 from stratum.posts p
            where p.id = post_id and p.agency_id = stratum.auth_agency_id()
        )
    );
-- Owners and designers make screens; anyone in the agency (viewers too) can rate them,
-- because rating writes decision_log, not this table.
create policy lab_screens_viewers_cannot_write on stratum.lab_screens
    as restrictive for insert to authenticated
    with check (exists (select 1 from stratum.profiles p
                        where p.user_id = auth.uid() and p.role in ('owner','designer')));

-- What was shown never changes: no update, no delete (screens go with their post).
revoke all on stratum.lab_screens from anon, public;
grant select, insert on stratum.lab_screens to authenticated;
revoke update, delete, truncate on stratum.lab_screens from authenticated;
