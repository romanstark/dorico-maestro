# Architecture: Dorico Maestro

System architecture and execution model for Dorico Maestro.

The design relies on a data-driven command registry, decoupling transport, execution lifecycle, and musical logic:
- **Data-Driven Registry:** Command signatures, parameters, and metadata are declared in `commands.yaml`.
- **Reusable Execution Pipeline:** A central executor handles note-input lifecycle guards, FIFO dispatch, and verification.
- **Layer Decoupling:** Transport modules do not contain music theory, and musical analysis modules do not access network sockets.

## Layers

```
commands.yaml            Catalog: command definitions (id, params, status, flags, docs)
   │
spec.py                  CommandSpec / ParamSpec validation and query builder
   │
registry.py              Runtime catalog loader and category/status indexing
   │
executor.py              Central execution engine (guards, note input lifecycle, FIFO dispatch, verification)
   │
client.py                Transport: WebSocket client, handshake, FIFO reply correlation, status snapshots
models.py                Value objects, enums, and mapping dictionaries
project_file.py          Offline .dorico container metadata parser
toolargs.py              Pydantic input models for MCP tool schemas
music/score.py           ScoreSpec data model and music21 conversion
music/theory.py          Offline harmonic, voice-leading, range, and counterpoint analysis
music/musicxml.py        MusicXML serialization, round-trip parsing, and import helpers
render.py                Caret plan generator and live dispatch
server.py                FastMCP tool definitions and dorico://commands resource
```

## Command Catalog

`src/dorico_maestro/commands.yaml` serves as the single source of truth for command metadata. The catalog contains 348 commands, comprising 340 base IDs from Dorico 6, four parameterized core commands (`NoteInput.Pitch`, `NoteInput.SetAccidental`, `Window.SwitchMode`, `Play.StartOrStop`), and four binary export commands (`File.Export`, `File.Export?FilterID=MusicXMLExportFilter`, `Print.ExportCurrentLayoutAsPDF`, `Print.ExportAllLayoutsAsPDF`).

Example catalog entry:

```yaml
- id: "NoteInput.Pitch"
  category: NoteInput
  status: verified            # verified | reachable | unavailable | broken | untested
  requires_note_input: true   # automatically enters/exits note input
  verify: note_added          # post-condition verifier name
  params:
    - {name: pitch,  dorico: Pitch,       kind: enum, enum: ["A", "B", "C", "D", "E", "F", "G"], required: true}
    - {name: octave, dorico: OctaveValue, kind: int,  required: true}
  doc: "Input a pitch at the caret (letter A-G in Pitch, scientific octave in OctaveValue)."
```

Parameter types are defined using the `kind` key (`str`, `int`, `enum`).
Accidentals are handled as a separate pre-step command (`NoteInput.SetAccidental?Type=...`) rather than a parameter to `NoteInput.Pitch`.
Destructive commands (such as `Edit.Delete`, `File.Close`, `File.Exit`) set `destructive: true` and require explicit confirmation.

## Command Specification & Validation (`spec.py`)

`CommandSpec` and `ParamSpec` define the schema for commands and their arguments.
The `validate` function validates provided arguments against declared parameter constraints before assembling URL query strings via `build`. Unknown arguments or invalid enum values raise `ValueError`.

## Execution Pipeline (`executor.py`)

The `execute` function encapsulates all cross-cutting dispatch concerns:
1. **Destructive Guard:** Rejects commands with `destructive: true` unless `confirm=True` is provided.
2. **Note Input Context:** Wraps commands with `requires_note_input=True` in `NoteInput.Enter` and `NoteInput.Exit`.
3. **Transport Dispatch:** Sends the assembled command string over the WebSocket client and correlates replies in FIFO order.
4. **Verification:** If the spec defines a `verify` hook, invokes the corresponding checker in `executor.VERIFIERS` to inspect the pushed status delta.

## Command Families

Category status distribution across the 348 catalog rows:

- **NoteInput (125 rows, 101 verified, 1 unavailable, 23 untested):** Handled via `NoteInputSession` in `session.py`. Manages entry, duration setting, pitch entry, and clean exit.
- **EventEdit (57 rows, 4 verified, 53 untested):** Commands operating on the active selection (navigation, transposition, rhythm adjustment). The four verified rows are `Navigate*` commands.
- **Play (31 rows, 24 verified, 2 unavailable, 5 untested):** Transport and playhead controls. `Play.StartOrStop` accepts `PlayFromLocation` targets.
- **Setup (10) and Project (8):** Structural operations (players, flows, layouts).

## Musical Domain Layer (`music/`)

- **`ScoreSpec` (`music/score.py`):** Typed contract object representing scores (`parts -> staves -> voices -> events`). Normalizes flat authoring shortcuts into canonical nested structures and bridges to `music21.stream.Score`.
- **Analysis (`music/theory.py`):** Deterministic offline music analysis functions: key estimation (`detect_key`), harmonic Roman numeral analysis (`roman_numeral_analysis`), voice leading validation (`check_voice_leading`), species counterpoint auditing (`check_species_counterpoint`), and instrument range verification (`instrument_range`).
- **Render Paths:**
  - **Caret Path (`render.py`):** Transforms a `ScoreSpec` into an ordered sequence of verified caret commands executed inside a `NoteInputSession`.
  - **MusicXML Path (`music/musicxml.py`, `render.import_musicxml`):** Serializes a `ScoreSpec` to MusicXML and imports it via `File.Open?FilterID=MusicXMLImportFilter`.

## MCP Interface (`server.py`)

The server exposes 29 tools and the `dorico://commands` resource:

1. **Connection and State:** `connect_to_dorico`, `get_status`, `read_selection`.
2. **Single-Command & Caret Tools:** `add_notes`, `add_rest`, `transpose`, `set_key_signature`, `set_time_signature`, `navigate`, `goto_bar`, `open_popover`, `playback`, `switch_mode`, `save`, `export_pdf`.
3. **Composition & File Tools:** `write_score`, `render_to_dorico`, `score_schema`, `export_musicxml`, `import_musicxml`, `read_score`, `read_project_info`.
4. **Offline Theory Tools:** `analyze_harmony`, `check_voice_leading`, `suggest_next_chord`, `instrument_range`, `check_counterpoint`.
5. **Discovery & Generic Dispatch:** `search_commands`, `run_command`, and `dorico://commands` resource.

## Verification Hooks

Post-condition verifiers in `executor.VERIFIERS` inspect status deltas pushed by Dorico after a command executes:
- `note_added`: Validates that `selectedEventType == "kNoteEvent"`, confirming that a note event was created.
