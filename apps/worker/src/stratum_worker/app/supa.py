"""A small typed client for PostgREST and Supabase Storage.

Written by hand rather than pulling in the vendor SDK for two reasons: it is about a
hundred lines against a plain REST API, and it makes the security property obvious at
a glance - every request carries the CALLER'S token, never a service key, so the
database applies that user's row-level security to everything we do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

JSON = dict[str, Any]
Prefer = Literal["return=representation", "return=minimal", "resolution=merge-duplicates"]


# One connection pool for the whole process, shared by every request.
#
# Opening `httpx.AsyncClient()` per call means a fresh TCP connection and a fresh TLS
# handshake every time - about a third of a second each to another continent. A
# single quick change makes roughly twenty calls, which was six seconds of pure
# handshaking. The caller's token travels in a header, not in the connection, so
# sharing the pool between users is safe.
_POOL: httpx.AsyncClient | None = None


def pool() -> httpx.AsyncClient:
    global _POOL
    if _POOL is None or _POOL.is_closed:
        _POOL = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _POOL


async def close_pool() -> None:
    global _POOL
    if _POOL is not None and not _POOL.is_closed:
        await _POOL.aclose()
    _POOL = None


class SupabaseError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"supabase {status}: {detail}")


@dataclass
class Supa:
    base_rest: str
    base_storage: str
    api_key: str
    access_token: str
    bucket: str = "stratum"
    schema: str = "stratum"
    timeout_s: float = 30.0

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.access_token}",
            "Accept-Profile": self.schema,
            "Content-Profile": self.schema,
        }
        headers.update(extra or {})
        return headers

    async def _send(self, client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
        response = await client.send(request)
        if response.status_code >= 400:
            raise SupabaseError(response.status_code, response.text[:500])
        return response

    # --- rows ---------------------------------------------------------------------

    async def select(self, table: str, **params: str) -> list[JSON]:
        client = pool()
        request = client.build_request(
            "GET", f"{self.base_rest}/{table}", headers=self._headers(), params=params
        )
        body = (await self._send(client, request)).json()
        return body if isinstance(body, list) else []

    async def insert(
        self,
        table: str,
        rows: list[JSON] | JSON,
        *,
        upsert: bool = False,
        on_conflict: str = "",
    ) -> list[JSON]:
        """Insert, or merge when a row with the same key is already there.

        PostgREST needs BOTH the merge-duplicates preference and the names of the
        columns that make a row "the same". Without on_conflict it only knows the
        primary key, so re-uploading the same photo comes back as a duplicate-key
        error instead of quietly being the same asset.
        """
        prefer = "return=representation"
        params: dict[str, str] = {}
        if upsert:
            prefer += ",resolution=merge-duplicates"
            if on_conflict:
                params["on_conflict"] = on_conflict
        client = pool()
        request = client.build_request(
            "POST",
            f"{self.base_rest}/{table}",
            headers=self._headers({"Prefer": prefer, "Content-Type": "application/json"}),
            params=params,
            json=rows,
        )
        body = (await self._send(client, request)).json()
        return body if isinstance(body, list) else [body]

    async def update(self, table: str, values: JSON, **filters: str) -> list[JSON]:
        client = pool()
        request = client.build_request(
            "PATCH",
            f"{self.base_rest}/{table}",
            headers=self._headers(
                {"Prefer": "return=representation", "Content-Type": "application/json"}
            ),
            params=filters,
            json=values,
        )
        body = (await self._send(client, request)).json()
        return body if isinstance(body, list) else [body]

    async def rpc(self, name: str, args: JSON | None = None) -> Any:
        client = pool()
        request = client.build_request(
            "POST",
            f"{self.base_rest}/rpc/{name}",
            headers=self._headers({"Content-Type": "application/json"}),
            json=args or {},
        )
        return (await self._send(client, request)).json()

    # --- files --------------------------------------------------------------------

    async def upload(self, path: str, data: bytes, content_type: str) -> str:
        client = pool()
        request = client.build_request(
            "POST",
            f"{self.base_storage}/object/{self.bucket}/{path}",
            headers=self._headers({"Content-Type": content_type, "x-upsert": "true"}),
            content=data,
            timeout=120.0,
        )
        await self._send(client, request)
        return path

    async def download(self, path: str) -> bytes:
        client = pool()
        request = client.build_request(
            "GET",
            f"{self.base_storage}/object/{self.bucket}/{path}",
            headers=self._headers(),
            timeout=120.0,
        )
        return (await self._send(client, request)).content

    async def signed_url(self, path: str, expires_in: int = 3600) -> str:
        """A link that works for an hour and then stops.

        The bucket is private, so this is the only way a browser can show a poster.
        Short-lived on purpose: a URL that never expires is a permanent public link to
        a client's photo the moment anyone forwards it.
        """
        client = pool()
        request = client.build_request(
            "POST",
            f"{self.base_storage}/object/sign/{self.bucket}/{path}",
            headers=self._headers({"Content-Type": "application/json"}),
            json={"expiresIn": expires_in},
        )
        body = (await self._send(client, request)).json()
        signed = str(body.get("signedURL") or body.get("signedUrl") or "")
        return f"{self.base_storage.removesuffix('/storage/v1')}/storage/v1{signed}"
