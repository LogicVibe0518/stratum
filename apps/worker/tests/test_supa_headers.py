"""Which headers carry which credential (Supabase's API keys, October 2026).

What a reviewer should be able to repeat back:
- a legacy key is a JWT and is still sent as both apikey and bearer, as before;
- a new API key (sb_publishable_..., sb_secret_...) is not a JWT: it goes in apikey only,
  and a client with no user token sends no Authorization header at all;
- a user's token always travels as the bearer, whatever the apikey.
"""

from __future__ import annotations

import pytest
from stratum_worker.app.supa import Supa, is_api_key

LEGACY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.sig"
SIGNED_IN = "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJ1In0.sig"


def _client(api_key: str, token: str) -> Supa:
    return Supa(
        base_rest="https://x/rest/v1",
        base_storage="https://x/storage/v1",
        api_key=api_key,
        access_token=token,
    )


def test_a_legacy_key_is_sent_both_ways() -> None:
    headers = _client(LEGACY, LEGACY)._headers()
    assert headers["apikey"] == LEGACY and headers["Authorization"] == f"Bearer {LEGACY}"


@pytest.mark.parametrize("key", ["sb_secret_abc", "sb_publishable_abc"])
def test_a_new_api_key_alone_goes_in_apikey_only(key: str) -> None:
    headers = _client(key, key)._headers()
    assert headers["apikey"] == key and "Authorization" not in headers


def test_a_user_token_is_the_bearer_beside_a_publishable_key() -> None:
    headers = _client("sb_publishable_abc", SIGNED_IN)._headers()
    assert headers["apikey"] == "sb_publishable_abc"
    assert headers["Authorization"] == f"Bearer {SIGNED_IN}"


def test_extra_headers_still_apply() -> None:
    headers = _client("sb_secret_abc", "sb_secret_abc")._headers(
        {"Prefer": "return=representation"}
    )
    assert headers["Prefer"] == "return=representation" and "Authorization" not in headers


def test_what_counts_as_an_api_key() -> None:
    assert is_api_key("sb_secret_x") and is_api_key("sb_publishable_x")
    assert not is_api_key(LEGACY) and not is_api_key("") and not is_api_key("sb_other_x")
