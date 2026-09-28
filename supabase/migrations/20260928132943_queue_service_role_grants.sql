-- Let the queue worker's service-role key do the two things it exists for.
--
-- ledger.py claims a job (rpc claim_job) and settles it (PATCH jobs, fenced on
-- leased_by and status) through PostgREST. PostgREST runs those requests as the
-- service_role database role, which passes row-level security but, like any role,
-- still needs grants - and had none on this schema, so a live worker got "permission
-- denied for schema stratum" on its first claim. The contract tests had run as the
-- database owner and could not see it; they now run both statements as service_role.
--
-- Exactly what ledger.py does, nothing more: no other table, no other function, and
-- only the columns settle() writes. A job's payload, agency and requester stay out of
-- this key's reach even though it can read every agency's queue.

grant usage on schema stratum to service_role;
grant execute on function stratum.claim_job(text, integer) to service_role;
grant select on stratum.jobs to service_role;
grant update (status, result, last_error, run_after, leased_until, updated_at)
    on stratum.jobs to service_role;

notify pgrst, 'reload schema';
