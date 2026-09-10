# Dorico Remote Control: Protocol Reference

Technical reference for external remote control of Steinberg Dorico via its WebSocket interface.
Information is derived from local application logs, key commands configuration files, and empirical probing on Dorico 6 (Windows 11).

> Note: The Remote Control API is an internal Steinberg interface without official public documentation. This document reflects verified protocol behavior.

## 1. Transport Layer

- **Protocol:** WebSocket, JSON text frames.
- **Host:** `127.0.0.1` (do not use `localhost`, as Windows resolves `localhost` to IPv6 `::1`, whereas Dorico listens on IPv4 only).
- **Port:** `4560` (scan range `4560–4565`).
- **Compatibility:** Supported since Dorico 4 and tested on Dorico 6.

## 2. Handshake & Session Management

1. Client sends: `{"message":"connect","clientName":"...","handshakeVersion":"1.0"}`
2. Dorico replies: `{"message":"sessiontoken","sessionToken":"..."}`
   - On the first connection, Dorico prompts the user for authorization in a modal dialog.
   - If rejected, Dorico terminates the socket with `kClientRejected_UserRejected`.
3. Client sends: `{"message":"acceptsessiontoken","sessionToken":"..."}`
4. Dorico confirms: `{"message":"response","code":"kConnected"}`
5. The client caches the token (e.g., `%APPDATA%\dorico-maestro\session_token.json` on Windows or `~/Library/Application Support/dorico-maestro/session_token.json` on macOS) and supplies it in step 1 on future runs to bypass confirmation.

## 3. Commands & Responses

- Send format: `{"message":"command","command":"<CommandString>","requestId":"..."}`
- Command string format: `Namespace.Command?Param1=Value1&Param2=Value2` (e.g. `NoteInput.Pitch?Pitch=C&OctaveValue=4`).
- Response format: `{"message":"response","code":"kOK"}` or `{"message":"response","code":"kError","detail":"kUnknownCommand"}`.

**FIFO Response Ordering:** Dorico responses do not include the incoming `requestId`. Responses must be correlated strictly in FIFO order.

**Command Acceptance vs Effect:** A response code of `kOK` confirms that Dorico's UI queue accepted the command. It does not guarantee that the intended musical modification took place. Verification must occur via pushed status deltas or score inspection.

**Modal Dialogs:** While a modal dialog is open in Dorico, commands sent over the socket return `kOK` immediately but remain unexecuted on the UI queue until the dialog is dismissed. `kCommandNotAllowed` is returned when a command is syntactically valid but inapplicable in the current application state.

## 4. Application Status

- There is no `Application.Status` query command (`kUnknownCommand`).
- Dorico pushes status updates (`{"message":"status", ...}`) upon connection and when internal state changes. Updates arrive as partial deltas and must be merged into a local snapshot.
- Pushed fields include: `hasScore`, `hasSelection`, `windowMode` (`kWriteMode`, etc.), `noteInputActive`, `duration` (`kCrotchet`, etc.), `selectedEventType`, `canUndo`, `rhythmicGridResolutionValue` (`kQuaver`, etc.), panel visibility, and articulation flags.
- Limitations: Status payloads do not report caret position, bar numbers, or beat offsets.
- Additional notification types: `selectionchanged`, `documentchanged`, `playbackstarted`, `playbackstopped`, `flowchanged`, `layoutchanged`.

## 5. Inspection Boundaries

The Remote Control API is selection-based:
- Only currently selected items and their rhythmic properties can be read.
- There is no native API query for arbitrary bars, tracks, or global score structure.
- For complete score analysis, export a MusicXML file and parse it with music21.

## 6. Discovering Command IDs

