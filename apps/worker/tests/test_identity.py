"""The scoped client factory: what the worker's credential says, and what it refuses.

These pin the properties a reviewer should check by hand in identity.py:
- the token names the job's requester and nobody else, and is verifiable with the secret;
- it dies with the lease, measured on the database's clock;
- the client presents the publishable key, never the service-role key;
- a job without a trustworthy requester is refused before any work, permanently;
- with a private key (the deployed worker since the Railway incident), the token is
  ES256, names the key's kid, verifies with the public half and says the same thing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from stratum_worker.app.supa import Supa
from stratum_worker.queue.fake import World
from stratum_worker.queue.identity import (
    CLOCK_SKEW_S,
    KeySigner,
    ScopedClientFactory,
    TokenSigner,
    claims_of,
    from_env,
    main,
    new_key,
)
from stratum_worker.queue.jobs import Job
from stratum_worker.queue.worker import Deps, QueueWorker

SECRET = b"s" * 40
URL = "https://example.supabase.co"
PUBLISHABLE = "sb_publishable_example"
USER = str(uuid.uuid4())
LEASE_END = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
# "Now" for the factory: three minutes before the lease ends, the moment of a claim.
CLAIMED_AT = (LEASE_END - timedelta(minutes=3)).timestamp()


def _job(**changes: Any) -> Job:
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "agency_id": str(uuid.uuid4()),
        "kind": "draft_post",
        "payload": {"post_id": "p"},
        "status": "running",
        "attempts": 1,
        "max_attempts": 3,
        "leased_by": "w",
        "leased_until": LEASE_END,
        "run_after": LEASE_END - timedelta(minutes=5),
        "created_at": LEASE_END - timedelta(minutes=5),
        "updated_at": LEASE_END - timedelta(minutes=3),
        "requested_by": USER,
    }
    row.update(changes)
    return Job.model_validate(row)


def _factory() -> ScopedClientFactory:
    return ScopedClientFactory(
        url=URL,
        publishable_key=PUBLISHABLE,
        signer=TokenSigner(SECRET, f"{URL}/auth/v1"),
        clock=lambda: CLAIMED_AT,
    )


def _verify(token: str, secret: bytes) -> dict[str, Any]:
    head, body, sig = token.split(".")
    expected = hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
    given = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    assert hmac.compare_digest(expected, given), "signature does not verify"
    header = json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))
    assert header == {"alg": "HS256", "typ": "JWT"}
    return claims_of(token)


def test_the_token_names_the_requester_and_verifies_with_the_secret() -> None:
    client = _factory()(_job())
    claims = _verify(client.access_token, SECRET)
    assert claims["sub"] == USER
    assert claims["role"] == claims["aud"] == "authenticated"
    assert claims["iss"] == f"{URL}/auth/v1"


def test_a_token_signed_with_another_secret_does_not_verify() -> None:
    token = _factory()(_job()).access_token
    with pytest.raises(AssertionError, match="does not verify"):
        _verify(token, b"x" * 40)


def test_the_token_dies_with_the_lease_on_the_database_clock() -> None:
    claims = claims_of(_factory()(_job()).access_token)
    assert claims["exp"] == int(LEASE_END.timestamp()) + CLOCK_SKEW_S


def test_a_later_lease_gives_a_later_expiry() -> None:
    later = LEASE_END + timedelta(minutes=10)
    first = claims_of(_factory()(_job()).access_token)["exp"]
    second = claims_of(_factory()(_job(leased_until=later)).access_token)["exp"]
    assert second - first == 600


def test_the_client_presents_the_publishable_key_and_nothing_else() -> None:
    client = _factory()(_job())
    assert isinstance(client, Supa)
    assert client.api_key == PUBLISHABLE
    assert client.base_rest == f"{URL}/rest/v1"
    assert client.base_storage == f"{URL}/storage/v1"


@pytest.mark.parametrize("requester", [None, "", "not-a-uuid", "1; drop table jobs"])
def test_a_job_without_a_real_requester_is_refused(requester: str | None) -> None:
    with pytest.raises(PermissionError):
        _factory()(_job(requested_by=requester))


def test_a_token_already_expired_is_never_signed() -> None:
    past = datetime(2020, 1, 1, tzinfo=UTC)
    with pytest.raises(PermissionError, match="already expired"):
        _factory()(_job(leased_until=past))


def test_a_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="too short"):
        TokenSigner(b"short", "issuer")


async def test_the_worker_fails_a_job_with_no_requester_without_doing_any_work(
    tmp_path: Path,
) -> None:
    world = World(tmp_path / "db.sqlite")
    agency = str(uuid.uuid4())
    await world.factory(agency).insert(
        "jobs", {"agency_id": agency, "kind": "draft_post", "payload": {"post_id": "p"}}
    )
    ran: list[str] = []

    async def handler(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        ran.append(job.id)
        return {}

    loop = QueueWorker(
        ledger=world.ledger(),
        factory=_factory(),
        deps=Deps(settings=world.settings(), gateway=None),  # type: ignore[arg-type]
        handlers={"draft_post": handler},
    )
    assert await loop.run_once()
    [row] = world.jobs()
    assert ran == []
    # Permanent on the first attempt: retrying cannot conjure a requester.
    assert row["status"] == "failed" and row["attempts"] == 1
    assert "no requester" in row["last_error"]


# --- from_env ---------------------------------------------------------------------------


def _legacy_key(role: str) -> str:
    def part(value: dict[str, Any]) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256'})}.{part({'role': role, 'iss': 'supabase'})}.sig"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_JWT_SECRET",
        "SUPABASE_JWT_PRIVATE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _write(path: Path, **values: str) -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return path


def test_from_env_builds_a_factory_from_the_worker_file(
    tmp_path: Path, clean_env: pytest.MonkeyPatch
) -> None:
    env = _write(
        tmp_path / ".env.worker",
        SUPABASE_URL=URL + "/",
        SUPABASE_ANON_KEY=_legacy_key("anon"),
        SUPABASE_JWT_SECRET="j" * 40,
    )
    factory = replace(from_env(env), clock=lambda: CLAIMED_AT)
    assert factory.url == URL
    assert claims_of(factory(_job()).access_token)["sub"] == USER


def test_from_env_names_what_is_missing(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    env = _write(tmp_path / ".env.worker", SUPABASE_URL=URL)
    with pytest.raises(RuntimeError, match="SUPABASE_ANON_KEY, SUPABASE_JWT_PRIVATE_KEY"):
        from_env(env)


@pytest.mark.parametrize("key", [_legacy_key("service_role"), "sb_secret_abc123"])
def test_from_env_refuses_a_service_key_in_the_publishable_slot(
    tmp_path: Path, clean_env: pytest.MonkeyPatch, key: str
) -> None:
    env = _write(
        tmp_path / ".env.worker",
        SUPABASE_URL=URL,
        SUPABASE_ANON_KEY=key,
        SUPABASE_JWT_SECRET="j" * 40,
    )
    with pytest.raises(RuntimeError, match="service-role key"):
        from_env(env)


def test_from_env_refuses_the_service_key_posing_as_the_jwt_secret(
    tmp_path: Path, clean_env: pytest.MonkeyPatch
) -> None:
    service = "k" * 40
    env = _write(
        tmp_path / ".env.worker",
        SUPABASE_URL=URL,
        SUPABASE_ANON_KEY=PUBLISHABLE,
        SUPABASE_JWT_SECRET=service,
        SUPABASE_SERVICE_ROLE_KEY=service,
    )
    with pytest.raises(RuntimeError, match="not the JWT secret"):
        from_env(env)


# --- ES256: a private key the project trusts --------------------------------------------


def _verify_es256(token: str, jwk: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    def raw(text: str) -> bytes:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    head, body, sig = token.split(".")
    public = ec.EllipticCurvePublicNumbers(
        int.from_bytes(raw(jwk["x"]), "big"), int.from_bytes(raw(jwk["y"]), "big"), ec.SECP256R1()
    ).public_key()
    signature = raw(sig)
    assert len(signature) == 64, "a JWS ES256 signature is r and s, 32 bytes each"
    der = encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    public.verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if not
    return json.loads(raw(head)), claims_of(token)


def _key_factory(jwk: dict[str, str]) -> ScopedClientFactory:
    return ScopedClientFactory(
        url=URL,
        publishable_key=PUBLISHABLE,
        signer=KeySigner(jwk, f"{URL}/auth/v1"),
        clock=lambda: CLAIMED_AT,
    )


def test_a_new_key_is_a_private_p256_jwk_with_a_fresh_kid() -> None:
    one, two = new_key(), new_key()
    assert set(one) == {"kty", "kid", "crv", "x", "y", "d"}
    assert (one["kty"], one["crv"]) == ("EC", "P-256")
    assert one["kid"] != two["kid"] and one["d"] != two["d"]
    uuid.UUID(one["kid"])


def test_the_key_token_names_its_kid_and_verifies_with_the_public_half() -> None:
    jwk = new_key()
    token = _key_factory(jwk)(_job()).access_token
    header, claims = _verify_es256(token, jwk)
    assert header == {"alg": "ES256", "kid": jwk["kid"], "typ": "JWT"}
    # Exactly what the secret-signed token says: same person, same lease, same role.
    legacy = claims_of(_factory()(_job()).access_token)
    assert claims == legacy
    assert claims["sub"] == USER and claims["role"] == "authenticated"


def test_a_key_token_does_not_verify_with_another_key() -> None:
    from cryptography.exceptions import InvalidSignature

    token = _key_factory(new_key())(_job()).access_token
    with pytest.raises(InvalidSignature):
        _verify_es256(token, new_key())


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"d": ""}, "no d"),
        ({"crv": "P-384"}, "curve P-256"),
        ({"kty": "RSA"}, "curve P-256"),
    ],
)
def test_a_broken_key_is_refused(change: dict[str, str], said: str) -> None:
    with pytest.raises(ValueError, match=said):
        KeySigner({**new_key(), **change}, "issuer")


def test_halves_of_two_different_keys_are_refused() -> None:
    mixed = {**new_key(), "d": new_key()["d"]}
    with pytest.raises(ValueError, match="do not match"):
        KeySigner(mixed, "issuer")


def test_from_env_prefers_the_private_key(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    jwk = new_key()
    env = _write(
        tmp_path / ".env.worker",
        SUPABASE_URL=URL,
        SUPABASE_ANON_KEY=PUBLISHABLE,
        SUPABASE_JWT_PRIVATE_KEY=json.dumps(jwk),
        SUPABASE_JWT_SECRET="j" * 40,
    )
    factory = replace(from_env(env), clock=lambda: CLAIMED_AT)
    header, claims = _verify_es256(factory(_job()).access_token, jwk)
    assert header["kid"] == jwk["kid"] and claims["sub"] == USER


def test_from_env_refuses_a_key_that_is_not_json(
    tmp_path: Path, clean_env: pytest.MonkeyPatch
) -> None:
    env = _write(
        tmp_path / ".env.worker",
        SUPABASE_URL=URL,
        SUPABASE_ANON_KEY=PUBLISHABLE,
        SUPABASE_JWT_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----",
    )
    with pytest.raises(RuntimeError, match="not a JSON key"):
        from_env(env)


def test_the_new_key_command_prints_one_key_and_how_to_use_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["--new-key"])
    out = capsys.readouterr().out
    jwk = json.loads(out.strip().splitlines()[-1])
    assert set(jwk) == {"kty", "kid", "crv", "x", "y", "d"}
    assert f"kid: {jwk['kid']}" in out
    assert "SUPABASE_JWT_PRIVATE_KEY" in out and "never commit" in out.lower()
    KeySigner(jwk, "issuer")  # the printed key loads
