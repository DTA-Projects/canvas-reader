"""Command-line interface: init, whoami, list, sync, status."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import requests

from . import __version__
from .canvas import API, AuthExpired, Canvas, CanvasError, RateLimited
from .convert import extract_pdf_text, html_to_markdown, rewrite_links
from .publish import PublishError, publish_markdown
from .store import (
    Config,
    ConfigError,
    load_config,
    load_manifest,
    safe_filename,
    save_manifest,
    slugify,
    write_env_file,
    write_text,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2
EXIT_CONFIG = 3

COURSES_QUERY = (
    f"{API}/courses"
    "?enrollment_type=student&enrollment_state=active"
    "&state[]=available&state[]=completed&per_page=100"
)


class CliError(Exception):
    """Bad user input or an impossible request."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _client() -> tuple[Config, Canvas]:
    cfg = load_config()
    return cfg, Canvas(cfg)


def _courses(client: Canvas) -> list[dict]:
    return client.get_list(COURSES_QUERY)


def _match_course(courses: list[dict], needle: str | None) -> list[dict]:
    if not needle:
        return courses
    want = needle.lower()
    hits = [
        c
        for c in courses
        if want == str(c["id"])
        or want in c["name"].lower()
        or want in str(c.get("course_code", "")).lower()
    ]
    if not hits:
        raise CliError(f"no course matches {needle!r} — run `canvas list` to see what's available")
    return hits


def _page_name(url: str) -> str:
    return slugify(url) + ".md"


# ---------------------------------------------------------------------------
# sync internals
# ---------------------------------------------------------------------------


def _download_files(
    client: Canvas,
    files: list[dict],
    course_dir: Path,
    entry: dict,
    *,
    force: bool,
    pdfs_only: bool,
) -> dict[int, str]:
    """Download course files; returns file id -> local name (for link rewriting)."""
    names: dict[int, str] = {}
    taken: set[str] = set()
    dest_dir = course_dir / "files"

    for file in files:
        file_id = file["id"]
        display = file.get("display_name") or file.get("filename") or f"file-{file_id}"
        name = safe_filename(display, fallback=f"file-{file_id}")
        if name in taken:  # two files with the same display name
            name = f"{file_id}-{name}"
        taken.add(name)
        dest = dest_dir / name
        is_pdf = name.endswith(".pdf")

        if pdfs_only and not is_pdf:
            continue

        url = file.get("url")
        if not url:  # locked for us
            previous = entry.get(str(file_id))
            if previous and (dest_dir / previous.get("name", "")).exists():
                names[file_id] = previous["name"]
            continue

        known = entry.get(str(file_id))
        if not force and known and known.get("size") == file.get("size") and dest.exists():
            names[file_id] = name
            if is_pdf and not dest.with_suffix(".txt").exists():
                extract_pdf_text(dest)
            continue

        magic = b"%PDF-" if is_pdf else None
        try:
            client.download(url, dest, magic=magic)
        except CanvasError:
            # the list's download link can carry a stale verifier: refetch once, retry
            fresh = client.get_obj(f"{API}/files/{file_id}")
            if not fresh.get("url"):
                raise
            client.download(fresh["url"], dest, magic=magic)

        entry[str(file_id)] = {"name": name, "size": file.get("size")}
        names[file_id] = name
        if is_pdf:
            extract_pdf_text(dest)

    return names


def _write_pages(
    client: Canvas,
    pages: list[dict],
    course_dir: Path,
    entry: dict,
    *,
    host: str,
    course_id: int,
    file_targets: dict[int, str],
    force: bool,
) -> dict[str, str]:
    """Write wiki pages as Markdown; returns page url -> local filename."""
    names: dict[str, str] = {}
    # keys are path-only so both absolute (host-prefixed) and relative links rewrite
    page_targets = {
        f"/courses/{course_id}/pages/{page['url']}": _page_name(page["url"])
        for page in pages
        if page.get("url")
    }

    for page in pages:
        url = page.get("url")
        if not url:
            continue
        body = page.get("body")
        if body is None:  # list didn't include it — fetch the page itself
            try:
                body = client.get_obj(f"{API}/courses/{course_id}/pages/{quote(url, safe='')}").get(
                    "body"
                )
            except CanvasError:
                body = None
        if not body:
            continue  # empty or block-editor page: nothing worth writing

        name = _page_name(url)
        updated = page.get("updated_at")
        dest = course_dir / "pages" / name
        if force or entry.get(url) != updated or not dest.exists():
            text = html_to_markdown(body)
            text = rewrite_links(
                text,
                host=host,
                page_targets=page_targets,
                file_targets={fid: f"../files/{n}" for fid, n in file_targets.items()},
            )
            write_text(dest, text)
        entry[url] = updated
        names[url] = name

    return names


