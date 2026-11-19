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

`src/dorico_maestro/commands.yaml` contains 369 commands: the 340 base IDs, four parameterized base commands (`NoteInput.Pitch`, `NoteInput.SetAccidental`, `Play.StartOrStop`, `Window.SwitchMode`), four binary export commands (`File.Export`, `File.Export?FilterID=MusicXMLExportFilter`, `Print.ExportCurrentLayoutAsPDF`, `Print.ExportAllLayoutsAsPDF`), and six `MusicalManipulation` menu commands (retrograde and inversion).
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
- **Keeping the Caret Between Calls:**
  Because re-entry moves the caret, a tool that writes at the caret leaves a running note input alone: `goto_bar` followed by `add_notes`, `add_rest` or a note-input command from `run_command` writes where `goto_bar` stopped, and successive calls continue from there. Only when note input is off do they enter and exit it themselves *(Dorico Elements 6.2.30)*.
- **Rest Input and the Y Key:**
  `NoteInput.RestMode` (the comma key) only toggles rest input; it places nothing. With rest input on, `NoteInput.Pitch?Pitch=X` (the Y key) inputs a rest of the current duration; with it off, the same command repeats the previous pitch. Pitch letters input notes in either state. A rest is therefore sent as rest mode on, `Pitch=X`, rest mode off, leaving the state as found *(Dorico Elements 6.2.30)*.
- **Pushed Status Misses Some Changes:**
  Within one connection Dorico pushes `restMode` turning on but not turning off, so the merged snapshot keeps reporting `true`. A `{"message":"getstatus"}` request answers with the current values; `DoricoClient.fresh_status` sends it, and note entry reads note input and rest mode through it *(Dorico Elements 6.2.30)*.
- **Stale Session Token:**
  A saved token that Dorico no longer knows (the client was removed in Preferences > Remote) gets no answer at all. The client waits a few seconds, deletes the token and repeats the handshake without one, which brings back the approval prompt. With an MSIX-packaged client on Windows, writes to `%APPDATA%` are redirected, so the token lives under `%LOCALAPPDATA%\Packages\<app>\LocalCache\Roaming\dorico-maestro`; `connect_to_dorico` reports the path it uses. Reported on Windows 11 *(Dorico 6.2.30)*; the retry is unit-tested, not reproduced live.
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

- **Read Requests Besides Commands:**
  The Remote API answers `getappinfo` (`info: version` gives `variant` and `number`, e.g. `Elements`, `6.2.40`), `getcommands` (every command the edition offers, with required and optional parameters), `getflows`, `getlayouts`, `getproperties` (selection only), `getlibrarycollections`, `getlibraryentities` (e.g. expression map ids) and `getplaybacktechniques` (the expression maps of the selected instruments). Without an active project window a request is answered with a plain `response` carrying `kNoScore`, in FIFO order with command replies *(Dorico Elements 6.2.40)*.
- **Playback Setup:**
  `Play.ActivateProject` toggles: sent to an active project it deactivates it. `Play.LoadPlugin` is refused with `kCommandNotAllowed` until the project is activated. `SlotID` is 0-based while the window numbers slots from 1, and slot 1 is the click. An endpoint is `1024 × SlotID + channel − 1`. `Play.SetEndpoint` with an instrument id that does not exist answers `kOK` and changes nothing. Each change pushes `playbackconfigurationchanged` without a payload. While the audio engine syncs a change (`Syncing Audio Engine` to `Sync complete` in the application log) further playback changes are refused with `kCommandNotAllowed`; the same commands a few seconds apart all apply, so `playback_setup` sends a refused change again for up to ten seconds. The audio engine's plugin cache can list two instrument classes under one name: HALion Sonic 7.5 registers the full instrument (`Instrument|Synth|Sampler`) and a content player (`ContentPlayer`) as "HALion Sonic", next to content players such as Symphonic Orchestra; `playback_setup` takes the full instrument for an exact name *(Dorico Elements 6.2.40)*.
