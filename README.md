# canvas-reader

[![CI](https://github.com/DTA-Projects/canvas-reader/actions/workflows/ci.yml/badge.svg)](https://github.com/DTA-Projects/canvas-reader/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)

Sync your Canvas courses to a local folder — **pages as clean Markdown, textbooks as PDFs** — that you and AI coding agents like [OpenCode](https://opencode.ai) can read offline.

Works at schools that **disable API tokens** by using your browser session instead. No admin access, no SSO automation, no password sharing.

## Features

- 📄 Canvas wiki pages → Markdown, links rewritten to the local files that were just downloaded
- 📚 PDF textbooks downloaded verbatim (verified `%PDF-`), plus a `.txt` sidecar so `grep` can search them
- 🔐 Two auth modes: API token **or** your browser's `_canvas_session` cookie (works with SSO/2FA schools)
- ⚡ Incremental re-sync via a manifest — unchanged pages and files are never re-fetched
- 🐧🐻🪟 Linux, macOS, and Windows (tested in CI on all three)
- 🤖 Ships an OpenCode skill and `/sync` command — clone, sync, ask

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Check with `python3 --version` |
| **[`uv`](https://docs.astral.sh/uv/)** | The installer below handles this if you don't have it |
| **Your Canvas URL** | e.g. `https://yourschool.instructure.com` |
| **Canvas access** | An API token *or* your browser session cookie (see [Authentication](#authentication)) |
| **OpenCode** *(optional)* | Only for the AI-reading features |

## Install — one command

```bash
uv tool install git+https://github.com/DTA-Projects/canvas-reader
```

<details>
<summary>No <code>uv</code> yet? One line per OS first:</summary>

**macOS / Linux**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell)**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then run the install command above again in a fresh shell.
</details>

Verify:

```bash
canvas --version
```

## Quickstart — three commands

```bash
canvas init      # interactive: your Canvas URL + auth method, written to .env
canvas whoami    # confirms your credentials work
canvas sync      # downloads every course (or one: canvas sync "Organic Chemistry")
```

Your content lands in `./content/`. Open it in any editor — or point OpenCode at it.

## Authentication

`canvas init` walks you through this interactively. Both modes write to `.env`, which is gitignored.

### Option A — API token *(if your school allows it)*

1. In Canvas: **Account → Profile → Approved Integrations → + New Access Token**
2. `canvas init` → choose `1` → paste the token.

> ⚠️ Many schools disable this button. If you don't see it, use Option B.

### Option B — Browser session cookie *(works at almost every school)*

Uses the session you already have from logging in. No admin approval, works with SSO/2FA,
and your password is never involved.

1. Log into Canvas in your browser as usual.
2. Open DevTools (**F12**) → **Application** tab (Chrome/Edge) or **Storage** tab (Firefox)
   → **Cookies** → your Canvas domain.
3. Copy the value of the **`_canvas_session`** cookie.
4. `canvas init` → choose `2` → paste the value.

**About expiry:** schools invalidate sessions after ~24 hours or when you close the browser.
When that happens the CLI fails fast with a clear message (exit code `2`):

```
✖ session cookie expired (HTTP 401) — copy a fresh _canvas_session cookie
  (F12 → Application → Cookies) and update .env
```

Re-copy the cookie and re-run — sync is incremental, so you only lose the wait, not the work.
`canvas whoami` is a one-second health check before a long sync.

> 🔎 **How credentials are handled:** requests only ever carry your token/cookie to
> `CANVAS_HOST` itself. File downloads follow redirects *by hand*, so your credentials are
> never sent to Canvas's CDN/S3 hosts.

**Precedence:** real environment variables beat `.env`; if both a token and a session are
set, the token wins.

## Usage

| Command | What it does |
|---|---|
| `canvas init` | Interactive setup — writes `.env`, verifies login |
| `canvas whoami` | Check credentials (`--json` for scripts) |
| `canvas list` | List your courses with IDs (`--json`) |
| `canvas sync` | Sync all courses; pass a name/code/id to sync one |
| `canvas sync --books-only` | Only download PDF textbooks |
| `canvas sync --force` | Re-download even if nothing changed |
| `canvas status` | What's synced, page/PDF counts, last sync time (`--json`) |

**Exit codes** (for scripting and agent use): `0` success · `1` error/rate-limited ·
`2` auth expired · `3` configuration problem.

## What gets downloaded

```
content/                                # gitignored — stays on your device
├── manifest.json                       # sync state → incremental updates
└── courses/
    └── 12345-intro-to-biology/
        ├── README.md                   # identity, syllabus, table of contents
        ├── modules.md                  # module structure, links made local
        ├── pages/
        │   ├── week-1-notes.md         # Canvas pages → Markdown + local images/links
        │   └── lab-safety.md
        └── files/
            ├── campbell-biology-12e.pdf    # original textbook
            ├── campbell-biology-12e.txt    # extracted text, so grep works
            └── slide-deck-1.pdf
```

Every page and module link points at a local file first; things we can't archive
(assignments, quizzes, external tools) keep their absolute Canvas URL.

## Using with OpenCode

```bash
git clone https://github.com/DTA-Projects/canvas-reader.git
cd canvas-reader
canvas sync          # populate ./content
opencode             # that's it
```

The repo ships `.opencode/skills/canvas-reader/` (auto-discovered), a `/sync` command, and
an `AGENTS.md`, so OpenCode automatically knows:

- course pages live in `content/courses/*/pages/*.md` — glob/grep first
- PDF textbooks are `content/courses/*/files/*.pdf` — the `read` tool opens them directly
- `*.txt` sidecars exist for full-text search (grep can't read inside PDFs)
- run `canvas sync` to refresh before answering time-sensitive questions

Ask things like *"What did my professor say about the midterm in Week 4 notes?"* — the
answer comes from your device. Nothing is uploaded.

## Migrating from `opencode-class-agents`

Coming from [DTA-Projects/opencode-class-agents](https://github.com/DTA-Projects/opencode-class-agents)?
This repo replaces it — same idea, rebuilt to work everywhere:

| Old setup | Replacement here |
|---|---|
| `CONTEXT.md` loaded via the `instructions:` config | Root **`AGENTS.md`** — the `instructions` array is silently ignored by OpenCode V2, so this actually loads |
| Per-course `agents/<course>.md` (`mode: primary`) | The **`canvas` skill** — covers every course at once, including cross-course questions |
| `node canvas.mjs <cmd>` hitting the live API on every question | **`canvas sync`** → local `content/` cache — answers are instant greps, no rate limits, works offline |
| `git-sync.ps1` (PowerShell only) | **`canvas sync`** — one command, identical on Linux, macOS, and Windows |
| Textbook paths hand-edited into each agent file | Downloaded automatically into `files/` and linked from `README.md`/`modules.md` |

**Migration steps:**

```bash
git clone https://github.com/DTA-Projects/canvas-reader.git
cd canvas-reader
canvas init          # your old canvas/.env values work as-is
canvas sync
opencode             # start from this repo; your old config is untouched
```

Your old per-course agent prompts still work: copy them into `.opencode/agents/<name>.md`
and change `mode: primary` → `mode: subagent`, so they answer alongside the main agent
instead of replacing it.

## Security

- **Your token/cookie is a login equivalent.** `.env` is gitignored — never commit it,
  paste it in issues, or screenshot it.
- **Rotate:** delete the token under *Approved Integrations*, or just close your browser
  tab for cookie mode.
- **Textbooks stay local.** `content/` and `*.pdf` are gitignored — copyrighted course
  material must never be pushed (GitHub also hard-rejects files over 100 MB).
- `.env` is written with owner-only permissions (`600`) on Linux/macOS.
- Found a vulnerability? See [SECURITY.md](SECURITY.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `session cookie expired` (exit 2) | Re-copy the `_canvas_session` cookie, update `.env`, rerun |
| `access token rejected` (exit 2) | Token was deleted or revoked — regenerate it |
| `still holds a placeholder` (exit 3) | You copied `.env.example` without filling it in |
| `Canvas is rate limiting us` (exit 1) | Canvas throttles by request cost — wait a few minutes; sync resumes where it stopped |
| `download returned a login page` | Session expired mid-sync — refresh the cookie and rerun; partial files are never committed |
| `CERTIFICATE_VERIFY_FAILED` | Corporate proxy — set `SSL_CERT_FILE=/path/to/corp-ca.pem` |
| `no course matches …` | Run `canvas list`; only active *student* enrollments are synced |
| Downloaded file says `not a valid file` | The file wasn't really a PDF — rerun with `--force` after refreshing credentials |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Quick version:

```bash
uv sync --extra dev
uv run pytest          # fully offline — no Canvas account needed
uv run ruff check .
```

## License & Disclaimer

[MIT](LICENSE). This is an unofficial tool, not affiliated with or endorsed by Instructure.
It uses Canvas's public API endpoints; downloading course material is intended for your own
personal, offline study — respect your institution's acceptable-use policy.
