-- M12b: the top three. A draft can lay out three different designs for one post;
-- the designer picks one. This table holds what was SHOWN - each option's poster,
-- its place on the screen and the probability the ranking policy had of showing it.
-- With decision_log (kind 'designer_pick', which already exists) it is the training
-- data for learning taste from picks (M15). Without the probability, a model
-- retrained on picks only ever learns to prefer what it already showed.
--
-- FOR LINE-BY-LINE REVIEW (AGENTS.md): new table, row-level security, grants.

create table stratum.post_options (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    post_id uuid not null references stratum.posts (id) on delete cascade,
    -- One draft's set of three. A new draft of the same post is a new batch.
    batch uuid not null,
    -- 1 = the ranking's best. Position is where it was shown (0 = first), which is
    -- shuffled, so a preference for "the first one" can be measured and removed.
    slot smallint not null check (slot between 1 and 3),
    position smallint not null check (position between 0 and 2),
    design_id text not null check (length(design_id) between 1 and 64),
    spec jsonb not null check (jsonb_typeof(spec) = 'object'),
    score double precision not null,
    propensity double precision not null check (propensity > 0 and propensity <= 1),
    explored boolean not null default false,
    -- Score parts (critic, framing cost, ...): for the model card and for debugging.
    detail jsonb not null default '{}'::jsonb check (jsonb_typeof(detail) = 'object'),
    created_by uuid default auth.uid() references auth.users (id) on delete set null,
    created_at timestamptz not null default now(),
    unique (batch, slot),
    unique (batch, position)
);
create index post_options_post_idx on stratum.post_options (post_id, created_at desc);

alter table stratum.post_options enable row level security;

-- The agency's own options, and only options for the agency's own posts. Checking the
-- post as well as agency_id stops a row that names our agency but someone else's post.
create policy post_options_read on stratum.post_options for select to authenticated
    using (agency_id = stratum.auth_agency_id());
create policy post_options_insert on stratum.post_options for insert to authenticated
    with check (
        agency_id = stratum.auth_agency_id()
        and exists (
            select 1 from stratum.posts p
            where p.id = post_id and p.agency_id = stratum.auth_agency_id()
        )
    );
-- Viewers look; owners and designers draft. Same rule as posts.
create policy post_options_viewers_cannot_write on stratum.post_options
    as restrictive for insert to authenticated
    with check (exists (select 1 from stratum.profiles p
                        where p.user_id = auth.uid() and p.role in ('owner','designer')));

-- What was shown never changes: no update, no delete (a post's options go with the
-- post, by the foreign key). The schema's default privileges grant everything to
-- authenticated, so the refusals are spelled out; anon gets nothing.
revoke all on stratum.post_options from anon, public;
grant select, insert on stratum.post_options to authenticated;
revoke update, delete, truncate on stratum.post_options from authenticated;
