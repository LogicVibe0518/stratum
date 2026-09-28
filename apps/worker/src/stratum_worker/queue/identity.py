"""The one place a credential is made: a short-lived token for the person who asked.

The worker serves every agency, so it must never be able to act for the wrong one by
accident. It does not do the work with a key that sees everything. For each claimed
job it signs a token that says "I am the person who asked for this", valid only while
the job's lease lasts, and talks to the database with that. Row-level security then
treats the worker exactly as it treats that person in their browser: their agency,
their role, nothing more.

Why the requester and not a robot account per agency:
- no extra users to create or keep in sync with the agencies table;
- the audit trail stays true: the draft is attributed to whoever pressed the button;
- someone removed from an agency can no longer run anything there, including jobs
  they queued before they left. The database simply stops finding their agency.

Why that is safe: `jobs.requested_by` is stamped by the database from auth.uid()
(migration 20260927174950). It used to travel in the job's payload, which the caller
writes. A worker that minted a token from that field would act as whoever the caller
named - someone in agency A could name a user in agency B. Never mint a credential
from a value a user can write.

Signing is HS256 with the project's JWT secret, done with the standard library. The
project signs its own sessions with ES256, but it still accepts tokens signed with
that secret (the publishable key the API uses is one). If the secret is ever revoked,
swap TokenSigner for one holding a private key imported into Supabase's JWT keys;
nothing else here changes.

Try it against the live project before trusting it:

    uv run python -m stratum_worker.queue.identity --check <user-uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stratum_worker.app.supa import Supa, SupabaseError
from stratum_worker.queue.jobs import Job
from stratum_worker.queue.ledger import KEY as SERVICE_KEY
from stratum_worker.queue.ledger import WORKER_ENV, worker_env

PUBLISHABLE = "SUPABASE_ANON_KEY"
JWT_SECRET = "SUPABASE_JWT_SECRET"  # noqa: S105 - the variable's name, not a secret
ROLE = "authenticated"

# Our clock and the database's disagree by a little. iat is backdated so a token is
# never "issued in the future", and exp carries some slack past the lease.
CLOCK_SKEW_S = 30
# Used only when a job arrives without a lease, which a claimed job never does.
FALLBACK_TTL_S = 180


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _compact(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


@dataclass(frozen=True)
class TokenSigner:
    secret: bytes
    issuer: str

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            # Supabase's own secrets are far longer. A short one is a typo or a
            # placeholder, and a weak HMAC key is a forgeable token.
            raise ValueError("the JWT secret is too short to be the project's real one")

    def sign(self, claims: dict[str, Any]) -> str:
        head = _b64url(_compact({"alg": "HS256", "typ": "JWT"}))
        body = _b64url(_compact(claims))
        mac = hmac.new(self.secret, f"{head}.{body}".encode("ascii"), hashlib.sha256)
        return f"{head}.{body}.{_b64url(mac.digest())}"

    def user_token(self, user_id: str, *, expires_at: int, now: float | None = None) -> str:
        issued = int(time.time() if now is None else now)
        if expires_at <= issued:
            raise PermissionError("refusing to sign a token that is already expired")
        return self.sign(
            {
                "aud": ROLE,
                "role": ROLE,
                "sub": user_id,
                "iss": self.issuer,
                "iat": issued - CLOCK_SKEW_S,
                "exp": expires_at,
                "is_anonymous": False,
            }
        )


def claims_of(token: str) -> dict[str, Any]:
    """The payload of a JWT, WITHOUT verifying it. For inspecting our own keys only."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _requester(job: Job) -> str:
    if not job.requested_by:
        raise PermissionError(
            f"job {job.id} has no requester, so there is no one to run it as. "
            "Jobs queued before migration 20260927174950, or by a user since deleted, "
            "have to be sent again."
        )
    try:
        return str(uuid.UUID(job.requested_by))
    except ValueError as exc:
        raise PermissionError(f"job {job.id} names a requester that is not a user id") from exc