def _item_line(
    item: dict,
    *,
    course_id: int,
    host: str,
    page_names: dict[str, str],
    file_names: dict[int, str],
) -> str:
    kind = item.get("type")
    title = (item.get("title") or "").strip() or "Untitled"

    if kind == "SubHeader":
        return f"### {title}"

    target = None
    if kind == "Page":
        name = page_names.get(item.get("page_url") or "")
        target = f"pages/{name}" if name else None
    elif kind == "File":
        name = file_names.get(item.get("content_id"))
        target = f"files/{name}" if name else None
    if not target:
        target = item.get("html_url") or item.get("external_url") or ""
    if not target:
        return f"- {title}"
    return f"- [{title}]({target})"


def _fetch_modules(client: Canvas, course_id: int) -> list[dict]:
    """Module structure — available even when the Files/Pages tabs are hidden."""
    modules = client.get_list(f"{API}/courses/{course_id}/modules?per_page=100")
    for module in modules:
        if "items" not in module:  # large modules omit inline items
            module["items"] = client.get_list(
                f"{API}/courses/{course_id}/modules/{module['id']}/items?per_page=100"
            )
    return modules


def _fetch_pages(
    client: Canvas, course_id: int, modules: list[dict], warnings: list[str]
) -> list[dict]:
    """Wiki pages; if the Pages tab is disabled, read them one-by-one via module items."""
    try:
        return client.get_list(f"{API}/courses/{course_id}/pages?include[]=body&per_page=100")
    except (AuthExpired, RateLimited):
        raise
    except CanvasError as exc:
        warnings.append(f"page listing denied ({exc}) — falling back to module items")

    seen: set[str] = set()
    pages: list[dict] = []
    failed = 0
    for module in modules:
        for item in module.get("items") or []:
            url = item.get("page_url") if item.get("type") == "Page" else None
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                pages.append(
                    client.get_obj(f"{API}/courses/{course_id}/pages/{quote(url, safe='')}")
                )
            except CanvasError:
                failed += 1
    if failed:
        warnings.append(f"{failed} module-linked pages are inaccessible")
    return pages


def _fetch_files(
    client: Canvas,
    course_id: int,
    modules: list[dict],
    extra_html: list[str | None],
    warnings: list[str],
) -> list[dict]:
    """Course files; if the Files tab is disabled, follow module items and every
    /files/<id> link found in the syllabus and page bodies."""
    try:
        return client.get_list(f"{API}/courses/{course_id}/files?per_page=100")
    except (AuthExpired, RateLimited):
        raise
    except CanvasError as exc:
        warnings.append(f"file listing denied ({exc}) — falling back to modules and links")

    ids: set[int] = set()
    for module in modules:
        for item in module.get("items") or []:
            if item.get("type") == "File" and item.get("content_id"):
                ids.add(int(item["content_id"]))
    for html in extra_html:
        ids.update(int(x) for x in re.findall(r"/files/(\d+)", html or ""))

    files: list[dict] = []
    failed = 0
    for file_id in sorted(ids):
        try:
            files.append(client.get_obj(f"{API}/files/{file_id}"))
        except CanvasError:
            failed += 1
    if failed:
        warnings.append(f"{failed} linked files are inaccessible")
    return files


