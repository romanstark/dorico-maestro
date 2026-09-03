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
5. The client caches the token (e.g., `%APPDATA%\dorico-maestro\session_token.json`) and supplies it in step 1 on future runs to bypass confirmation.

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

1. **`keycommands.json`** (and localized variants) in the Dorico installation directory. Contains key-bindable command definitions.
2. **`application.log`** in `%APPDATA%\Steinberg\Dorico 6\`. Dorico logs command IDs and arguments executed through UI interactions.

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
  `Print.ExportCurrentLayoutAsPDF` executes unattended without opening a dialog and writes a PDF adjacent to the project file *(tested against Dorico Elements 6.2.30)*.
  `File.Export?FilterID=MusicXMLExportFilter` opens the MusicXML export dialog requiring user interaction.
- **MusicXML Import:**
  `File.Open?File=<path>&FilterID=MusicXMLImportFilter` successfully imports a MusicXML file as a new flow *(tested against Dorico Elements 6.2.30)*. Paths must use forward slashes and avoid URI encoding.
- **Modal Dialog Detection:**
  While a dialog is active, incoming commands return `kOK` but do not alter score state.
- **Caret Dead-Reckoning:**
  Because Dorico does not expose caret coordinates, `goto_bar` deterministically repositions the caret by moving to bar 1 and stepping forward (`NoteInput.MoveRightBar`, `NoteInput.MoveDown`, and `NoteInput.MoveRight`).
- **Dynamics and Articulations:**
  `EventEdit.*` commands operate only on existing selections. Articulations are applied via `NoteInput.SetArticulation?Value=...`. Dynamics are entered by opening the dynamic popover (`NoteInput.CreateDynamic`).

## References

- Dorico.Net library: https://github.com/scott-janssens/Dorico.Net
- Remote Control API .NET discussion (Steinberg Forums): https://forums.steinberg.net/t/remote-control-api-net-library/884017
- Steinberg Key Commands Documentation: https://www.steinberg.help/r/dorico/doricofirststeps/5.1/en/dorico_first_steps/topics/first_steps_intro/first_steps_key_commands_r.html
- Local reference files: `scripts/probe_commands.py`, `keycommands.json`, `application.log`.
