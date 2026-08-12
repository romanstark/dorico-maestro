# Contributing to Dorico Maestro

Thanks for your interest! This project maps and hardens Steinberg Dorico's
(undocumented) Remote Control API and builds a composition-partner MCP server on
top. Contributions of all sizes are welcome.

## Ways to help

- **Verify commands.** About 165 of the ~340 entries in
  [`src/dorico_maestro/commands.yaml`](src/dorico_maestro/commands.yaml) are still
  `untested`. Probe one against real Dorico and flip its status.
- **Enrich the catalog.** Add parameter specs / flags to a command using Dorico's
  `application.log` as the source of truth (it echoes the exact command + params
  for every UI action).
- **Build features.** The MusicXML round-trip (full-score reading), more
  high-level tools, harmony/counterpoint helpers — see
  [docs/architecture.md](docs/architecture.md).

## Development setup

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest                            # 243 tests, no Dorico required
ruff check src tests              # lint
```

## Verifying a Dorico command (the core workflow)

With Dorico open and Remote Control on:

```bash
python scripts/probe_commands.py "Edit.SelectAll" "Play.Stop"
```

Only probe **non-destructive** commands against a score you care about, or use a
throwaway project. When a command returns `kOK`, set its `status: verified` in
`commands.yaml` (and note anything you learned in its `doc:`). If it returns
`kUnknownCommand` / `kError`, mark it `broken` with a note.

## Conventions

- Python 3.11+, full type hints, `ruff`-clean.
- Keep the layers separate: `client.py` is transport only, `music/` is music21
  only, `executor.py` is the glue, `server.py` maps musical intent. Don't mix.
- Every Dorico command goes through the registry + `execute()` — no ad-hoc command
  strings in `server.py`.
- Be honest in tool results: distinguish "accepted (`kOK`)" from "verified effect".

## Pull requests

1. Fork, branch from `main`.
2. Make your change with tests; keep `pytest` and `ruff check` green (CI enforces
   both).
3. Open a PR describing the change and how you verified it (especially any live
   Dorico behaviour).

## Contributor License Agreement (CLA)

Dorico Maestro is dual-licensed (AGPL-3.0 for the open project, commercial
licenses on request). So that commercial licensing stays possible, contributors
agree to the lightweight [CLA](CLA.md) before their first PR is merged — you keep
copyright to your contribution and grant the maintainer the rights described
there. In practice this is a one-time sign-off on your first PR.

## Commercial use

If AGPL doesn't fit your use case (e.g. embedding in a proprietary product),
contact Roman Stark (mail@romanstark.de) about a commercial license.
