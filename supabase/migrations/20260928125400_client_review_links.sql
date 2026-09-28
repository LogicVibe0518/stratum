-- Client review links, and the log every learning model in Stratum trains on.
--
-- A client approves a post, or asks for a change, from a link on their phone. No
-- account, no password: the link itself is the credential. So it is treated like one.
--
-- - Only a SHA-256 of the token is stored. A leaked database row, backup or log line
--   cannot be replayed as a link; the token exists only in the URL the designer sent.
-- - The anonymous caller never touches a table. Two SECURITY DEFINER functions are
--   the whole surface: review_open reads one review, review_decide records one
--   verdict. Each checks the token, the expiry and that the item belongs to that
--   review, and neither says WHY a link failed, so a guess learns nothing.
-- - A client's verdict can only be written by review_decide. Designers can read
--   verdicts, never write them, so the approval history is evidence, not a claim.
--
-- decision_log is append-only and is the training data: which options were shown
-- and how likely each was to be shown, which one the designer picked, what the
-- client said. It is written from the first day of use, because a decision that was
-- not recorded when it happened can never be recovered.

create table stratum.reviews (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    client_id uuid not null references stratum.clients (id) on delete cascade,
    token_hash text not null unique check (token_hash ~ '^[a-f0-9]{64}$'),
    title text not null default '' check (length(title) <= 200),
    expires_at timestamptz not null,
    revoked_at timestamptz,
    created_by uuid default auth.uid() references auth.users (id) on delete set null,
    created_at timestamptz not null default now(),
    -- A link that lives for months is a permanent key to a client's unposted work.
    check (expires_at <= created_at + interval '31 days')
);
create index reviews_client_idx on stratum.reviews (client_id, created_at desc);

-- The clock and the author are the database's, not the caller's: otherwise a link
-- could be back-dated past the 31-day rule, or credited to someone else. And a
-- link's token never changes once issued.
create or replace function stratum.stamp_review()
returns trigger language plpgsql
set search_path = stratum, public as $$
begin
    if tg_op = 'INSERT' then
        new.created_at := now();
        new.created_by := coalesce(auth.uid(), new.created_by);
    else
        new.created_at := old.created_at;
        new.created_by := old.created_by;
        new.token_hash := old.token_hash;
        new.agency_id := old.agency_id;
        new.client_id := old.client_id;
        -- Revoking is for good: a link sent to a lost phone must not come back to life.
        if old.revoked_at is not null then
            new.revoked_at := old.revoked_at;
        end if;
    end if;
    return new;
end $$;

create trigger reviews_stamp
    before insert or update on stratum.reviews
    for each row execute function stratum.stamp_review();

create table stratum.review_items (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    review_id uuid not null references stratum.reviews (id) on delete cascade,
    post_id uuid not null references stratum.posts (id) on delete cascade,
    version_id uuid not null references stratum.post_versions (id) on delete cascade,
    position integer not null default 0,
    caption text not null default '' check (length(caption) <= 5000),
    -- {"4:5": "<signed url>", ...}. Snapshots taken when the link was made: the client
    -- approves exactly the pixels they saw, even if the designer edits afterwards.
    previews jsonb not null default '{}'::jsonb check (jsonb_typeof(previews) = 'object'),
    unique (review_id, post_id)
);
create index review_items_post_idx on stratum.review_items (post_id);

create table stratum.review_decisions (
    id uuid primary key default gen_random_uuid(),
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    review_item_id uuid not null references stratum.review_items (id) on delete cascade,
    verdict text not null check (verdict in ('approved', 'changes')),
    comment text not null default '' check (length(comment) <= 2000),
    -- Where on the poster the client tapped: fractions of width and height.
    pin jsonb check (pin is null or jsonb_typeof(pin) = 'object'),
    created_at timestamptz not null default now()
);
create index review_decisions_item_idx on stratum.review_decisions (review_item_id, created_at desc);

