# Contributing to Dorico Maestro

Thank you for your interest in contributing to Dorico Maestro. This project maps and tests Steinberg Dorico's Remote Control API and provides a composition-partner MCP server on top.

## Ways to Help

- **Verify commands.** 131 of the 348 entries in [`src/dorico_maestro/commands.yaml`](src/dorico_maestro/commands.yaml) have not yet been run live. Of the remaining entries, 190 are `verified`, 23 are `reachable`, and 4 are `unavailable`. Probe commands against a running Dorico instance and record the observed behavior.
- **Test command categories.** 12 of the 22 categories currently have no verified rows (41 rows in total): `Setup` (10), `Project` (8), `TextEditor` (6), `OptionsDialog` (5), and eight smaller groups. Several of these rows are already `reachable`. The largest untested category is `EventEdit`, where 53 of 57 rows remain untested (`Navigate*` commands are verified).
- **Enrich the catalog.** Add parameter definitions (`params:`, including `name`, `dorico`, `kind`, `enum`, `required`) and executor flags (`destructive:`, `requires_note_input:`, `verify:`) using Dorico's `application.log` as reference. Dorico logs command names and arguments for UI actions (see [docs/protocol.md](docs/protocol.md) §6).
- **Re-sync against your Dorico installation.** The catalog was seeded from `keycommands.json` of Dorico 6 (340 base IDs listed in [docs/dorico_command_catalog.md](docs/dorico_command_catalog.md)). Run `python scripts/sync_catalog.py --dry-run` to inspect differences against your local installation. Running without `--dry-run` appends missing IDs as `untested` without modifying existing entries.

## Development Setup

```bash
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest                                 # run unit tests, no Dorico required
ruff check src scripts tests           # lint check
pyright                                # type check
pyrefly check                          # strict type rules, see [tool.pyrefly.errors]
```

CI runs all four. `pyright` takes its paths and its interpreter from `[tool.pyright]` in `pyproject.toml`, so it checks the same thing here as it does there. `pyrefly` enforces the stricter rules in `[tool.pyrefly.errors]`, among them `unnecessary-type-conversion`, which `pyright` does not flag — so a branch that is clean under `pyright` alone can still be rejected by CI.

### Pointing your MCP client at your working copy

The global `command: "dorico-maestro"` from the README resolves to whatever is installed on your `PATH`, which is not your local clone.

To exercise the code you are editing, this repository provides pre-configured, project-level MCP configurations using `uv run dorico-maestro`. When an AI agent launches a project-scoped MCP server, it sets the working directory to the repository root. `uv run` automatically discovers the local `pyproject.toml` and links directly to `src/dorico_maestro`. Every edit takes effect the next time the server starts—cross-platform across Windows, macOS, and Linux without needing any machine-specific absolute paths.

Pre-configured project files included in the repository:
- **Antigravity / Gemini:** `.agents/mcp_config.json`
- **Cursor:** `.cursor/mcp.json`
- **VS Code / GitHub Copilot:** `.vscode/mcp.json`
- **Claude Code:** `.claude/mcp.json`
- **OpenAI Codex:** `.codex/config.toml`

The configuration uses the standard runner format (JSON):

```json
{
  "mcpServers": {
    "dorico-maestro": {
      "command": "uv",
      "args": ["run", "dorico-maestro"]
    }
  }
}
```

And in TOML (`.codex/config.toml`):

```toml
[mcp_servers.dorico-maestro]
command = "uv"
args = ["run", "dorico-maestro"]
```

#### Manual Fallback (without uv)

If you develop without `uv` and use a standard `python -m venv`, point your client at Python running the server module directly:

```json
{
  "mcpServers": {
    "dorico-maestro": {
      "command": "python",
      "args": ["-m", "dorico_maestro.server"]
    }
  }
}
```
*(On systems where `python` on `PATH` does not point to your virtual environment, replace `"python"` with the path to your virtual environment's Python interpreter, e.g. `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on macOS/Linux).*

## Verifying a Dorico Command

With Dorico running, Remote Control enabled, and a scratch project open:

```bash
python scripts/probe_commands.py "Play.Stop" "NoteEdit.PitchUp"
```

The script prints the response code, detail, and current catalog status for each command. It does not modify `commands.yaml`.

Probe non-destructive commands first. The probe script sends commands directly via `client.send` without the safety checks in `executor.execute()`. For example, `Play.Stop` halts playback, and `NoteEdit.PitchUp` shifts selected notes up diatonically. Commands that modify the score should only be tested on temporary projects.

Setting a row status in `commands.yaml`:

- **verified:** The command returned `kOK` and the intended effect was confirmed in the score or pushed status. Document the observed behavior in `doc:`.
- **reachable:** The command returned `kOK` in a context where its effect could not be verified, or returned `kCommandNotAllowed` (confirming the command exists but was inactive in the current context).
- **unavailable:** The command returned `kUnknownCommand`. Dorico restricts commands depending on product tier (Elements, SE, Pro). An `unavailable` command may be supported in other editions.
- **broken:** The command reached Dorico but failed due to an internal issue unrelated to licensing.

Keep `doc:` descriptions concise, factual, and operational:
- Describe what the command does, its preconditions, and any required parameters.
- If a command requires a specific edition, note it concisely: `Note: Unavailable on Dorico Elements (requires Dorico Pro).`
- Do not include diary narratives, lab logs, date stamps, or markdown bolding (`**`).

Run `pytest` after updating catalog statuses. The test suite verifies that documentation claims remain synchronized with catalog data.

## Coding Conventions

- Python 3.11+, complete type annotations, `ruff`-compliant (line length 100).
- Strict separation of layers: `client.py` handles transport, `music/` handles music21 logic, `executor.py` handles execution and safety guards, and `server.py` exposes MCP tools. See [docs/architecture.md](docs/architecture.md).
- New capabilities should be added as catalog entries rather than ad-hoc command strings. Tools dispatch commands through `_run()` and `executor.execute()`.
- Distinguish between command acceptance (`kOK`) and verified score effects.

## Pull Requests

1. Create a feature branch from `main`.
2. Implement your changes with accompanying tests. Ensure `pytest` and `ruff check src scripts tests` pass.
3. Open a pull request describing the changes and how they were validated against Dorico.

## Contributor License Agreement (CLA)

Dorico Maestro is dual-licensed (AGPL-3.0 for open-source use, commercial licenses available upon request). Contributors agree to the [CLA](CLA.md) prior to merging their first pull request. Copyright remains with the contributor.

## Commercial Licensing

For proprietary licensing or embedding into non-AGPL environments, contact Roman Stark at mail@romanstark.de.
