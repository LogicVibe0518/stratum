"""Collect public-domain photos for the photo-type classifier (stage 6).

Searches Openverse for CC0 and public-domain photos only, downloads up to PER_CLASS
per class into STRATUM_TYPES/<class>/, shrinks each to 1024 px, and writes
manifest.csv: where every photo came from and under which licence.

The search words are only a first guess at the label. You check every photo by eye
afterwards (stage 6, step 2) - that check is the labelling.

    uv run --no-project --with-requirements requirements.txt python fetch_types.py
    uv run ... python fetch_types.py --dry-run     # search only, download nothing
    uv run ... python fetch_types.py --top-up      # round 2: TOP_UP searches, new photos only

Client photos are the final test, so any download that is a copy of one is skipped.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import photolab as lab
from PIL import Image

API = "https://api.openverse.org/v1/images/"
LICENCES = {"cc0", "pdm"}  # CC0 and Public Domain Mark: no conditions at all
PER_CLASS = 70  # you will throw some away
PAGE_SIZE = 20  # the most Openverse gives without an account
PAGES = 2
BETWEEN_SEARCHES = 3.5  # seconds: Openverse allows 20 searches a minute without an account
SEARCH_TIMEOUT = 90  # seconds: without an account a search often takes 15-30 s to answer
SEARCH_TRIES = 3
LONGEST_SIDE = 1024
USER_AGENT = "stratum-photo-types/1.0 (student project; one-off fetch)"

# A few searches per class, so one search word does not decide what the class looks like.
SEARCHES = {
    "people_posing": [
        "team portrait",
        "group photo staff",
        "smiling worker portrait",
        "family portrait",
    ],
    "people_working": [
        "worker repairing",
        "carpenter working",
        "chef cooking kitchen",
        "painter painting wall",
        "mechanic repairing",
    ],
    "place": [
        "living room interior",
        "kitchen interior",
        "house exterior",
        "garden lawn",
        "bathroom interior",
        "shop interior",
    ],
    "object": [
        "tools on table",
        "product close up",
        "plate of food",
        "hand tools",
        "kitchen appliance",
    ],
    "vehicle": ["van", "pickup truck", "delivery truck", "work vehicle"],
    "graphic": ["poster", "flyer", "infographic", "sign with text", "screenshot"],
}
# Graphics are not photographs, so that class searches every kind of image.
PHOTOS_ONLY = {label: label != "graphic" for label in SEARCHES}

# Round 2 (20 Oct), after looking at round 1: these searches came back on topic far more often.
# --top-up adds to what is there and never downloads a photo twice.
TOP_UP = {
    "people_working": [
        "man working",
        "woman working",
        "construction worker",
        "barista",
        "farmer working",
    ],
    "people_posing": ["smiling woman", "smiling man", "team photo"],
    "vehicle": ["parked car", "truck"],
}
TOP_UP_PER_CLASS = 60
SAME_PHOTO = 30  # fingerprint bits apart (of 256): client copies measured 0-9, other photos 76+


def get(url: str, timeout: float = 30) -> bytes:
    # https only: a search result could name any address, and a file: or ftp: address
    # must never be opened (the S310 lint rule exists for exactly this).
    if not url.startswith("https://"):
        raise ValueError(f"not an https address: {url[:60]}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def search(words: str, page: int, photos_only: bool) -> list[dict]:
    params = {
        "q": words,
        "license": ",".join(sorted(LICENCES)),
        "page_size": PAGE_SIZE,
        "page": page,
        "mature": "false",
    }
    if photos_only:
        params["category"] = "photograph"
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(1, SEARCH_TRIES + 1):
        try:
            return json.loads(get(url, SEARCH_TIMEOUT)).get("results", [])
        except urllib.error.HTTPError as error:  # e.g. asking for a page past the last one
            if error.code >= 500 and attempt < SEARCH_TRIES:  # Openverse's own hiccup: retry
                time.sleep(15 * attempt)
                continue
            print(f"    {words!r} page {page}: HTTP {error.code}, skipped")
            return []
        except (TimeoutError, urllib.error.URLError) as error:  # slow or no answer: wait, retry
            print(f"    {words!r} page {page}: no answer ({error}), try {attempt}/{SEARCH_TRIES}")
            time.sleep(15 * attempt)
    return []  # no answer three times: skip this search; the other searches still count


def candidates(searches: dict[str, list[str]], seen: set[str]) -> dict[str, list[dict]]:
    """Search results per class, each photo once (the first class that found it keeps it)."""
    found: dict[str, list[dict]] = {label: [] for label in searches}
    for label, all_words in searches.items():
        per_search = []
        for words in all_words:
            rows = []
            for page in range(1, PAGES + 1):
                got = search(words, page, PHOTOS_ONLY[label])
                rows += got
                time.sleep(BETWEEN_SEARCHES)
                if len(got) < PAGE_SIZE:
                    break  # that was the last page
            per_search.append(rows)
            print(f"  {label:15} {words!r}: {len(rows)} results")
        # take one result from each search in turn, so every search word is represented
        for row in (r for group in itertools.zip_longest(*per_search) for r in group if r):
            if row["id"] in seen or row["license"] not in LICENCES:
                continue
            seen.add(row["id"])
            row["query_class"] = label
            found[label].append(row)
    return found


def shrink(data: bytes) -> bytes:
    image = Image.open(io.BytesIO(data))
    image.draft("RGB", (LONGEST_SIDE, LONGEST_SIDE))  # cheap first shrink for big JPEGs
    image = image.convert("RGB")
    image.thumbnail((LONGEST_SIDE, LONGEST_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return out.getvalue()


def fingerprint(image: Image.Image) -> int:
    """256 bits: is each pixel brighter than its right-hand neighbour? Survives resizing."""
    pixels = image.convert("L").resize((17, 16)).tobytes()
    bits = 0
    for i in range(256):
        row, col = divmod(i, 16)
        if pixels[row * 17 + col] > pixels[row * 17 + col + 1]:
            bits |= 1 << i
    return bits


def client_fingerprints() -> list[int]:
    """The clients' photos are the final test: none of them may end up in training."""
    clients = sorted({case.client for case in lab.load_cases()})
    return [fingerprint(Image.open(p)) for c in clients for p in lab.photos_of(c)]