def sync_course(
    client: Canvas,
    course: dict,
    cfg: Config,
    manifest: dict,
    *,
    books_only: bool,
    force: bool,
) -> dict:
    """Sync one course into content/courses/<id>-<slug>/ and update the manifest.

    Degrades per section: a denied Files/Pages listing falls back to module items
    and link scraping instead of aborting the whole sync.
    """
    course_id = course["id"]
    slug = f"{course_id}-{slugify(course['name'])}"
    course_dir = cfg.content_dir / "courses" / slug
    entry = manifest["courses"].setdefault(str(course_id), {})
    entry.update({"name": course["name"], "slug": slug})
    file_entry = entry.setdefault("files", {})
    page_entry = entry.setdefault("pages", {})
    warnings: list[str] = []

    course_obj = client.get_obj(f"{API}/courses/{course_id}?include[]=syllabus_body")
    modules = _fetch_modules(client, course_id)
    pages = [] if books_only else _fetch_pages(client, course_id, modules, warnings)
    files = _fetch_files(
        client,
        course_id,
        modules,
        extra_html=[course_obj.get("syllabus_body"), *(p.get("body") for p in pages)],
        warnings=warnings,
    )
    file_names = _download_files(
        client, files, course_dir, file_entry, force=force, pdfs_only=books_only
    )
    if books_only:
        entry["synced_at"] = _now()
        return {
            "id": course_id,
            "name": course["name"],
            "slug": slug,
            "pages": 0,
            "files": len(file_names),
            "warnings": warnings,
        }

    page_names = _write_pages(
        client,
        pages,
        course_dir,
        page_entry,
        host=cfg.host,
        course_id=course_id,
        file_targets=file_names,
        force=force,
    )

    page_targets = {f"/courses/{course_id}/pages/{u}": n for u, n in page_names.items()}
    file_targets = {fid: f"files/{n}" for fid, n in file_names.items()}

    # README: identity, syllabus, table of contents
    parts = [f"# {course['name']}", ""]
    if course.get("course_code"):
        parts += [f"`{course['course_code']}` · Canvas course `{course_id}`", ""]
    if course.get("html_url"):
        parts += [f"[Open in Canvas]({course['html_url']})", ""]
    syllabus = course_obj.get("syllabus_body")
    if syllabus:
        body = rewrite_links(
            html_to_markdown(syllabus),
            host=cfg.host,
            page_targets=page_targets,
            file_targets=file_targets,
        )
        parts += ["## Syllabus", "", body.rstrip(), ""]
    parts += [
        "## Contents",
        "",
        "- [Modules](modules.md) — the course structure in reading order",
        f"- {len(page_names)} wiki pages in [`pages/`](pages/)",
        f"- {len(file_names)} files (incl. PDF textbooks) in [`files/`](files/)",
        "",
    ]
    write_text(course_dir / "README.md", "\n".join(parts))

    # modules.md: reading order with local links where we have the content
    lines = [f"# Modules — {course['name']}", ""]
    for module in modules:
        lines += [f"## {module.get('name') or 'Untitled module'}", ""]
        for item in module.get("items") or []:
            lines.append(
                _item_line(
                    item,
                    course_id=course_id,
                    host=cfg.host,
                    page_names=page_names,
                    file_names=file_names,
                )
            )
        lines.append("")
    write_text(course_dir / "modules.md", "\n".join(lines).rstrip() + "\n")

    entry["synced_at"] = _now()
    return {
        "id": course_id,
        "name": course["name"],
        "slug": slug,
        "pages": len(page_names),
        "files": len(file_names),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    env_path = Path(".env")
    if env_path.exists() and not args.force:
        raise CliError(f"{env_path} already exists — edit it directly, or re-run with --force")

    host = input("Canvas URL [https://yourschool.instructure.com]: ").strip()
    if not host:
        raise CliError("a Canvas URL is required")
    if not host.startswith(("http://", "https://")):
        host = "https://" + host

    print("Authentication:")
    print("  [1] API token  (Canvas → Account → Profile → Approved Integrations)")
    print("  [2] session cookie  (log in, F12 → Application → Cookies → _canvas_session)")
    mode = input("Choose 1 or 2: ").strip()

    token = session = None
    if mode == "1":
        token = getpass.getpass("API token: ").strip()
        if not token:
            raise CliError("empty token")
    elif mode == "2":
        session = input("_canvas_session value: ").strip()
        if not session:
            raise CliError("empty session cookie")
    else:
        raise CliError("choose 1 or 2")

    write_env_file(env_path, host, token, session)
    print(f"✔ wrote {env_path} (owner-only permissions)")

    cfg, client = _client()  # validates what we just wrote
    me = client.get_obj(f"{API}/users/self/profile")
    print(f"✔ signed in as {me.get('name', '?')} ({cfg.auth_mode} mode) — next: canvas sync")
    return EXIT_OK


def cmd_whoami(args: argparse.Namespace) -> int:
    cfg, client = _client()
    me = client.get_obj(f"{API}/users/self/profile")
    info = {
        "id": me.get("id"),
        "name": me.get("name"),
        "login": me.get("login_id") or me.get("email"),
        "auth": cfg.auth_mode,
        "host": cfg.host,
    }
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"✔ {info['name']} <{info['login']}> — {info['auth']} auth @ {info['host']}")
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    cfg, client = _client()
    courses = _courses(client)
    if args.json:
        print(
            json.dumps(
                [{"id": c["id"], "code": c.get("course_code"), "name": c["name"]} for c in courses],
                indent=2,
            )
        )
    else:
        if not courses:
            print("No active student courses found.")
        for c in courses:
            print(f"{c['id']:>8}  {c.get('course_code') or '-':<20}  {c['name']}")
        print(f'\n{len(courses)} course(s). Sync one with: canvas sync "<name or id>"')
    return EXIT_OK