def _expiry(job: Job, now: float) -> int:
    """The credential lives exactly as long as the lease, plus a little slack.

    leased_until comes from the database clock - the same clock that checks exp - so
    laptop clock drift cannot stretch or cut the token. A credential that outlived
    the lease would be a credential for a job this worker may no longer own.
    """
    if job.leased_until is None:
        return int(now) + FALLBACK_TTL_S
    return int(job.leased_until.timestamp()) + CLOCK_SKEW_S


@dataclass(frozen=True)
class ScopedClientFactory:
    """ClientFactory: a claimed job -> a client acting as that job's requester."""

    url: str
    publishable_key: str
    signer: TokenSigner
    bucket: str = "stratum"
    # Injected so tests do not depend on today's date: the first version of these
    # tests pinned a lease to a fixed morning and started failing that afternoon.
    clock: Callable[[], float] = time.time

    def __call__(self, job: Job) -> Supa:
        now = self.clock()
        token = self.signer.user_token(_requester(job), expires_at=_expiry(job, now), now=now)
        return Supa(
            base_rest=f"{self.url}/rest/v1",
            base_storage=f"{self.url}/storage/v1",
            # apikey is the PUBLISHABLE key: the gateway needs a key, and this one
            # grants nothing by itself. Authority comes only from the user token.
            api_key=self.publishable_key,
            access_token=token,
            bucket=self.bucket,
        )


def _refuse_privileged(key: str) -> None:
    """The publishable slot must never hold a key that bypasses row-level security."""
    if key.startswith("sb_secret_") or claims_of(key).get("role") == "service_role":
        raise RuntimeError(
            f"{PUBLISHABLE} holds the service-role key. The scoped client must present "
            "the publishable key; the service-role key belongs to the ledger alone."
        )


def from_env(env_file: Path = WORKER_ENV) -> ScopedClientFactory:
    """The factory `stratum-queue --factory stratum_worker.queue.identity:from_env` loads.

    Reads SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_JWT_SECRET from the worker's
    environment or .env.worker, never from the API's .env.
    """
    env = worker_env(env_file)
    url = env.get("SUPABASE_URL", "").rstrip("/")
    key = env.get(PUBLISHABLE, "")
    secret = env.get(JWT_SECRET, "")
    missing = [
        name
        for name, value in (("SUPABASE_URL", url), (PUBLISHABLE, key), (JWT_SECRET, secret))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"the worker needs {', '.join(missing)} in its environment or in {env_file}. "
            "The JWT secret is in the Supabase dashboard under Settings, JWT Keys, "
            "Legacy JWT secret."
        )
    _refuse_privileged(key)
    if secret == env.get(SERVICE_KEY):
        raise RuntimeError(f"{JWT_SECRET} is set to the service-role key, not the JWT secret")
    return ScopedClientFactory(
        url=url,
        publishable_key=key,
        signer=TokenSigner(secret=secret.encode("utf-8"), issuer=f"{url}/auth/v1"),
    )


# --- a smoke test against the live project -------------------------------------------


async def _check(user_id: str) -> dict[str, Any]:
    from stratum_worker.app.supa import close_pool

    factory = from_env()
    probe = Job.model_validate(
        {
            "id": "identity-check",
            "agency_id": "",
            "kind": "check",
            "payload": {},
            "status": "running",
            "attempts": 1,
            "max_attempts": 1,
            "run_after": "2026-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "requested_by": user_id,
        }
    )
    try:
        me = await factory(probe).rpc("me")
    except SupabaseError as exc:
        if exc.status in (401, 403):
            raise SystemExit(
                f"the database rejected the token ({exc.detail}). The JWT secret in "
                ".env.worker is wrong, or the project no longer accepts it."
            ) from exc
        raise
    finally:
        await close_pool()
    return me if isinstance(me, dict) else {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m stratum_worker.queue.identity",
        description="Sign a token for one user and ask the database who that is.",
    )
    parser.add_argument("--check", metavar="USER_ID", required=True)
    args = parser.parse_args(argv)
    me = asyncio.run(_check(args.check))
    if not me.get("agency_id"):
        raise SystemExit(
            "the token was accepted, but that user belongs to no agency, so every job "
            "they queued would see nothing"
        )
    who, agency, role = me["user_id"], me.get("agency_name"), me["role"]
    print(f"ok: the worker would act as {who} in {agency!r} ({role})")


if __name__ == "__main__":
    main()
