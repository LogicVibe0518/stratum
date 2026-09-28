# C1b — Polish from the C1 review, and "Open in Canva"

**Branch:** `feat/ui-canva` · **Who:** Cursor · **Reviewer:** Claude · **Needs:** M14 merged.

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C1b-polish-and-canva.md, and implement it exactly.
Only edit apps/api/src/stratum_api/static/index.html and
apps/api/tests/test_ui_safety.py. Do not change any Python in apps/ or packages/.
Keep esc() or textContent for everything shown. Run make check. Do not commit or push.
Finish with a report: what you changed, what you could not do, anything you guessed.
```

## 1. "Open in Canva" on the Post screen

M14 added `GET /api/posts/{id}/canva.pptx?aspect=4:5`, which returns one size of the
post as a PowerPoint file with every layer editable. Canva imports it layer by layer.

- In the Post screen header, next to "Download all", add a secondary button
  **Open in Canva** that opens a small menu listing the post's sizes (from
  `post.aspects`), e.g. `4:5 feed`, `1:1 square`, `9:16 story`, `16:9 wide`.
- Choosing a size fetches that file with `authHeader()`, the way preview images are
  fetched (retry once on 401), and saves it with the filename from the
  `Content-Disposition` header, through a temporary object URL revoked straight after.
- While it downloads, the menu item shows a spinner; on failure, a toast with the
  server's sentence.
- Under the menu, one muted line: "In Canva: Create a design → Import file."

## 2. Three small fixes from the C1 review

1. When a post has no 9:16 render, the Story frame keeps its 9:16 shape (390 × 693)
   with the message centred inside, instead of collapsing to one line.
2. On phones, the Back control in the top bar sits on the same baseline as the
   title next to it.
3. `save()` wraps `localStorage.setItem` in try/catch, like `load()` does, so a
   browser that blocks storage (Safari private mode) doesn't throw on sign-in.

## 3. Test to add to `apps/api/tests/test_ui_safety.py`

```python
def test_the_page_offers_the_canva_export() -> None:
    assert "/canva.pptx?aspect=" in PAGE
```

## Acceptance (Claude checks)

- [ ] Each size downloads a file named like `boiler-offer-4x5.pptx`.
- [ ] Opening the file in Canva shows separate layers (Claude checks the file's
      structure; you check it in Canva once).
- [ ] Story keeps its shape with no 9:16 render; the phone Back aligns; sign-in works
      with storage blocked.
- [ ] No Python changed. `make check` passes.
