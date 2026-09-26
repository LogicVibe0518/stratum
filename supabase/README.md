# Database

The migrations here are the record of what is deployed. Apply them in filename order.

Project: `piuwwigzpofwrzymmrsw` (eu-west-2, London). Its sibling `shandil-dev` is an
unrelated live application - never point anything here at it.

Applying by hand from the SQL editor is fine; if you use the Supabase CLI:

    supabase link --project-ref piuwwigzpofwrzymmrsw
    supabase db push

After adding or changing any function, PostgREST needs its schema cache rebuilt:

    notify pgrst, 'reload schema';

## What matters in here

- **Every table carries `agency_id`** and every policy compares it to
  `stratum.auth_agency_id()`, in both USING and WITH CHECK. USING alone would let a
  caller insert a row into someone else's agency.
- **No service-role key is needed by the API.** `stratum.onboard` and `stratum.me` are
  narrow SECURITY DEFINER functions; there is no credential in the web process that can
  read across tenants.
- **`stratum.claim_job` uses FOR UPDATE SKIP LOCKED and a lease.** Crash safety comes
  from the lease expiring, never from a status flag. Do not add a heartbeat or a
  "stuck job" sweeper - that would be reimplementing the lease, badly.
