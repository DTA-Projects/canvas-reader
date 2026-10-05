# Security Policy

## Reporting a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/DTA-Projects/canvas-reader/security)
if available, or open a minimal issue titled `security:` **without** including
credentials, tokens, cookies, school names, or course material.

## How this project handles credentials

- Credentials live only in `.env` (gitignored) or process environment variables.
- `.env` is created with owner-only permissions (`600`) on Linux/macOS.
- Credentials are sent **only** to `CANVAS_HOST` — file downloads follow redirects
  by hand so tokens/cookies never reach CDN or S3 hosts.
- Logs and error messages never include credential values; error text from Canvas
  is passed through, credential headers are not.
- Exit code `2` always means "your credential expired or was revoked" — rotate and
  update `.env`.

## What a Canvas credential means

A session cookie or API token grants **your full student access** to your school's
Canvas: grades, submissions, personal data. Treat it like a password:

- never commit it, never paste it into issues/chats/screenshots
- rotate it if exposed: delete the token under *Approved Integrations*, or simply
  log out of Canvas (which invalidates the session cookie)
- on shared machines, prefer the API token with a short expiry if your school allows it

## Course content

Synced content (`content/`) and PDFs are ignored by git. Textbooks and course
material are copyrighted — do not commit or redistribute them.
