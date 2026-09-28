"""A free, local stand-in for Supabase, so the queue can be tested and load-tested
without a network, a database server or a model bill.

One SQLite file holds every table as JSON rows, and a folder holds storage. Several
processes can share it, which is what makes the kill -9 test real: the workers are
separate processes, and one of them really dies.

Three things are copied faithfully because the tests depend on them:
- SqliteLedger.claim is claim_job's WHERE clause, line for line. SQLite serialises
  writers, so one UPDATE ... WHERE id = (SELECT ... LIMIT 1) is as exclusive as
  FOR UPDATE SKIP LOCKED - it waits where Postgres would skip, which changes speed,
  not correctness. The Postgres function itself is tested in test_queue_contract.py.
- FakeSupa is scoped to one agency the way row-level security scopes a user: it
  cannot see, write or store anything belonging to another agency.
- Brand-kit versions behave as the brand_kit_history triggers make them: a client's
  first kit is version 1, every change of colours is the next number and is kept in
  brand_kits, and nobody writes brand_kits directly. The triggers themselves are
  tested in test_brand_kits_contract.py.

Everything else is only as much PostgREST as service.draft uses.
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from stratum_worker.app.settings import Settings
from stratum_worker.app.supa import JSON, Supa, SupabaseError
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.registry import offline_router
from stratum_worker.queue.jobs import Job

Clock = Callable[[], datetime]

UNIQUE: dict[str, tuple[str, ...]] = {
    "clients": ("agency_id", "slug"),
    "assets": ("agency_id", "sha256"),
    "posts": ("client_id", "slug"),
    "post_versions": ("post_id", "version"),
    "renders": ("post_version_id", "aspect"),
    "jobs": ("agency_id", "idempotency_key"),
    "brand_kits": ("client_id", "version"),
}
# Written only by the database's own triggers; a signed-in user may only read them.
READ_ONLY = {"brand_kits"}

DEFAULTS: dict[str, dict[str, Any]] = {
    "clients": {"trade": "", "archived": False},
    "assets": {"kind": "photo", "flags": [], "quality": {}},
    "posts": {"status": "draft", "words": None, "cost_usd": 0, "occasion": "", "photo_note": ""},
    "post_versions": {"note": ""},
    "steps": {"provider": "", "model": "", "cached": False, "cost_usd": 0, "latency_ms": 0},
    "jobs": {
        "status": "queued",
        "attempts": 0,
        "max_attempts": 3,
        "last_error": "",
        "leased_until": None,
        "leased_by": None,
        "result": None,
    },
}
TOUCHED = {"posts", "jobs"}  # tables with an updated_at column


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    """Fixed-width UTC, so string comparison in SQLite is time comparison."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    db.execute("pragma journal_mode=wal")
    db.execute("pragma busy_timeout=30000")
    db.execute(
        "create table if not exists rows (tbl text not null, id text not null, "
        "agency_id text, doc text not null, primary key (tbl, id))"
    )
    return db


def _matches(doc: dict[str, Any], filters: dict[str, str]) -> bool:
    for column, expression in filters.items():
        op, _, want = expression.partition(".")
        if "->>" in column:
            # PostgREST's JSON path filter, e.g. brand->>version=eq.3
            outer, _, key = column.partition("->>")
            inner = doc.get(outer)
            have = inner.get(key) if isinstance(inner, dict) else None
        else:
            have = doc.get(column)
        if op == "is" and want == "null":
            if have is not None:
                return False
            continue
        if op != "eq":
            raise NotImplementedError(f"fake PostgREST supports eq and is.null, not {expression!r}")
        text = json.dumps(have) if isinstance(have, bool) else str(have)
        if text != want:
            return False
    return True


