# M10a-2 — The API and the worker are two machines

**Status:** written by Claude, 29 Sep 2026, from the second live test. No migration,
no auth or token code, no new dependency.

## What went wrong on the live site

After #20 was merged and deployed, the Krishna before/after post still failed with
the old message: "This design needs a real photo for before, after." Two separate
things were wrong, one hiding the other.

1. **The cloud worker could not open any uploaded file.** Its log for job
   `b4a900a1` shows `AssetNotFound: 25bb3381…`, which is the client's logo, on
   attempts 1 to 3. Photos and logos are uploaded through the API, which ingests
   them onto its own disk and copies them to Supabase Storage. On Railway the
   worker is a different machine with a different disk. `local_path` was meant to
   fetch from storage when a file is missing ("the store on disk is a cache in front
   of object storage"), but it first asked the store for the file's `meta.json`,
   which raised before the fetch could run. On a laptop the API and the worker share
   one disk, so this never happened and no test covered it.
2. **A laptop worker, started before #20, was still running.** It shares the live
   queue, took the job's second attempt (`leased_by =
   Rushils-MacBook-Air.local:46055:…`), and failed with the pre-#20 message. That is
   the error the page showed, so it looked as if #20 had not worked.

The same bug would also have hit the API after every redeploy, because a redeploy
gives it an empty disk: brand colours from the logo, previews and the Canva file
all read assets through `local_path`.

## What changed

- **`local_path` fetches what this machine never ingested.** An asset ingested here
  is read from its folder, as before. Anything else is downloaded once from storage
  into `_fetched/<sha>.png` and read from there afterwards.
- **A fetched file is checked against its id.** The id is the SHA-256 of the pixels,
  so the check is exact. A damaged or different picture in storage raises
  `CorruptImage` with a sentence; it is never drawn on a poster.
- **Fetched files live apart from ingested ones.** A folder named by a sha is a
  complete asset only with its `meta.json`. Writing a lone `full.png` there would
  make a later ingest of the same photo fail, so fetched copies get their own folder.
  Writes are atomic, so the worker's four job slots can fetch the same file at once.
- **Every lease names the build that took it.** The worker id is now
  `host:pid:<build>:<random>:<slot>`, where the build is the first seven characters of
  Railway's `RAILWAY_GIT_COMMIT_SHA`, or `local` anywhere else. The start-up line
  says it too. A job taken by a stale laptop worker now says `local` in
  `stratum.jobs.leased_by`.

## Five things a reviewer should be able to repeat back

1. **Storage is the record; each disk is a cache.** Any machine can draw any poster
   from the bucket alone.
2. **The bug hid because one laptop is one disk.** The new tests give the API and
   the worker separate disks over one database and one bucket, as on Railway.
3. **A fetched file must be the picture its id names**, checked on the pixels, or it
   is refused.
4. **A fetched file never looks like a half-ingested asset**, so it cannot block a
   later upload of the same photo.
5. **A lease says which code ran the job**: a commit on Railway, `local` on a laptop.

## Tests

`test_two_machines.py` (8):

- the live case: two ~350 px photos uploaded through the API, a before/after drafted
  by a worker with an empty disk, in all four sizes;
- a second read comes from the local disk, with one download in total;
- the API reads a logo's colours after a redeploy has wiped its disk;
- a different picture stored under an asset's id is refused, and nothing is kept;
- a damaged stored copy is refused;
- a fetched copy does not block ingesting the same photo later;
- a lease names the Railway build, or says `local`.

Mutation checks: with the old `local_path`, 6 of the 8 fail with the live error
(`AssetNotFound`). With the fetched copy written into the asset's own folder, the
re-ingest test fails. Without the pixel check, the different-picture test fails.

## Operations note

Run only one kind of worker against the live project. With the Railway worker
running, stop any laptop worker (`Ctrl+C` in its tab). If a laptop worker is needed,
point it at a local or branch database.
