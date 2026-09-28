# M8 — The scoped client factory

**Status:** written by Claude, 28 Sep 2026. Flagged for line-by-line human review
(AGENTS.md: token and migration code). Migration applied to project
`piuwwigzpofwrzymmrsw` on 28 Sep as `20260927174950_jobs_requested_by`.

## What changed

- `supabase/migrations/20260927174950_jobs_requested_by.sql` — `jobs.requested_by`,
  filled by a trigger from `auth.uid()`, whatever the caller sent.
- `queue/identity.py` — `ScopedClientFactory`: for each claimed job, sign an HS256
  token for `job.requested_by`, expiring with the job's lease, and return a `Supa`
  that presents the publishable key plus that token. `from_env()` is what
  `stratum-queue --factory stratum_worker.queue.identity:from_env` loads.
- `ClientFactory` now takes the whole `Job`, not an agency id.
- The API no longer puts `requested_by` in the payload; the worker no longer reads it.
- AGENTS.md rules 4 and 10 and the stack line now describe the queue as it is.

## Five things a reviewer should be able to repeat back

1. **The worker acts as the person who asked.** Row-level security then scopes it to
   their agency and role, exactly as in their browser. No per-agency robot users.
2. **Never mint a credential from a value a user can write.** The requester used to be
   in the payload, which the caller writes; anyone in agency A could have named a user
   in agency B. The database now stamps it (tested against real Postgres).
3. **The credential dies with the lease,** measured on the database clock
   (`leased_until`), so drift on the worker's machine cannot stretch it.
4. **The publishable key goes in `apikey`, never the service key.** `from_env` refuses
   a service key in that slot, and refuses the service key posing as the JWT secret.
5. **A job with no requester fails permanently before any work,** because retrying
   cannot produce one.

## Risks and what would break it

- It depends on the project still accepting tokens signed with the legacy JWT secret.
  If that secret is revoked, the `--check` smoke test fails loudly; the fix is a
  signer holding a private key imported into Supabase's JWT keys.
- The worker process now holds two cross-tenant secrets (service key, JWT secret). The
  confinement is in the code paths (`ledger.py`, `identity.py`), and becomes a process
  boundary once the worker deploys as its own service.
- Jobs queued before the migration have no requester and will fail with a clear
  message; re-sending the post fixes it. There were none on the live database.

## Verify it

    uv run python -m stratum_worker.queue.identity --check <your user id>
    uv run stratum-queue --factory stratum_worker.queue.identity:from_env --concurrency 2

Then make a post in the web app with "run in the background" ticked.
