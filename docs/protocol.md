# Dorico Remote Control — protocol reference

Reference for driving Dorico from the outside. Sources: Steinberg's shipped
files, third-party libraries, community posts, and reverse engineering against
**Dorico 6** on Windows 11.

> The API is **not officially documented** publicly; Steinberg point developers to
> contact them for full docs. Treat this file as the living ground truth.

## 1. Transport

- **WebSocket**, JSON text messages.
- Host **`127.0.0.1`** — *not* `localhost`. On Windows `localhost` resolves to IPv6
  `::1` first, which dead-ends (Dorico listens IPv4-only) and stalls short connect
  timeouts.
- **Port**: `4560`, with `4560–4565` as the documented scan range.
- Available since **Dorico 4**; works on **Dorico 6** (also reported on
  Dorico Pro 5.1.70).

## 2. Handshake & session

1. Client → `{"message":"connect","clientName":"…","handshakeVersion":"1.0"}`
2. Dorico → `{"message":"sessiontoken","sessionToken":"…"}`
   - **First time**, Dorico shows a *user approval dialog*; the composer must accept.
   - Rejection closes the socket with `kClientRejected_UserRejected`.
3. Client → `{"message":"acceptsessiontoken","sessionToken":"…"}`
4. Dorico → `{"message":"response","code":"kConnected"}`
5. Persist the token (e.g. `%APPDATA%\dorico-maestro\session_token.json`) and send
   it in step 1 next time to skip the dialog.

## 3. Commands & responses

- Send: `{"message":"command","command":"<CommandString>","requestId":"…"}`
- Command string format: `Namespace.Command?Param1=Value1&Param2=Value2`
  (e.g. `NoteInput.Pitch?Pitch=C&OctaveValue=4`).
- Response: `{"message":"response","code":"kOK"}` or
  `{"message":"response","code":"kError","detail":"kUnknownCommand"}`.

⚠️ **Responses contain NO `requestId`.** You cannot correlate by ID. Dorico answers
in order → **correlate replies to requests FIFO**.

⚠️ **`kOK` ≠ effect.** It means "command accepted", not "did what you wanted".
Verify via pushed status, MusicXML round-trip, or the composer's eyes.

## 4. Status (pushed, not queried)

- There is **no** `Application.Status` command (returns `kUnknownCommand`).
- Dorico **pushes** `{"message":"status", …}` right after connect and on change,
  as **partial deltas** — merge them into an accumulated snapshot.
- Rich fields incl. `hasScore`, `hasSelection`, `windowMode` (`kWriteMode`…),
  `noteInputActive`, `duration` (`kCrotchet`…), `selectedEventType`, `canUndo`,
  panel/track visibility, articulation toggles, etc.
- Other pushed message types: `selectionchanged`, `documentchanged`,
  `playbackstarted/stopped`, `flowchanged`, `layoutchanged` (names per client libs).

## 5. The read limitation (critical)

The API is **selection-based only**. You can read the *currently selected* items and
their properties — but there is **no way to query arbitrary bars/staves or the
surrounding context** (independently confirmed by the Dorico.Net author). 

→ For whole-score understanding, use **MusicXML** + `music21`. But note the export
gap below.

## 6. Discovering real command IDs

Two authoritative, local sources — no guessing:

