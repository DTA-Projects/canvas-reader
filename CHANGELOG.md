# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [0.1.0] — 2026-10-04

### Added

- `canvas init` — interactive setup writing a protected `.env` (token or session cookie)
- `canvas sync` — incremental course sync: wiki pages → Markdown with rewritten
  local links, PDFs verified by magic bytes, `.txt` sidecars for grep, module
  reading order, syllabus into `README.md`
- `canvas whoami` / `canvas list` / `canvas status` with `--json` output and
  documented exit codes (0/1/2/3)
- Network layer: Link-header pagination, rate-limit classification
  (403/429 "Rate Limit Exceeded" vs auth errors), exponential backoff,
  cross-host credential isolation, manual redirect handling for downloads
- OpenCode integration: repo-shipped skill, `/sync` command, `AGENTS.md`,
  least-privilege `opencode.json` permissions
- CI matrix: Linux / Windows / macOS × Python 3.11 / 3.14, offline tests
- Docs: README (auth, migration from `opencode-class-agents`, troubleshooting),
  CONTRIBUTING, SECURITY
