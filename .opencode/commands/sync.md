---
description: Sync Canvas courses into content/ and summarize what changed
---

Refresh the local Canvas content, then report what changed:

!`canvas sync 2>&1 | tail -40`

Scope requested: $ARGUMENTS

If a course name was requested above, re-run as `canvas sync "<name>"` instead.
If the command exits with code 2, the Canvas credential expired — tell the user
to refresh their cookie/token per README → Authentication, and stop.
