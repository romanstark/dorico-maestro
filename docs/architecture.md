# Architecture — Dorico Maestro

How we implement **all** of Dorico's commands without writing per-command code:
describe each command as **data**, and write the build/execute/verify machinery
**once**. Approach:

- **Data-driven registry at runtime** — a YAML catalog + generic builder/executor.
  Adding a command = a data row, not new code.
- **Few high-level MCP tools + a generic `run_command` + a command *resource*** —
  not 340 tool definitions.

See also: [protocol.md](protocol.md) (transport & constraints),
[dorico_command_catalog.md](dorico_command_catalog.md) (the 340 IDs).

## Layers

```
commands.yaml            the catalog: every command as data (id, params, status, flags, doc)
   │  (loaded at runtime)
spec.py                  CommandSpec / ParamSpec + build() + validate()      ← written once
   │
registry.py              load catalog → specs; look up by id/category; status index
   │
executor.py              ONE execute(): connect · note-input lifecycle · send (FIFO) ·
   │                     kOK/kError→Result · optional verify · destructive guard   ← written once
client.py                transport (done)
models.py                enums / value types (pure stdlib) + score value objects
music/score.py           ScoreSpec: the typed score model + music21 bridge
music/theory.py          key/harmony/voice-leading/range/counterpoint analysis (music21)
music/musicxml.py        MusicXML round-trip + ScoreSpec ⇄ MusicXML + import-command assembly
render.py                ScoreSpec → live caret plan (pure planner) + async send (the only new
   │                     module that talks to Dorico)
server.py                high-level tools + run_command + dorico://commands resource
```

Design rule: **transport, command semantics, and musical logic never mix.**
`client.py` knows sockets, not music. `music/` knows music21, not sockets.
`executor.py` glues a spec to the client. `server.py` maps musical intent to specs.

## The catalog (single source of truth)

`src/dorico_maestro/commands.yaml` — one entry per command:

```yaml
- id: NoteInput.Pitch
  category: NoteInput
  status: verified            # verified | broken | untested
  requires_note_input: true   # executor enters/exits note input around it
  destructive: false          # if true, executor requires explicit confirmation
  params:
    - {name: pitch,  dorico: Pitch,       required: true}
    - {name: octave, dorico: OctaveValue, type: int, required: true}
    - {name: accidental, dorico: null, enum: [Sharp, Flat, Natural]}  # handled specially
  verify: note_added          # optional: name of a post-condition checker
  doc: "Input a pitch at the caret."
```

Seeding & enrichment:
- `scripts/sync_catalog.py` extracts the raw 340 IDs from Dorico's `keycommands.json`
  and **merges** them into `commands.yaml` *without clobbering* hand-added params,
  status, flags or docs (new IDs appended as `status: untested`, missing IDs flagged).
- Humans enrich entries over time (param names/types, flags, verify hooks).
- `scripts/probe_commands.py` flips `status` verified/broken from live results.

(YAML for authoring ergonomics — comments, readability. `pyyaml` dependency; a
generated `catalog.json` is a zero-dep fallback if we ever want it.)

## Spec + builder (`spec.py`) — written once

```python
@dataclass
class ParamSpec:
    name: str
    dorico: str | None          # Dorico query key; None = handled by a custom builder
    type: str = "str"           # str | int | enum
    enum: list[str] | None = None
    required: bool = False
    cast: Callable = str        # value transform (e.g. NoteDuration -> "4")

@dataclass
class CommandSpec:
    id: str
    category: str
    params: list[ParamSpec] = field(default_factory=list)
    status: CmdStatus = CmdStatus.UNTESTED
    requires_note_input: bool = False
    destructive: bool = False
    verify: str | None = None
    doc: str = ""

def build(spec: CommandSpec, **args) -> str:
    _validate(spec, args)                       # required / enum / type
    q = "&".join(f"{p.dorico}={_encode(p.cast(args[p.name]))}"
                 for p in spec.params if p.dorico and p.name in args)
    return f"{spec.id}?{q}" if q else spec.id
```

## Executor (`executor.py`) — where all shared behavior lives

```python
async def execute(client, registry, cmd_id, *, confirm=False, **args) -> Result:
    spec = registry.get(cmd_id)
    if spec.destructive and not confirm:
        return Result.blocked(spec, "destructive; pass confirm=True")
    async with note_input_if_needed(client, spec):     # Enter → … → Exit, guaranteed
        resp = await client.send(build(spec, **args))
    result = Result.from_response(resp, spec)           # kOK/kError → structured
    if spec.verify:                                     # kOK ≠ effect → check
        result.verified = await VERIFIERS[spec.verify](client, args)
    return result
```

Cross-cutting concerns, each solved **once** here: connection, note-input
lifecycle, FIFO send, error mapping, verification, the destructive guard.

## Hard command families → shared patterns (not per-command code)