def download(
    rows: list[dict], folder: Path, want: int, hashes: set[str], clients: list[int], round_: str
) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    kept = []
    for row in rows:
        if len(kept) >= want:
            break
        if (row.get("width") or 0) * (row.get("height") or 0) > 40_000_000:
            continue  # a 40-megapixel scan is not worth the download
        try:
            data = shrink(get(row["url"]))
        except Exception as error:  # a dead link or a broken file: skip it, say so
            print("    skipped", row["id"], type(error).__name__)
            continue
        sha = hashlib.sha256(data).hexdigest()
        if sha in hashes:
            continue
        hashes.add(sha)
        mark = fingerprint(Image.open(io.BytesIO(data)))
        if any((mark ^ client).bit_count() <= SAME_PHOTO for client in clients):
            print("    skipped", row["id"], "- a client photo (the client test must stay unseen)")
            continue
        name = f"ov-{row['id'][:8]}.jpg"
        (folder / name).write_bytes(data)
        kept.append(
            {
                "file": name,
                "query_class": row["query_class"],
                "openverse_id": row["id"],
                "licence": row["license"],
                "licence_version": row.get("license_version") or "",
                "creator": row.get("creator") or "",
                "source": row.get("source") or "",
                "landing_page": row.get("foreign_landing_url") or "",
                "sha256": sha,
                "round": round_,
            }
        )
    return kept


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    top_up = "--top-up" in sys.argv
    manifest: list[dict] = []
    if top_up:  # keep round 1's rows; the new photos are added to them
        with open(lab.TYPES / "manifest.csv", newline="") as f:
            manifest = [{**row, "round": row.get("round") or "1"} for row in csv.DictReader(f)]
    searches = TOP_UP if top_up else {label: SEARCHES[label] for label in lab.CLASSES}
    print("searching Openverse (CC0 and public domain only)...")
    found = candidates(searches, seen={row["openverse_id"] for row in manifest})
    for label, rows in found.items():
        print(f"{label:15} {len(rows)} candidates")
    if dry_run:
        return
    hashes = {row["sha256"] for row in manifest}
    clients = client_fingerprints()
    want, round_ = (TOP_UP_PER_CLASS, "2") if top_up else (PER_CLASS, "1")
    for label in searches:
        print("downloading", label)
        manifest += download(found[label], lab.TYPES / label, want, hashes, clients, round_)
    with open(lab.TYPES / "manifest.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    print(len(manifest), "photos in", lab.TYPES)


if __name__ == "__main__":
    main()