create table stratum.decision_log (
    id bigint generated always as identity primary key,
    agency_id uuid not null references stratum.agencies (id) on delete cascade,
    client_id uuid references stratum.clients (id) on delete set null,
    post_id uuid references stratum.posts (id) on delete set null,
    version_id uuid references stratum.post_versions (id) on delete set null,
    kind text not null check (kind in (
        'client_verdict', 'designer_pick', 'edit_request',
        'suggestion_accepted', 'suggestion_ignored'
    )),
    detail jsonb not null default '{}'::jsonb check (jsonb_typeof(detail) = 'object'),
    -- The person who acted. NULL means the client, through a review link, and only
    -- review_decide can write that.
    actor uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now()
);
create index decision_log_agency_idx on stratum.decision_log (agency_id, created_at);
create index decision_log_post_idx on stratum.decision_log (post_id);

alter table stratum.reviews enable row level security;
alter table stratum.review_items enable row level security;
alter table stratum.review_decisions enable row level security;
alter table stratum.decision_log enable row level security;

create policy reviews_rw on stratum.reviews for all to authenticated
    using (agency_id = stratum.auth_agency_id())
    with check (agency_id = stratum.auth_agency_id());
create policy review_items_rw on stratum.review_items for all to authenticated
    using (agency_id = stratum.auth_agency_id())
    with check (agency_id = stratum.auth_agency_id());

-- Read-only for the agency: a verdict is the client's, written by review_decide alone.
create policy review_decisions_read on stratum.review_decisions for select to authenticated
    using (agency_id = stratum.auth_agency_id());

-- Append-only. A person may log their own actions, never a client's verdict.
create policy decision_log_read on stratum.decision_log for select to authenticated
    using (agency_id = stratum.auth_agency_id());
create policy decision_log_append on stratum.decision_log for insert to authenticated
    with check (agency_id = stratum.auth_agency_id()
                and actor = auth.uid()
                and kind <> 'client_verdict');

grant select, insert, update, delete on stratum.reviews, stratum.review_items to authenticated;
grant select on stratum.review_decisions to authenticated;
grant select, insert on stratum.decision_log to authenticated;
revoke insert, update, delete on stratum.review_decisions from authenticated;
revoke update, delete on stratum.decision_log from authenticated;

-- The review a token opens, or nothing. Never says whether it was wrong, expired or
-- revoked: the answer would help someone guessing.
create or replace function stratum.review_by_token(token text)
returns stratum.reviews language sql stable security definer
set search_path = stratum, public as $$
    select r.* from stratum.reviews r
    where token is not null
      and length(token) between 32 and 128
      and r.token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex')
      and r.revoked_at is null
      and r.expires_at > now()
$$;
revoke all on function stratum.review_by_token(text) from public, anon, authenticated;

create or replace function stratum.review_open(token text)
returns jsonb language plpgsql stable security definer
set search_path = stratum, public as $$
declare
    r stratum.reviews%rowtype;
begin
    select * into r from stratum.review_by_token(token);
    if r.id is null then
        return null;
    end if;
    return jsonb_build_object(
        'title', r.title,
        'expires_at', r.expires_at,
        'client', (select c.name from stratum.clients c where c.id = r.client_id),
        'agency', (select a.name from stratum.agencies a where a.id = r.agency_id),
        'items', coalesce((
            select jsonb_agg(jsonb_build_object(
                'id', i.id,
                'position', i.position,
                'caption', i.caption,
                'previews', i.previews,
                'decision', (
                    select jsonb_build_object(
                        'verdict', d.verdict, 'comment', d.comment, 'pin', d.pin,
                        'at', d.created_at)
                    from stratum.review_decisions d
                    where d.review_item_id = i.id
                    order by d.created_at desc, d.id desc
                    limit 1)
            ) order by i.position, i.id)
            from stratum.review_items i where i.review_id = r.id
        ), '[]'::jsonb)
    );
end $$;

create or replace function stratum.review_decide(
    token text, item_id uuid, verdict text, comment text default '', pin jsonb default null
)
returns jsonb language plpgsql security definer
set search_path = stratum, public as $$
declare
    r stratum.reviews%rowtype;
    i stratum.review_items%rowtype;
    said text := left(trim(coalesce(comment, '')), 2000);
    kept jsonb;
    d stratum.review_decisions%rowtype;