1. **`keycommands.json`** (and localized `keycommands_<lang>.json`) in
   `…\Program Files\Steinberg\Dorico6\`. The complete catalog of command IDs, grouped
   by context. We derived [`dorico_command_catalog.md`](dorico_command_catalog.md)
   from it — **340 commands** in Dorico 6.
2. **`application.log`** in `…\AppData\Roaming\Steinberg\Dorico 6\`. Dorico echoes the
   command for every UI action it performs (e.g. `File.Open?File=…`). Do the action
   in the UI, read the exact command + parameters back. Best way to learn parameter
   names/values.

## 7. Command landscape (Dorico 6, 340 commands)

| Namespace | # | What |
|---|---|---|
| `NoteInput` | 123 | note entry, durations, accidentals, intervals, rests, ties, caret |
| `EventEdit` | 57 | editing selected events (dynamics, articulations, properties…) |
| `Play` | 30 | playback / record / transport |
| `Window` | 28 | mode switching, panels, tabs |
| `Edit` | 16 | undo/redo, copy/paste, select, go-to, breaks, markers |
| `View` | 16 | zoom, track/overlay visibility |
| `Setup` | 10 | players/instruments arrangement |
| `File` | 9 | new/open/save/close/score-info |
| `Project` | 8 | flows, instruments, players, layouts |
| `NoteEdit` | 8 | pitch up/down (dia/chromatic/octave), respell |
| others | ~28 | UI, TextEditor, Print, OptionsDialog, Engrave, Page, JumpBar, Video, Script, Help |

Full list: [`dorico_command_catalog.md`](dorico_command_catalog.md).

### Real command names worth knowing (correct the common wrong guesses)

| Want | ❌ common wrong guess | ✅ real (Dorico 6) |
|---|---|---|
| Playback start | `Playback.Play` | `Play.StartOrStop?PlayFromLocation=kPlayhead` (also `kSelection`, `kStartOfFlow`) |
| Playback stop | `Playback.Stop` | `Play.Stop` |
| Go to bar | `Navigate.GoToBar?Bar=N` | `Edit.GoTo` (dialog) · `JumpBar.GotoMode` |
| Deselect | — | `Edit.SelectNone` |
| Close project | `File.Close` | `File.CloseProject` (both exist) |
| MusicXML import | — | `File.Open?File=…&FilterID=MusicXMLImportFilter` ✅ **works** — pass the path RAW (forward slashes); see §8 |
| Transpose pitch | `Edit.TransposeUpStep` | `NoteEdit.PitchUp` / `PitchUpChromatic` / `PitchUpOctave` |
| Run a Lua script | — | `Script.RunLastScript` |

### Verified working (Dorico 6)
`kOK` + confirmed effect: `Edit.SelectAll`, `Edit.Copy`, `Edit.SelectNone`,
`NoteInput.Enter`, `NoteInput.Exit`, `NoteInput.Pitch?Pitch=C&OctaveValue=4`,
`NoteInput.NoteValue?LogDuration=kQuaver` (duration; British value names — the real
command), `NoteInput.SetAccidental?Type=kSharp`, caret staff-switching
`NoteInput.MoveUpTop` / `NoteInput.MoveLeftBar` / `NoteInput.MoveDown`, chord stacking
`NoteInput.StartEndChord`, `Window.SwitchMode?WindowMode=kWriteMode`, `File.Save`
(already-saved project), `Play.StartOrStop?PlayFromLocation=kStartOfFlow` / `Play.Stop`.
Rejected (`kUnknownCommand`, not real Remote-API commands): all `Navigate.*`, all
`Playback.*`, `Application.Status/GetCommands/GetFlows/GetLayouts`,
`Edit.GetProperties`, and `NoteInput.SetDuration` (the duration command is
`NoteInput.NoteValue?LogDuration=k…`).

## 8. Known gaps / open questions

- **No `File.Export` command** in the key-command catalog. Exporting PDF/MusicXML is
  a dialog action and may not be remotely triggerable as a plain command — needs
  investigation (application.log during an export; or UI automation; or a watched
  export folder). This directly affects the `read_full_score` (MusicXML) plan.
- **MusicXML *import* over the API works.** `File.Open?File=…&FilterID=MusicXMLImportFilter`
  returns `kOK` and imports the file as a **new flow**. Pass the path **raw with forward
  slashes**: Dorico does **not** URL-decode the `File=` value, and Windows accepts `/`; a
  path containing `& ? #` cannot be passed (it would break the command's mini-query).
  Dorico runs its normal import, so it creates a new flow and may pop a "create a new
  player?" dialog the user must confirm. For an in-place write into the existing sheet,
  use the caret path (`write_score(method="caret")`). Do **not** fire the bare
  `File.Open?File=…` (association open) — it can raise a modal file picker.
  Lesson: don't call something "broken" until you've seen what the *user's screen* shows;
  the failing dialog was invisible to the API but obvious to the composer.
- **Caret/position model**: with selection-only reads, how to reliably know/set where
  notes will land? Likely drive via selection + `NoteInput.*` caret moves.
- Does Dorico expose any command-list/capability query over the API? (`Application.*`
  query commands we tried are all fake.)
- Are `EventEdit.*` the route for dynamics/articulations on a selection?

## Sources

- Dorico.Net — .NET Remote Control API library — https://github.com/scott-janssens/Dorico.Net
- Remote Control API .NET Library (Steinberg Forums) — https://forums.steinberg.net/t/remote-control-api-net-library/884017
- Dorico 4 Remote Control API (SoundFlow forum) — https://forum.soundflow.org/-6393/dorico-4-remote-control-api
- Key commands in Dorico (steinberg.help) — https://www.steinberg.help/r/dorico/doricofirststeps/5.1/en/dorico_first_steps/topics/first_steps_intro/first_steps_key_commands_r.html
- `happycastle114/dorico-mcp-server` (prior art we learned the protocol shape from) — https://github.com/happycastle114/dorico-mcp-server
- Local sources: `scripts/probe_commands.py`, `keycommands.json`, `application.log`.