def cmd_sync(args: argparse.Namespace) -> int:
    cfg, client = _client()
    courses = _match_course(_courses(client), args.course)
    manifest = load_manifest(cfg.content_dir)
    results = []
    for course in courses:
        print(f"→ syncing: {course['name']}", file=sys.stderr)
        result = sync_course(
            client, course, cfg, manifest, books_only=args.books_only, force=args.force
        )
        results.append(result)
        if not args.json:
            verb = (
                "textbooks only"
                if args.books_only
                else f"{result['pages']} pages, {result['files']} files"
            )
            print(f"✔ {result['name']} — {verb}")
        for warning in result.get("warnings", []):
            print(f"⚠ {result['name']}: {warning}", file=sys.stderr)
    save_manifest(cfg.content_dir, manifest)
    try:
        published = publish_markdown(cfg)
    except PublishError as exc:
        print(f"⚠ publish failed: {exc}", file=sys.stderr)
    else:
        if published:
            print(f"↑ study-materials: {published}", file=sys.stderr)
    if args.json:
        print(json.dumps(results, indent=2))
    elif results:
        where = cfg.content_dir.resolve()
        print(f"\nDone. Content is in {where}")
        print("Ask your agent: open opencode in this repo — the canvas skill knows the rest.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    manifest = load_manifest(cfg.content_dir)
    rows = []
    for course_id, entry in sorted(manifest["courses"].items()):
        course_dir = cfg.content_dir / "courses" / entry.get("slug", course_id)
        pages = (
            len(list((course_dir / "pages").glob("*.md"))) if (course_dir / "pages").exists() else 0
        )
        pdfs = (
            len(list((course_dir / "files").glob("*.pdf")))
            if (course_dir / "files").exists()
            else 0
        )
        rows.append(
            {
                "id": int(course_id),
                "name": entry.get("name"),
                "pages": pages,
                "pdfs": pdfs,
                "synced_at": entry.get("synced_at"),
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("Nothing synced yet — run `canvas sync`.")
    else:
        for row in rows:
            when = (row["synced_at"] or "never").replace("T", " ")
            counts = f"{row['pages']:>3} pages  {row['pdfs']:>3} pdfs"
            print(f"{row['id']:>8}  {counts}  {when}  {row['name']}")
    if not args.json and cfg.textbooks_dir:
        print(f"Textbooks: {cfg.textbooks_dir}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canvas",
        description="Sync Canvas courses to local Markdown + PDF files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="interactive setup: write .env and verify login")
    p.add_argument("--force", action="store_true", help="overwrite an existing .env")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("whoami", help="verify credentials")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("list", help="list your courses")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("sync", help="download courses (pages + files) locally")
    p.add_argument("course", nargs="?", help="course name, code, or id (default: all)")
    p.add_argument("--books-only", action="store_true", help="only download PDF textbooks")
    p.add_argument("--force", action="store_true", help="re-download even if unchanged")
    p.add_argument("--json", action="store_true", help="machine-readable summary")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("status", help="show what has been synced")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except AuthExpired as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return EXIT_AUTH
    except ConfigError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except RateLimited as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return EXIT_ERROR
    except CanvasError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return EXIT_ERROR
    except CliError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return EXIT_ERROR
    except requests.exceptions.RequestException as exc:
        print(f"✖ cannot reach Canvas: {exc}", file=sys.stderr)
        print("  → check CANVAS_HOST in .env, your network/VPN, then retry", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\n✖ interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
