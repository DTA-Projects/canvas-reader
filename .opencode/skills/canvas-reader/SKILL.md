---
name: Canvas Course
description: Read and search the local Canvas course content synced into this repo - pages, modules, syllabi, and PDF textbooks. Use when asked about coursework, due dates, lecture notes, or textbook material.
---

# Canvas course content

This repository caches your Canvas courses locally. All answers about coursework
come from here, not from the network.

## Layout

```
content/
├── manifest.json                        # what's synced, when it last changed
└── courses/<id>-<slug>/
    ├── README.md                        # syllabus + contents (start here)
    ├── modules.md                       # reading order / module structure
    ├── pages/*.md                       # wiki pages as Markdown
    └── files/                           # PDFs, images, decks
        └── <textbook>.txt               # sidecar text for grepping PDFs
```

## Workflow

1. List `content/courses/*/README.md` to see which courses exist.
2. Grep before reading: `pages/` for lecture notes, `*.txt` sidecars for textbook
   passages. Read whole files only after grep narrows the location.
3. PDF textbooks: grep the `.txt` sidecar to find the section, then `read` the
   matching `.pdf` — the read tool passes PDFs (≤20 MiB) to the model directly.
   `grep` cannot search inside PDFs; that is why the sidecars exist.
4. If the question is about *today* (due dates, new announcements) or `content/`
   looks stale, run `canvas sync` first (or suggest the user run `/sync`).
5. Links inside the Markdown that are absolute `https://...` URLs point at content
   that exists only on Canvas (assignments, quizzes). Report the URL instead of
   guessing.

## Auth failures

Exit code `2` means the Canvas credential expired (session cookies last ~24h).
Tell the user to copy a fresh `_canvas_session` cookie into `.env`, then rerun
`canvas sync`. Do not attempt to bypass this.
