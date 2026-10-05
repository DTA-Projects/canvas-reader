# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [0.1.0] — 2026-10-04

### Added

- `canvas init` — interactive setup writing a protected `.env` (token or session cookie)
- `canvas sync` — incremental course sync: wiki pages → Markdown with rewritten
  local links, PDFs verified by magic bytes, `.txt` sidecars for grep, module
  reading order, syllabus into `README.md`
- **Graceful degradation for restricted courses:** when a school hides the Files
  or Pages navigation tab (403 on the listings), sync falls back to module items,
  single-object fetches, and `/files/<id>` links scraped from syllabi and pages —
  reported as `⚠` warnings instead of aborting
- `canvas whoami` / `canvas list` / `canvas status` with `--json` output and
  documented exit codes (0/1/2/3)
- Network layer: Link-header pagination, rate-limit classification
  (403/429 "Rate Limit Exceeded" vs auth errors), exponential backoff,
  cross-host credential isolation, manual redirect handling for downloads,
  failing endpoint named in every error message
- OpenCode integration: repo-shipped skill, `/sync` command, `AGENTS.md`,
  least-privilege `opencode.json` permissions
- CI matrix: Linux / Windows / macOS × Python 3.11 / 3.14, offline tests
- Docs: README (auth, migration from `opencode-class-agents`, troubleshooting),
  CONTRIBUTING, SECURITY

### Fixed

- `.env` discovery: `find_dotenv(usecwd=True)` — plain `load_dotenv()` walks from
  site-packages, so a tool-installed `canvas` never saw the project's `.env`
- Unreachable `CANVAS_HOST` printed a raw traceback; now a friendly error (exit 1)
- Cookie auth could send credentials to `school.instructure.com.evil.example`
  (prefix match); now an exact origin boundary check
