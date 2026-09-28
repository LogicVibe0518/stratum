-- Who asked for a job: stamped by the database, never taken from the request.
--
-- The queue worker runs each job AS the person who asked for it. It signs a
-- short-lived token for that user and row-level security does the rest, so a job can
-- only ever see its own agency. That is safe only if nobody can choose whose name
-- goes on a job. If the requester came from the request body (it used to sit in
-- payload.requested_by), anyone in agency A could write a user id from agency B into
-- their own job and have the worker act as that user: a confused deputy. So the
-- column is filled here, from auth.uid(), whatever the insert said.
--
-- Rollback, if ever needed:
--   drop trigger jobs_stamp_requester on stratum.jobs;
--   drop function stratum.stamp_job_requester();
--   alter table stratum.jobs drop column requested_by;

alter table stratum.jobs
    add column requested_by uuid references auth.users (id) on delete set null;

create or replace function stratum.stamp_job_requester()
returns trigger language plpgsql
set search_path = stratum, public as $$
begin
    if tg_op = 'INSERT' then
        -- A signed-in caller is always the requester. With no caller at all (the
        -- service role, a migration, a test running as the table owner) the value
        -- given is kept: those callers are already trusted and have no uid to stamp.
        new.requested_by := coalesce(auth.uid(), new.requested_by);
    else
        -- Re-sending a failed post (jobs.requeue, run as the user) makes whoever
        -- re-sent it the requester. The worker's own writes - claim and settle,
        -- which carry no user - leave it exactly as it was.
        new.requested_by := coalesce(auth.uid(), old.requested_by);
    end if;
    return new;
end $$;

create trigger jobs_stamp_requester
    before insert or update on stratum.jobs
    for each row execute function stratum.stamp_job_requester();