- **NoteInput (123):** `NoteInputSession` context manager — enter → set
  duration/accidental/chord-mode → pitches/rests → exit. All note entry flows
  through it. `accidental` and chord-mode are pre-steps it injects.
- **EventEdit (57):** `select_then_apply(selector, spec, **args)` helper — most
  need an active selection; the selection strategy is shared.
- **Play (30):** mostly parameterless toggles → plain `execute()`.
- **Setup / Project:** structural creators (instruments, flows, layouts) → plain
  `execute()` with params.

## The "music brain" — `ScoreSpec`, analysis, and the two write paths

Above the single-command surface sits a **pure musical layer** that reasons about
whole scores, so the assistant can draft, analyse and refine music before (or
instead of) touching Dorico.

- **`ScoreSpec` (`music/score.py`)** — the single typed contract object. The LLM
  emits a JSON dict (`parts → staves → voices → events`, plus a flat
  `part.events` authoring shortcut); `score_from_dict` normalises + validates it
  (path-tagged `ScoreSpecError`, pitch grammar delegated to `session.parse_pitch`
  so it never drifts from the caret path), and `score_to_music21` bridges to
  music21 for MusicXML and analysis.
- **Analysis (`music/theory.py`)** — offline, deterministic helpers over
  `ScoreSpec`/pitch lists: `detect_key`, `roman_numeral_analysis`, `find_parallels`,
  `check_voice_leading`, `suggest_cadence`/`suggest_next_chord`, `check_ranges`,
  `check_species_counterpoint`. No Dorico connection.
- **Two write paths.** The **caret path** (`render.py`) is GUARANTEED zero-manual-step:
  a *pure planner* (`plan_staff`/`plan_flow`) turns a `ScoreSpec` into an ordered
  list of **verified** commands, and thin async wrappers send them inside a
  `NoteInputSession`. It cannot enter popover-only elements (key/time/clef/named
  dynamics/tempo) — those are dropped with a warning. The **MusicXML path**
  (`music/musicxml.py` + `render.import_musicxml`) writes full fidelity to disk and
  imports it via the Remote API (`File.Open?FilterID=MusicXMLImportFilter`); the import
  creates a new flow and may prompt to create a player. See [protocol.md](protocol.md) §8.

The import graph stays acyclic (`models ← score ← {theory, musicxml, render}`,
`musicxml → theory → score`); `models.py` stays pure stdlib (music21 class names
live as plain strings in its maps) and `render.py` is the only new module importing
the client/session.

## MCP surface (`server.py`)

1. **High-level single-command tools** — intent, not raw commands:
   `add_notes`, `add_rest`, `transpose`, `set_key_signature`, `set_time_signature`,
   `navigate`, `playback`, `switch_mode`, `save`. Each composes one or more specs
   via the executor, adding musical semantics + verification.
2. **Score-composition tools (music brain)** — whole-score intent:
   - `write_score(score, method, preflight)` — the headline: validate, optionally
     preflight (range + voice-leading, advisory only), then write via the caret
     path (default) or MusicXML.
   - `render_to_dorico(score, dry_run)` — explicit caret render; `dry_run=True`
     returns the planned command list and sends nothing (the composer's preview).
   - `export_musicxml(score, path)` / `import_musicxml(path)` — offline MusicXML
     write / best-effort import.
   - `analyze_harmony`, `check_voice_leading`, `suggest_next_chord`,
     `instrument_range`, `check_counterpoint` — offline theory (no Dorico needed).
3. **`run_command(id, params)`** — generic escape hatch for the long tail; validates
   against the registry and reports `status`.
4. **`dorico://commands` resource** — the catalog exposed as an MCP resource so the
   assistant can *discover* commands (filter by category/status) without carrying
   340 tool definitions in context.

## Verification (`verify` hooks)

Because `kOK` only means *accepted*, specs may name a verifier: a small async
function checking a post-condition from pushed status (e.g. `canUndo` flipped,
`selectedEventType == kNoteEvent`) or, for structural changes, a MusicXML diff.
Verifiers live in one place and are reused across commands.

## Directory

```
src/dorico_maestro/
  client.py         transport (done)
  spec.py           CommandSpec/ParamSpec + build + validate
  registry.py       catalog loader + lookups
  executor.py       execute() + note_input_if_needed + verifiers
  session.py        NoteInputSession, select_then_apply
  commands.yaml     the catalog (data)
  models.py         enums / value types + score value objects (TimeSignature, maps)
  render.py         ScoreSpec → caret plan (pure) + async send (the only new Dorico-facing module)
  server.py         MCP tools + run_command + commands resource
  music/
    score.py        ScoreSpec model + music21 bridge
    theory.py       key/harmony/voice-leading/range/counterpoint analysis
    musicxml.py     MusicXML round-trip + ScoreSpec ⇄ MusicXML + import-command assembly
scripts/
  sync_catalog.py   seed/update commands.yaml from keycommands.json
  probe_commands.py update status live
```