begin
    select * into r from stratum.review_by_token(token);
    if r.id is null then
        raise exception 'this review link has expired or is not valid' using errcode = '28000';
    end if;
    select * into i from stratum.review_items where id = item_id and review_id = r.id;
    if not found then
        raise exception 'that post is not part of this review' using errcode = '28000';
    end if;
    if verdict is null or verdict not in ('approved', 'changes') then
        raise exception 'the verdict must be approved or changes' using errcode = '22023';
    end if;
    if verdict = 'changes' and said = '' then
        raise exception 'say what should change' using errcode = '22023';
    end if;
    -- A pin is where the client tapped: fractions of the width and height of the
    -- format they were looking at. "aspect" is optional, and must be one this post was
    -- shown in.
    -- coalesce(..., false): a pin with no "x" makes jsonb_typeof return NULL, and
    -- NOT (NULL) is NULL, which IF treats as false - so without it, a pin missing a
    -- coordinate slipped through. A test caught it; three-valued logic is a trap.
    -- CASE, not AND, because SQL does not promise to evaluate AND left to right: the
    -- numeric casts must only run once both values are known to be numbers.
    if pin is not null and not coalesce(
        case
            when jsonb_typeof(pin) = 'object'
             and jsonb_typeof(pin -> 'x') = 'number'
             and jsonb_typeof(pin -> 'y') = 'number'
             and (coalesce(jsonb_typeof(pin -> 'aspect'), 'null') = 'null'
                  or (jsonb_typeof(pin -> 'aspect') = 'string'
                      and i.previews ? (pin ->> 'aspect')))
            then (pin ->> 'x')::numeric between 0 and 1 and (pin ->> 'y')::numeric between 0 and 1
            else false
        end,
        false
    ) then
        raise exception 'a pin is {"x": 0-1, "y": 0-1} on a format this post was shown in'
            using errcode = '22023';
    end if;
    -- Keep only what was checked. This function is reachable straight through the
    -- REST API, so anything else sent inside a pin never reaches a table.
    if pin is not null then
        kept := jsonb_strip_nulls(jsonb_build_object(
            'x', (pin ->> 'x')::numeric,
            'y', (pin ->> 'y')::numeric,
            'aspect', pin ->> 'aspect'));
    end if;

    insert into stratum.review_decisions (agency_id, review_item_id, verdict, comment, pin)
    values (r.agency_id, i.id, verdict, said, kept)
    returning * into d;

    update stratum.posts
    set status = case when verdict = 'approved' then 'approved' else 'review' end,
        updated_at = now()
    where id = i.post_id and agency_id = r.agency_id;

    insert into stratum.decision_log (agency_id, client_id, post_id, version_id, kind, detail, actor)
    values (r.agency_id, r.client_id, i.post_id, i.version_id, 'client_verdict',
            jsonb_build_object('verdict', verdict, 'comment', said, 'pin', kept,
                               'review_id', r.id, 'item_id', i.id),
            null);

    return jsonb_build_object('verdict', d.verdict, 'comment', d.comment, 'pin', d.pin,
                              'at', d.created_at);
end $$;

revoke all on function stratum.review_open(text) from public;
revoke all on function stratum.review_decide(text, uuid, text, text, jsonb) from public;
grant execute on function stratum.review_open(text) to anon, authenticated;
grant execute on function stratum.review_decide(text, uuid, text, text, jsonb) to anon, authenticated;

-- Postgres lets PUBLIC execute every new function unless told otherwise. Until now that
-- did not matter, because the anonymous role could not see this schema at all. It is
-- about to, so close it first: anon may run the two review functions above and nothing
-- else. (A per-schema ALTER DEFAULT PRIVILEGES cannot take away a global default, so
-- the guard for functions added later is a test instead:
-- test_the_anonymous_role_can_run_the_two_review_functions_and_nothing_else fails
-- until a new function's migration says `revoke all ... from public`.)
revoke all on function stratum.auth_agency_id() from public, anon;
grant execute on function stratum.auth_agency_id() to authenticated;
revoke all on function stratum.stamp_job_requester() from public, anon, authenticated;
revoke all on function stratum.stamp_review() from public, anon, authenticated;

-- The anonymous role may now resolve names in this schema. It still has no privilege
-- on any table, so the two functions above are all it can reach.
grant usage on schema stratum to anon;

notify pgrst, 'reload schema';
