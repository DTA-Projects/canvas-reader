# Contributing

Thanks for helping. This project stays small on purpose — a focused CLI, a
tested network layer, and an agent-friendly content layout.

## Development setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or plain pip).

```bash
git clone https://github.com/DTA-Projects/canvas-reader.git
cd canvas-reader
uv sync --extra dev      # or: python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

## Checks

```bash
uv run pytest            # 73 tests, fully offline — no Canvas account needed
uv run ruff check src tests
uv run ruff format src tests
```

CI runs the same checks on Linux, Windows, and macOS across Python 3.11 and 3.14.
A PR must stay green on all of them.

## Ground rules

- **Tests never hit the network.** Mock HTTP with `responses`; commit fixtures as
  plain HTML strings, not real course material.
- **No real credentials, course IDs, school names, or PDFs** in commits, tests,
  or docs. `.env`, `content/`, and `*.pdf` are gitignored for a reason (see
  GitHub's 100 MB per-file limit and basic privacy).
- **One dependency earns its place.** Prefer the standard library; every
  dependency must be justified in the PR description.
- **Keep the module count low.** `store` (disk), `canvas` (network), `convert`
  (text), `cli` (commands). If you're adding a fifth module, explain why.
- Follow the existing style; `ruff format` is authoritative.

## Release checklist (maintainers)

1. Bump `version` in `pyproject.toml` and `src/canvas_reader/__init__.py`
2. Update `CHANGELOG.md`
3. Tag `vX.Y.Z` — installs pin tags via
   `uv tool install git+https://github.com/DTA-Projects/canvas-reader@vX.Y.Z`
