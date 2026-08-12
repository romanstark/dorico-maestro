# Dorico Maestro

> An AI composition partner that remote-controls **Steinberg Dorico** through the
> Model Context Protocol (MCP).

Dorico Maestro turns Dorico into a shared canvas between a composer and an AI
assistant. You discuss a musical idea in plain language; the assistant writes it
into your open Dorico score, plays it back, and the two of you refine it together
— bar by bar.

> **Status: experimental (v0.1), but broadly exercised.** **176 of 344**
> catalogued commands are verified live against Dorico 6 (returned `kOK`), 3 are
> known-broken, and the rest are not yet exercised — many are *recognised* by
> Dorico but need a specific context, or open dialogs that can't be auto-tested.
> Every command carries an honest status (`verified` / `broken` / `untested`) in
> [`src/dorico_maestro/commands.yaml`](src/dorico_maestro/commands.yaml), also
> served by the `dorico://commands` resource.

---

## How it works

```
You ⇄ Claude (LLM) ⇄ Dorico Maestro (MCP server) ⇄ Dorico (Remote Control · WebSocket 127.0.0.1:4560)
                                 │
                                 └─ music21 ⇄ MusicXML  (full-score generation & reading)
```

- **Data-driven core** — every Dorico command is described as data in
  `commands.yaml`; one generic builder/executor sends them, so behaviour is
  consistent and adding a command is a data change, not new code.
- **Small, honest MCP surface** — a handful of musical tools (`add_notes`,
  `transpose`, `playback`, …), a generic `run_command`, and a
  `dorico://commands` resource to discover the whole catalog. `kOK` from Dorico
  means *accepted*, not *effective* — results say which.
- **Full-score awareness** — Dorico's API can only read the *current selection*,
  so whole-piece understanding uses MusicXML + [music21](https://web.mit.edu/music21/).
- **A "music brain"** — a typed `ScoreSpec` the assistant fills in, plus offline
  theory (key detection, Roman-numeral analysis, voice-leading & parallel checks,
  instrument ranges, species counterpoint). It writes a whole score into Dorico
  through the **guaranteed caret path** (verified commands only, zero manual steps)
  or, for richer notation, exports/​imports **MusicXML**.

## Requirements

- **Steinberg Dorico** (Remote Control API; introduced in Dorico 4, tested on
  Dorico 6), with Remote Control enabled.
- **Python 3.11+**.
- An MCP client (Claude Desktop / Claude Code).

## Install

```bash
git clone git@github.com:romanstark/dorico-maestro.git
cd dorico-maestro
python -m venv .venv
.venv\Scripts\activate          # Windows  (use source .venv/bin/activate on macOS/Linux)
pip install -e ".[dev]"
pytest                          # optional: 243 tests, no Dorico needed
```

## Enable Dorico's Remote Control

1. In Dorico, turn on the **Remote Control** API (it listens on `127.0.0.1:4560`).
2. The first time Dorico Maestro connects, Dorico asks you to **allow the
   connection** — accept it. On the very first connect you may be prompted **more
   than once (allow it twice)**; accept each time. A session token is then stored
   and reused, so later runs connect without a prompt.

## Register with your MCP client

Add to your client's `mcpServers` config (adjust the path to your checkout):

```json
{
  "mcpServers": {
    "dorico-maestro": {
      "command": "/absolute/path/to/dorico-maestro/.venv/Scripts/python.exe",
      "args": ["-m", "dorico_maestro.server"]
    }
  }
}
```

Restart the client, open a score in Dorico, then say *"connect to Dorico"*.

## Usage

Talk to the assistant naturally; it drives these tools:

- `connect_to_dorico`, `get_status` — connect and read Dorico's live state.
- `add_notes(["C4","E4","G4"], duration="quarter")` — enter notes at the caret
  (accidentals like `"F#5"`/`"Bb3"` supported), then leaves note input cleanly.
- `add_rest`, `transpose`, `switch_mode`, `playback`, `save`.
- `write_score(score)` — write a whole `ScoreSpec` (JSON) into Dorico via the
  guaranteed caret path (or `method="musicxml"`); `render_to_dorico(score,
  dry_run=True)` previews the exact command plan without sending.
- `export_musicxml` / `import_musicxml` — full-fidelity MusicXML out, best-effort in.
- `analyze_harmony`, `check_voice_leading`, `suggest_next_chord`,
  `instrument_range`, `check_counterpoint` — offline theory, no Dorico needed.
- `run_command(command_id, params)` — the escape hatch for any catalogued command.
- Resource `dorico://commands` (and `dorico://commands/{category|status}`) —
  discover the full command set and its verification status.

## What works / current limits

- **Verified live (176 commands):** connect + status; note input
  (pitch/duration/accidental/rest/tie/dot/chords); selection & event navigation;
  all zoom/viewport; playback transport; all five window modes; transposition
  (`NoteEdit.*`); and much of `EventEdit.*`.
- **Whole-score composition & theory (offline + caret):** a typed `ScoreSpec`
  drives full-score writing through the verified caret path (multi-staff piano is
  proven live) and a battery of offline theory tools; multi-instrument caret entry
  is flagged experimental until verified live.
- **Selection-only reads:** there is no "read bar N" — the API reads only the
  current selection; whole-score reading will go via MusicXML export + music21.
- **Popover-only actions not yet supported:** time/key signatures and dynamics are
  entered via a Dorico popover whose value the API can't fill, so those tools
  report the limitation instead of silently doing nothing.
- **`kOK` ≠ effect:** tools verify where they can and say so; otherwise confirm in
  the score.

## Contributing

Contributions are very welcome — via **GitHub Issues** and **Pull Requests**. CI
runs the tests and linter on every PR. Because the project is dual-licensed (see
below), contributors sign a lightweight **CLA**. Start with
[CONTRIBUTING.md](CONTRIBUTING.md).

Good first tasks: live-verify `untested` commands (flip their status in
`commands.yaml`), enrich command params from Dorico's `application.log`, or help
build the MusicXML round-trip.

## License

**AGPL-3.0-or-later** — see [LICENSE](LICENSE). The software is free and open; if
you deploy a modified version, you must share your source under the same license.

**Commercial licensing:** if you want to use Dorico Maestro in a proprietary or
closed-source product (AGPL terms don't fit), a separate commercial license is
available — contact Roman Stark (mail@romanstark.de).

**Third-party notices:** see [THIRD-PARTY.md](THIRD-PARTY.md) — Dorico Maestro builds
on [music21](https://github.com/cuthbertLab/music21) (BSD-3-Clause) and the
royalty-free [MusicXML](https://www.w3.org/2021/06/musicxml40/) format; neither
restricts this project's licensing.

## Documentation

- [docs/architecture.md](docs/architecture.md) — how the data-driven design fits together
- [docs/protocol.md](docs/protocol.md) — the reverse-engineered Dorico Remote Control protocol
- [docs/dorico_command_catalog.md](docs/dorico_command_catalog.md) — all ~340 command IDs
