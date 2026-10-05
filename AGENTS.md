# canvas-reader

A CLI that syncs your Canvas courses into this repository as local content:
wiki pages become Markdown, PDF textbooks are downloaded verbatim (with `.txt`
sidecars for search). This file teaches agents how to work with that content.

## Commands

| Command | Purpose |
|---|---|
| `canvas sync` | Refresh all courses (or one: `canvas sync "BIO"`) |
| `canvas sync --books-only` | Only refresh PDF textbooks |
| `canvas status --json` | What's synced, without touching the network beyond auth |
| `canvas list --json` | Course IDs and names |
| `canvas whoami` | Verify credentials still work |

Exit codes: `0` ok · `1` error or rate-limited · `2` auth expired (tell the user to
refresh their cookie/token) · `3` configuration problem (`.env` missing or placeholder).

## Content layout

```
content/courses/<id>-<slug>/
├── README.md        # syllabus + table of contents
├── modules.md       # course structure with local links
├── pages/*.md       # Canvas wiki pages → Markdown
└── files/           # PDFs, images, decks (+ <name>.txt sidecars next to PDFs)
```

`content/` is gitignored — it is private, per-device data. Never commit it, never
paste its contents wholesale into issues, and never push PDFs to GitHub.

When `STUDY_REPO` is set, each sync also pushes a Markdown-only copy into that
private repo's `canvas/<course>/` folders — read from `content/` locally; never
publish more than Markdown yourself. Files the tool did not publish (handwritten
notes inside the course folders) are preserved on every publish.

## How to answer course questions

1. `canvas status` (or read `content/manifest.json`) to see what's available; if nothing
   is synced or the question is time-sensitive, run `canvas sync` first.
2. Glob/grep inside `content/courses/*/pages/` before reading whole files.
3. For textbooks, check `canvas status` — a `Textbooks: <folder>` line means a
   shared textbook folder is configured (here: `Z:\Textbooks`, i.e.
   `/mnt/z/Textbooks` under WSL); prefer those PDFs, and fall back to
   `content/courses/*/files/` + their `.txt` sidecars. Grep the sidecar to find
   the page, then `read` the `.pdf` (the read tool passes PDFs to the model
   directly) for diagrams.
4. Links that point outside the course folder are absolute Canvas URLs — they mean
   "this lives only on Canvas" (assignments, quizzes); say so instead of inventing content.

## Security rules

- Never print, echo, or commit `.env` — it holds a live Canvas credential.
- Never send the token/cookie anywhere except through the `canvas` CLI.
- If a command exits `2`, the credential expired — ask the user for a fresh one;
  do not try to work around it.
