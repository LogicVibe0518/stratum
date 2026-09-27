"""A durable job queue on Postgres: stratum.jobs plus stratum.claim_job.

Crash safety comes from the lease expiring, never from a status flag. A worker that
dies mid-job simply stops renewing nothing; once leased_until passes, claim_job hands
the job to the next worker that asks. There is no heartbeat and no sweeper.
"""

from stratum_worker.queue.jobs import ClientFactory, Job, describe, enqueue

__all__ = ["ClientFactory", "Job", "describe", "enqueue"]