1. **`keycommands.json`** (and localized variants) in the Dorico installation directory (`C:\Program Files\Steinberg\Dorico 6\` on Windows, `/Applications/Dorico 6.app/Contents/Resources/` on macOS). Contains key-bindable command definitions.
2. **`application.log`** in `%APPDATA%\Steinberg\Dorico 6\` (Windows) or `~/Library/Application Support/Steinberg/Dorico 6/` (macOS). Dorico logs command IDs and arguments executed through UI interactions.

## 7. Command Catalog Overview

The 22 command namespaces in Dorico 6 (340 base key-bindable commands):

| Namespace | Count | Description |
|---|---|---|
| `NoteInput` | 123 | Note entry, durations, accidentals, intervals, rests, ties, caret navigation |
| `EventEdit` | 57 | Selection manipulation: navigation, nudging, duration adjustments, cross-stave moves |
| `Play` | 30 | Transport controls, playhead positioning, Key Editor tools |
| `Window` | 28 | Workspace mode switching, toolbars, panels, window layouts |
| `Edit` | 16 | Undo, redo, clipboard, selection, jump bar commands |
| `View` | 16 | Viewport navigation, zoom levels, track visibility |
| `Setup` | 10 | Player and instrument organization in Setup mode |
| `File` | 9 | Project creation, opening, saving, closing |
| `Project` | 8 | Flow, player, instrument, and layout configuration |
| `NoteEdit` | 8 | Diatonic, chromatic, and octave transposition, enharmonic respelling |
| `UI` | 7 | Pane focus, escape key, jump bar invocation |
| `TextEditor` | 6 | Font formatting, sizing, Unicode conversion |
| `OptionsDialog` | 5 | Navigation and filtering in options dialogs |
| `Print` | 5 | Print preview navigation and layout selection |
| `Engrave` | 2 | Engraving mode tools and options |
| `JumpBar` | 2 | Jump bar commands and go-to modes |
| `Page` | 2 | System break formatting |
| `ScrubPlayback` | 2 | Scrub playback transport controls |
| `Application` | 1 | Preferences |
| `Help` | 1 | Help overlay toggle |
| `Script` | 1 | Execute last script |
| `Video` | 1 | Video window display |
| **Total** | **340** | Base key-command catalog |

`src/dorico_maestro/commands.yaml` contains 348 commands: the 340 base IDs, four parameterized base commands (`NoteInput.Pitch`, `NoteInput.SetAccidental`, `Play.StartOrStop`, `Window.SwitchMode`), and four binary export commands (`File.Export`, `File.Export?FilterID=MusicXMLExportFilter`, `Print.ExportCurrentLayoutAsPDF`, `Print.ExportAllLayoutsAsPDF`).
Catalog status distribution: 190 verified, 23 reachable, 4 unavailable, 0 broken, 131 untested.

### Verified Core Commands (Dorico 6)

- Confirmed working: `Edit.SelectAll`, `Edit.Copy`, `Edit.SelectNone`, `NoteInput.Enter`, `NoteInput.Exit`, `NoteInput.Pitch?Pitch=C&OctaveValue=4`, `NoteInput.NoteValue?LogDuration=kQuaver`, `NoteInput.SetAccidental?Type=kSharp`, `NoteInput.MoveUpTop`, `NoteInput.MoveLeftBar`, `NoteInput.MoveDown`, `NoteInput.StartEndChord`, `Window.SwitchMode?WindowMode=kWriteMode`, `File.Save`, `Play.StartOrStop?PlayFromLocation=kStartOfFlow`, `Play.Stop`.
- Unsupported namespaces: `Navigate.*` and `Playback.*` do not exist. Navigation is handled via `EventEdit.Navigate*` (selection) and `NoteInput.Move*` (caret).
- Tier-restricted commands (`unavailable` on Elements): `NoteInput.ShowNoteInputOptions`, `Play.NavigateBackwards`, `Play.NavigateForwards`, `Script.RunLastScript`. For transport positioning, use `Play.Forward` and `Play.Rewind`.

## 8. Technical Findings & Workarounds

- **PDF and MusicXML Export:**
  `Print.ExportCurrentLayoutAsPDF` executes unattended without opening a dialog and writes a PDF adjacent to the project file *(Dorico Elements 6.2.30)*.
  `File.Export?FilterID=MusicXMLExportFilter` opens the native MusicXML export modal dialog, which requires user confirmation:
  1. Passing `File=<path>` (as accepted by `File.Open`) is ignored by the export filter; Dorico displays the modal save prompt with its default directory.
  2. `Dorico6.exe` provides no CLI export switch or background export argument.
  3. The `.dorico` ZIP archive format stores project configuration and engraving rules in `score.xml` and `scorelibrary.xml`, but does not contain a raw parseable notation stream.
  These constraints establish the export confirmation dialog as a protocol boundary in the Remote API *(Dorico Elements 6.2.30)*. Consequently, full-score inspection (`read_open_score`) requires confirming the export dialog once and subsequently parses the resulting MusicXML.
- **MusicXML Import:**
  `File.Open?File=<path>&FilterID=MusicXMLImportFilter` imports a MusicXML file as a new flow *(Dorico Elements 6.2.30)*. Paths must use forward slashes without URL encoding.
- **Modal Dialog Detection:**
  While a modal dialog is open in Dorico, incoming commands return `kOK` but do not mutate the score *(Dorico Elements 6.2.30)*.
- **Diatonic Interpretation of `NoteInput.Pitch`:**
  Dorico interprets pitch letters diatonically relative to the prevailing key signature; unadorned pitch letters automatically adopt key-signature accidentals (for example, in A-flat major, letters D, E, A, and B are flattened). Because Remote API responses do not report implicit accidentals, `session.pitch_commands` prefixes every note with an explicit `NoteInput.SetAccidental` (`kNatural` for unaltered pitches) to enforce absolute pitch spelling. Dorico automatically suppresses redundant natural signs in engraving, rendering naturals only where required by the key signature *(Dorico Elements 6.2.30)*.
- **Selection-Based Caret Placement (`NoteInput.Enter`):**
  Entering note input places the caret at the start of the active selection. Dispatching `NoteInput.Exit` -> `Edit.SelectAll` -> `NoteInput.Enter` reliably positions the caret at bar 1 of the top staff in three commands regardless of flow length *(Dorico Elements 6.2.30)*. The initial `Exit` ensures deterministic execution because `Enter` toggles note-input mode. This replaces bar-by-bar rewinding and functions uniformly across scores of any measure count.
- **Caret Position on Note Input Re-Entry:**
  Re-entering note input does not restore prior caret coordinates. When no selection is active, `NoteInput.Exit` followed by `NoteInput.Enter` reactivates note input, but caret placement defaults to the current visible viewport boundary rather than retaining earlier coordinates *(Dorico Elements 6.2.30)*. Consequently, `NoteInputSession` manages input state only; explicit bar navigation must be dispatched when a target measure is required.
- **Command Throughput and Delay Independence:**
  Dorico's Remote API reliably processes consecutive commands over the WebSocket connection without dropped packets or required inter-command delays *(Dorico Elements 6.2.30)*. Perceived positioning discrepancies stem from viewport-relative caret placement upon re-entry rather than transmission loss. The transport client therefore dispatches commands without artificial throttling.
- **`NoteInput.Enter` Toggle Behavior:**
  Sending `NoteInput.Enter` while note input is already active toggles note input off. `NoteInput.Exit` is idempotent and safe to send when note input is inactive. Sending `Exit` before `Enter` ensures a clean, active note-input state *(Dorico Elements 6.2.30)*.
- **Pickup Bar Numbering:**
  Dorico does not number an opening pickup measure as bar 1. Step-wise bar navigation from flow start must account for pickup presence to align with printed measure numbers. Nothing in the pushed status reveals a pickup, so it has to be told rather than detected *(Dorico Elements 6.2.30)*. `goto_bar` and `open_popover` accept a `pickup` flag, and `read_open_score` reports whether an upbeat measure is present.
- **Caret Dead-Reckoning:**
  Because Dorico does not expose caret coordinates via the Remote Control API, `goto_bar` deterministically repositions the caret: it jumps to the flow start with the sequence above, then steps forward (`NoteInput.MoveRightBar`, `NoteInput.MoveDown`, and `NoteInput.MoveRight`). The jump costs the same four commands at any distance, so no caller has to know how long the flow is and there is no length beyond which positioning degrades. Whether `MoveRightBar` clamps at the end of the flow the way `MoveLeftBar` clamps at its start has not been established.
- **Dynamics and Articulations:**
  `EventEdit.*` commands operate only on existing selections. Articulations are applied via `NoteInput.SetArticulation?Value=...`. Dynamics are entered by opening the dynamic popover (`NoteInput.CreateDynamic`).

## References

- Dorico.Net library: https://github.com/scott-janssens/Dorico.Net
- Remote Control API .NET discussion (Steinberg Forums): https://forums.steinberg.net/t/remote-control-api-net-library/884017
- Steinberg Key Commands Documentation: https://www.steinberg.help/r/dorico/doricofirststeps/5.1/en/dorico_first_steps/topics/first_steps_intro/first_steps_key_commands_r.html
- Local reference files: `scripts/probe_commands.py`, `keycommands.json`, `application.log`.