- **Popover Commands With a Definition:**
  `NoteInput.CreateDynamic?Definition=mf` creates the dynamic at the caret without opening the popover and leaves it selected (`kDynamicGroupEvent`); `NoteInput.CreateTempo?Definition=q = 60` does the same for a tempo (`kImmediateTempoChangeEvent`), though the status first reads `kMixed` for a moment *(Dorico Elements 6.2.40)*.
- **Flows and Project Files:**
  `Project.Flow.Rename` and `Project.Flows.Delete` answer `kCommandNotAllowed` in Write mode and work in Setup mode; deleting opens a confirmation dialog. `File.SaveAs?File=<path>` and `File.CloseProject?OpenScoreID=<id>&Save=false` run without a dialog; `File.Revert` asks first and reopens the project under a new `openScoreID`. `File.Save` answers before the file is written, and `Notify=true` adds no message; Dorico's application log records `notifyPostCommandExecute` when a command has finished *(Dorico Elements 6.2.40)*.
- **The Saved Project (`score.dtn`, `enginedata`):**
  `score.dtn` is a document tree: `u32 4, u32 1`, a key table and a value table (each `u32` count and NUL-terminated strings), `u32` entry count, then entries in pre-order. An entry is a type byte, a key index and, by type, a value index (leaf), one zero (empty), or a zero, a child count and child entry indices (dict, list). The type's low two bits give the kind; the upper bits differ between files (`0xBC` and `0x7C` seen). It holds players with `instrumentID`, the plugin rack (`playModeData/pluginRackContents`), each instrument's endpoints and `syncedEndpoints`. `supplementary_data/vstaudioengine/enginedata` is two length-prefixed strings and then records (name, `u64` length, payload): `MixerDocument` XML lists `<instrument id name slot uid>`, and each `PluginState` is `u64 id, u32 n, VST3 preset, u32 n, preset file name`. Dorico opens a project whose `PluginState` was replaced and keeps the new state on its next save *(Dorico Elements 6.2.40)*.
- **Endpoints of a Replaced Slot:**
  `1024 × SlotID + channel − 1` holds for a slot loaded once. A project whose slot had been replaced in the field kept its instruments on 2048 and up while the rack listed the plugin at `SlotID` 1 and the engine synced 1024 and up, and another backup listed one plugin in Dorico's rack while the engine hosted a different one; neither shows in the Remote API. `read_playback_setup` reports both from the saved file *(Dorico Elements 6.2.40)*.
- **Player Names, Templates and Presets by Command:**
  `Project.Player.Rename?PlayerID=…&Name=…` works in Setup mode and is refused in Write mode. `Play.ApplyPlaybackTemplate?PlaybackTemplateID=playbacktemplate.auto` rebuilds the setup from the template: it reloads the template's sounds and resets routing and expression maps. `Play.LoadPluginPreset` answers `kOK` and stores `PresetURI` as the channel's `halionPresetURI` in `score.dtn`, but the plugin kept its program, also after the project was deactivated and activated again; a `?` inside the URI must be sent as `\?`, while `%3F` is stored literally. A plugin's own window can be photographed with an ordinary screenshot after `Play.ShowPlugin` *(Dorico Elements 6.2.40)*.
- **Command Values:**
  Dorico logs a comma inside a value as `\,` (e.g. `Position=32\, 1`). A value containing `&`, `?` or `#` cannot be sent *(Dorico Elements 6.2.40)*.

## References

- Dorico.Net library: https://github.com/scott-janssens/Dorico.Net
- Remote Control API .NET discussion (Steinberg Forums): https://forums.steinberg.net/t/remote-control-api-net-library/884017
- Steinberg Key Commands Documentation: https://www.steinberg.help/r/dorico/doricofirststeps/5.1/en/dorico_first_steps/topics/first_steps_intro/first_steps_key_commands_r.html
- Local reference files: `scripts/probe_commands.py`, `keycommands.json`, `application.log`.
