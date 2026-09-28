# Briefs for Cursor

Each file here is one module for a Cursor agent (Grok models). Claude writes the brief
and the hard logic; Cursor does the rest; Claude reviews before anything merges.

## How to run one

1. Make a worktree so Cursor's branch never collides with anything else in flight
   (run in Warp from `~/dev/stratum`, after `main` has the latest merges):

       git worktree add ../stratum-<name> -b <branch> main
       cd ../stratum-<name> && make setup

2. Open that folder in Cursor, start a new Agent chat, and paste the prompt at the top
   of the brief (the block under "Paste this into Cursor").
3. When Cursor says it is done, run `make check` in that folder, then tell Claude
   "review <brief id>". Claude reads the diff through the bridge and replies with
   fixes or a go.
4. Commit and push from Warp; open the PR; squash-merge after the review.
5. Clean up: `git worktree remove ../stratum-<name>`.

## Rules every brief inherits

- Read `AGENTS.md` first. It wins over anything in a brief.
- Touch only the files the brief lists. If something else seems to need changing,
  stop and say so in the chat instead of changing it.
- No new dependencies unless the brief names them.
- `make check` must pass. Never weaken or delete a test to make it pass.
- Finish with a short report: files changed, what you could not do, anything you
  guessed.

## Order

| Id | Module | Who | Depends on |
| --- | --- | --- | --- |
| M8 | Scoped client factory (queue runs as the requester) | Claude (done, #11) | queue merged |
| C1 | UI foundation, Needs you home, feed/story/grid previews | Cursor (done, #13) | nothing |
| C2 | Deploy: Docker image, API and worker on Railway | Cursor | M8 merged |
| M13 | Client review link: tables, functions, routes, client page | Claude (done, #12) | M8 |
| C5 | Send for review, review links, client feedback, page polish | Cursor | C1, M13 |
| M14 | Export: PowerPoint for Canva plus the zip | Claude (done) | C1 |
| C1b | Polish from the C1 review, and "Open in Canva" buttons | Cursor | M14 |
| M9 + C3 | Brand kit from the logo, onboarding form | Claude, then Cursor | C1 |
| M10–M12 | Real photos, layout variants, top three with a ranker | Claude, Cursor on adapters | C1 |
| M16 | The ranker learns the team's picks | Claude | M12 in use |