def _next_brand(old: Any, new: Any) -> tuple[Any, bool]:
    """clients.brand as stamp_brand_version leaves it, and whether it is a new version."""
    if not isinstance(new, dict):
        raise SupabaseError(400, "a brand kit must be a JSON object")
    if old is None:
        return {**new, "version": 1}, True

    def content(brand: Any) -> Any:
        return (
            {k: v for k, v in brand.items() if k != "version"} if isinstance(brand, dict) else brand
        )

    if content(new) == content(old):
        return old, False
    return {**new, "version": int(old.get("version", 1)) + 1}, True


def _sort_key(value: object) -> tuple[int, float, str]:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return (0, float(value), "")
    return (1, 0.0, "" if value is None else str(value))


class FakeSupa(Supa):
    """The PostgREST and Storage calls service.draft makes, for one agency."""

    def __init__(self, world: World, agency_id: str) -> None:
        super().__init__(
            base_rest="fake://rest", base_storage="fake://storage", api_key="", access_token=""
        )
        self._world = world
        self.agency_id = agency_id

    def _rows(self, db: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
        found = db.execute(
            "select doc from rows where tbl = ? and agency_id = ?", (table, self.agency_id)
        )
        return [json.loads(doc) for (doc,) in found]

    def _write(self, db: sqlite3.Connection, table: str, doc: dict[str, Any]) -> None:
        db.execute(
            "insert or replace into rows (tbl, id, agency_id, doc) values (?, ?, ?, ?)",
            (table, doc["id"], doc.get("agency_id"), json.dumps(doc)),
        )

    def _record_brand(self, db: sqlite3.Connection, client: dict[str, Any], now: str) -> None:
        brand = client["brand"]
        self._write(
            db,
            "brand_kits",
            {
                "id": str(uuid.uuid4()),
                "agency_id": client["agency_id"],
                "client_id": client["id"],
                "version": brand["version"],
                "kit": brand,
                "created_by": None,
                "created_at": now,
            },
        )

    async def select(self, table: str, **params: str) -> list[JSON]:
        order = params.pop("order", "")
        limit = params.pop("limit", "")
        params.pop("select", None)
        with self._world.db() as db:
            rows = [r for r in self._rows(db, table) if _matches(r, params)]
        for part in reversed([p for p in order.split(",") if p]):
            column, _, direction = part.partition(".")
            rows.sort(key=lambda r: _sort_key(r.get(column)), reverse=direction == "desc")
        return rows[: int(limit)] if limit else rows

    async def insert(
        self, table: str, rows: list[JSON] | JSON, *, upsert: bool = False, on_conflict: str = ""
    ) -> list[JSON]:
        batch = rows if isinstance(rows, list) else [rows]
        out: list[JSON] = []
        now = stamp(self._world.clock())
        if table in READ_ONLY:
            raise SupabaseError(403, f"permission denied for table {table}")
        with self._world.db() as db:
            db.execute("begin immediate")
            try:
                existing = self._rows(db, table)
                for row in batch:
                    if row.get("agency_id") != self.agency_id:
                        raise SupabaseError(403, "new row violates row-level security policy")
                    doc = {"id": str(uuid.uuid4()), "created_at": now, **DEFAULTS.get(table, {})}
                    if table in TOUCHED:
                        doc["updated_at"] = now
                    if table == "jobs":
                        doc["run_after"] = now
                    doc.update(row)
                    key = UNIQUE.get(table)
                    clash = next(
                        (e for e in existing if key and all(e.get(k) == doc.get(k) for k in key)),
                        None,
                    )
                    if clash is not None:
                        if not upsert:
                            raise SupabaseError(409, f"duplicate key value violates unique {key}")
                        doc = {**clash, **row}
                    versioned = False
                    if table == "clients":
                        before = clash["brand"] if clash is not None else None
                        doc["brand"], versioned = _next_brand(before, doc.get("brand"))
                    self._write(db, table, doc)
                    if versioned:
                        self._record_brand(db, doc, now)
                    existing.append(doc)
                    out.append(doc)
                db.execute("commit")
            except BaseException:
                db.execute("rollback")
                raise
        return out

    async def update(self, table: str, values: JSON, **filters: str) -> list[JSON]:
        now = stamp(self._world.clock())
        changes = {k: (now if v in ("now", "now()") else v) for k, v in values.items()}
        out: list[JSON] = []
        if table in READ_ONLY:
            raise SupabaseError(403, f"permission denied for table {table}")
        with self._world.db() as db:
            db.execute("begin immediate")
            try:
                for row in self._rows(db, table):
                    if _matches(row, filters):
                        versioned = False
                        if table == "clients" and "brand" in changes:
                            before = row["brand"]
                            row.update(changes)
                            row["brand"], versioned = _next_brand(before, changes["brand"])
                        else:
                            row.update(changes)
                        self._write(db, table, row)
                        if versioned:
                            self._record_brand(db, row, now)
                        out.append(row)
                db.execute("commit")
            except BaseException:
                db.execute("rollback")
                raise
        return out

    async def rpc(self, name: str, args: JSON | None = None) -> Any:
        raise SupabaseError(404, f"the fake has no function {name}")

    def _path(self, path: str) -> Path:
        if path.split("/", 1)[0] != self.agency_id:
            raise SupabaseError(403, "storage path is outside this agency")
        return self._world.root / "storage" / path

    async def upload(self, path: str, data: bytes, content_type: str) -> str:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    async def download(self, path: str) -> bytes:
        target = self._path(path)
        if not target.is_file():
            raise SupabaseError(404, "object not found")
        return target.read_bytes()

    async def signed_url(self, path: str, expires_in: int = 3600) -> str:
        return self._path(path).as_uri()


class SqliteLedger:
    """claim_job and the fenced settle write, over the same SQLite file."""

    def __init__(self, world: World) -> None:
        self._world = world

    async def claim(self, worker: str, lease_seconds: int) -> Job | None:
        now = self._world.clock()
        until = stamp(now + timedelta(seconds=lease_seconds))
        with self._world.db() as db:
            got = db.execute(
                """
                update rows set doc = json_set(doc,
                    '$.status', 'running',
                    '$.attempts', json_extract(doc, '$.attempts') + 1,
                    '$.leased_by', :worker,
                    '$.leased_until', :until,
                    '$.updated_at', :now)
                where tbl = 'jobs' and id = (
                    select id from rows c where c.tbl = 'jobs'
                      and json_extract(c.doc, '$.run_after') <= :now
                      and (json_extract(c.doc, '$.status') = 'queued'
                           or (json_extract(c.doc, '$.status') = 'running'
                               and json_extract(c.doc, '$.leased_until') < :now))
                      and json_extract(c.doc, '$.attempts') < json_extract(c.doc, '$.max_attempts')
                    order by json_extract(c.doc, '$.run_after') limit 1)
                returning doc
                """,
                {"worker": worker, "until": until, "now": stamp(now)},
            ).fetchone()
        return Job.model_validate(json.loads(got[0])) if got else None

    async def settle(self, job: Job, worker: str, values: dict[str, Any]) -> bool:
        patch = {**values, "updated_at": stamp(self._world.clock())}
        with self._world.db() as db:
            db.execute("begin immediate")
            found = db.execute(
                "select doc from rows where tbl = 'jobs' and id = ?", (job.id,)
            ).fetchone()
            doc = json.loads(found[0]) if found else {}
            if doc.get("leased_by") != worker or doc.get("status") != "running":
                db.execute("commit")
                return False
            doc.update(patch)
            db.execute(
                "update rows set doc = ? where tbl = 'jobs' and id = ?", (json.dumps(doc), job.id)
            )
            db.execute("commit")
        return True


class _Db:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._db = connect(self._path)
        return self._db

    def __exit__(self, *_: object) -> None:
        if self._db is not None:
            self._db.close()


class World:
    """One fake Supabase: a SQLite file plus a storage folder next to it."""

    def __init__(self, path: Path, clock: Clock = utcnow) -> None:
        self.path = path
        self.root = path.parent
        self.clock = clock
        connect(path).close()

    def db(self) -> _Db:
        return _Db(self.path)

    def factory(self, agency_id: str) -> Supa:
        """A client that sees one agency only, the way row-level security scopes a user."""
        return FakeSupa(self, agency_id)

    def scoped(self, job: Job) -> Supa:
        """The test double for ClientFactory.

        The real factory acts as job.requested_by and lets the database find their
        agency. The stand-in has no users, so it scopes by the job's agency directly;
        who may appear in requested_by is the database's rule, tested against real
        Postgres in test_queue_contract.
        """
        return FakeSupa(self, job.agency_id)

    def ledger(self) -> SqliteLedger:
        return SqliteLedger(self)

    def jobs(self) -> list[dict[str, Any]]:
        """Every job, all agencies. For tests and the load test's report only."""
        with self.db() as db:
            return [json.loads(d) for (d,) in db.execute("select doc from rows where tbl='jobs'")]

    def settings(self, repo_root: Path | None = None) -> Settings:
        root = repo_root or Path.cwd()
        return Settings(
            asset_cache=self.root / "assets",
            templates_dir=root / "templates",
            repo_root=root,
        )


# --- a free gateway and a seeded client ----------------------------------------------

FAKE_COPY: dict[str, object] = {
    "headline": "Boiler service, sorted",
    "offer_detail": "20% off every service booked this month",
    "cta": "Call 0161 496 0000",
    "caption": "Winter is coming and your boiler knows it. Gas Safe 000000.",
    "hashtags": ["#manchesterplumber", "#boilerservice"],
    "alt_text": "An engineer beside a wall-mounted boiler",
}


def gateway(model_delay_s: float = 0.0, *, budget: Budget | None = None) -> Gateway:
    """Every task answered by FakeProvider: real routing and budget, no network, $0."""
    router = offline_router([FAKE_COPY] * 100_000, delay_s=model_delay_s)
    return Gateway(router=router, budget=budget or Budget(daily_cap_usd=2.0))


def _photo_png() -> bytes:
    """A sharp, bright, full-size synthetic photo - it passes intake without a real one."""
    import numpy as np
    from PIL import Image

    y, x = np.mgrid[0:1350, 0:1080]
    edges = 40 * (((x // 6) + (y // 6)) % 2)  # hard edges read as sharp to intake
    base = 130 + 50 * np.sin(x / 9.0) * np.cos(y / 11.0) + edges
    rgb = np.stack([base, base * 0.8 + 30, 255 - base * 0.5], axis=-1).clip(0, 255)
    out = io.BytesIO()
    Image.fromarray(rgb.astype("uint8"), "RGB").save(out, "PNG")
    return out.getvalue()


async def seed(
    world: World, repo_root: Path, client_dir: str = "kowalski-plumbing"
) -> tuple[str, str]:
    """One agency with one fictional client, a logo and a photo. Returns (agency, client)."""
    from stratum_worker.app import service

    agency_id = str(uuid.uuid4())
    folder = repo_root / "clients" / client_dir
    meta = json.loads((folder / "client.json").read_text(encoding="utf-8"))
    brand = json.loads((folder / "brand.json").read_text(encoding="utf-8"))
    supa = world.factory(agency_id)
    client = (
        await supa.insert(
            "clients",
            {
                "agency_id": agency_id,
                "slug": meta["id"],
                "name": meta["name"],
                "trade": meta.get("trade", ""),
                "brand": brand,
                "rules": meta["rules"],
            },
        )
    )[0]
    ctx = service.Context(
        supa=supa, settings=world.settings(repo_root), gateway=gateway(), agency_id=agency_id
    )
    await service.store_image(ctx, client["id"], (folder / "logo.png").read_bytes(), kind="logo")
    await service.store_image(ctx, client["id"], await asyncio.to_thread(_photo_png), kind="photo")
    return agency_id, str(client["id"])
