# Dorico Maestro

> An AI composition and orchestration partner for **Steinberg Dorico**. Speak in natural language to write notes directly into your score, audit voice leading, analyze harmony, check instrument ranges, and manage engraving layouts, right inside your open project.

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/) [![Dorico 4+](https://img.shields.io/badge/Dorico-4%20%7C%205%20%7C%206-orange.svg)](https://www.steinberg.net/dorico/) [![No telemetry](https://img.shields.io/badge/telemetry-none-brightgreen.svg)](#your-music-stays-yours)

---

## Stay in the creative flow

Composing and engraving music in a notation program requires deep focus, but constantly breaking your train of thought to navigate popovers, click through staves, and proofread voice leading can pull you out of the zone.

**Dorico Maestro** acts like an assistant orchestrator and copyist sitting beside you at the scoring desk:
- **Keep your eyes on the score:** Dorico stays open in front of you. You watch the notation appear, evaluate voicing and balance, and make the musical decisions.
- **Natural scoring dialogue:** Ask for chord voicings, chorale harmonizations, counterpoint audits, transpositions, or layout adjustments in plain musical language.
- **Real notation, real scores:** Nothing is simulated or pre-rendered. Everything is written directly into your active Dorico project through the live caret or standard MusicXML as editable Steinberg Dorico notation events.

---

## How it feels at the scoring desk

Here is what collaborating with Maestro looks like during a scoring session:

### 1. Score Writing & Harmony
> **You:** *"Write a 4-part chorale phrase in D minor at the caret. Soprano starts on F4 and moves step-wise down to D4. Build a classical SATB voicing with smooth contrary motion."*
>
> **Assistant:** Entered a 4-bar phrase in D minor across Soprano, Alto, Tenor, and Bass staves:
> - **Soprano:** F4 (half note) -> E4 (quarter note) -> D4 (half note).
> - **Alto / Tenor:** Inner voices provide full triadic harmony with step-wise motion.
> - **Bass:** Contrary root motion resolving firmly to the final tonic cadence.
> - **Verification:** Audited voice leading: no parallel fifths or octaves detected.
>
> Notes are in your score and ready to play.

### 2. Voice Leading & Counterpoint Audit
> **You:** *"Check the active string quartet passage for voice crossing, parallel octaves, and unresolved leading tones."*
>
> **Assistant:** 
> - Audited the four parts using local music theory analysis.
> - **Result:** Detected parallel octaves between Violin 2 and Cello on beat 3 of bar 14 (A3->B3 and A2->B2).
> - **Voice Leading:** Unresolved leading tone in Viola (G#4 steps down to E4 instead of resolving up to A4).
> - **Recommendation:** Resolve Viola up to A4 to complete the cadence and hold Violin 2 on E4 to eliminate the octave parallel.

### 3. Orchestration & Engraving
> **You:** *"Check if the Cor Anglais part is within comfortable range, transpose the French Horn in F down a minor third, and export the current layout to PDF."*
>
> **Assistant:** 
> - Verified Cor Anglais compass: all notes fall between G3 and C6 (well within comfortable orchestral range).
> - Transposed the French Horn selection down a minor third using chromatic transposition with flat-enharmonic spelling.
> - Exported the current layout directly to PDF into your project directory without opening a modal dialog.

---

## What you can do

| Workflow | Example prompt | What Maestro does |
|---|---|---|
| **✍️ Caret Note Entry** | *"Input a 4-bar melody at the caret in G major with dotted quarters and eighths"* | Steps through the score at the caret, sets durations and pitches, applies accidentals and ties |
| **🎼 Full-Score Writing** | *"Create a string quartet sketch with Violin 1, Violin 2, Viola, and Cello"* | Generates complete multi-part scores with staves, voices, key signatures, and meters |
| **🔍 Harmony & Analysis** | *"Analyze the Roman numeral harmony of this progression in E-flat major"* | Performs local key detection, chord labeling, and harmonic analysis |
| **📐 Voice Leading & Rules** | *"Audit this counterpoint passage against first-species voice leading rules"* | Identifies parallel fifths, octaves, voice crossing, and forbidden melodic leaps |
| **🎺 Orchestration & Range** | *"Check whether the Trumpet 1 line exceeds the comfortable orchestral range"* | Validates instrument compasses and warns of difficult register extremes |
| **🖨️ Engraving & Layout** | *"Switch to Print mode and export the full score layout as PDF"* | Navigates Dorico modes, switches views, and runs unattended PDF exports |
| **📂 Offline Project Inspection** | *"Read the flows, players, and metadata from this .dorico file"* | Inspects `.dorico` project archives directly on disk without launching Dorico |
| **🔄 Enharmonic & Transpose** | *"Transpose selected notes up a whole tone and respell using sharps"* | Dispatches diatonic/chromatic transpositions and enharmonic respelling |

---

## Why Maestro is reliable

Most AI music tools generate raw MIDI or static audio files without understanding the underlying notation rules.

Dorico Maestro is built on a **robust, safety-first architecture**:
- **Lifecycle & Caret Safety:** Note input commands use strict session context managers (`try ... finally: NoteInput.Exit`). Even if an operation fails or arguments are malformed, Dorico is never left stranded in an open note-input state.
- **Destructive Command Guard:** Commands that could discard musical work (`Edit.Delete`, `File.Close`, `File.Quit`) are automatically blocked unless explicitly authorized with `confirm=True`.
- **Overwrite Mode Transparency:** Because Dorico defaults to Overwrite mode and the Remote Control API cannot read bar contents back, Maestro monitors `noteInputMode` and flags `displaces_existing: true` in its response whenever notes land in Overwrite mode.
- **Data-Driven & Verified:** Built on a comprehensive catalog of 348 commands, with **190 of 348** commands verified live against Dorico 6.
- **Dual Writing Paths:** Offers direct live caret input for fast interactive editing, and native MusicXML generation powered by music21 for complete multi-voice orchestral scores.

---

## Quick start

### 1. Set up the server
Clone the repository and install the package:

```bash
git clone https://github.com/romanstark/dorico-maestro.git
cd dorico-maestro
python -m venv .venv
```

Activate the environment:
- **Windows (PowerShell):** `.venv\Scripts\activate`
- **macOS / Linux:** `source .venv/bin/activate`

Install dependencies:
```bash
pip install -e ".[dev]"
```

Verify tests without needing Dorico:
```bash
pytest
```

### 2. Connect to Dorico
1. Open **Steinberg Dorico** (version 4, 5, or 6) and open any project. Dorico automatically listens on local WebSocket port `4560`.
2. On first connection, Dorico will display a permission prompt (*"Do you want to allow Dorico Maestro to connect?"*). Click **Authorize**. A persistent session token is saved automatically to `%APPDATA%\dorico-maestro\session_token.json` for future sessions.

### 3. Connect your AI assistant
Add Dorico Maestro to your MCP client configuration (e.g., Claude Desktop, Antigravity IDE, Cursor):

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

Open a project in Dorico, connect, and start with: *"What mode is Dorico currently in?"*

---

## Your music stays yours

- **100% Local:** All communication between your AI assistant and Dorico takes place over a local loopback WebSocket connection (`127.0.0.1:4560`) on your machine.
- **No Telemetry or Cloud Tracking:** Dorico Maestro collects zero analytics and transmits no prompts, scores, or project files to any external server.
- **Local Storage:** Your music, `.dorico` archives, and MusicXML files remain strictly on your local disk.

---

## What stays in your hands

Dorico's Remote Control API is powerful, but certain tasks are intentionally reserved for you in the Dorico interface:

| Task | Why | How to do it |
|---|---|---|
| **Typing into Popovers** | Remote API opens popovers (Shift+D, Shift+T, etc.) but cannot type text into them | Maestro opens the popover and tells you the exact value; you press Enter, or use `write_score(method="musicxml")` |
| **Modal Dialog Confirmation** | File and export dialogs (e.g. MusicXML export) require user confirmation | Confirm the prompt in Dorico, or use `export_pdf` for unattended PDF export |
| **Initial Connection Approval** | Dorico security model requires one-time user authorization | Click **Authorize** on the Dorico permission prompt on first run |
| **Artistic Judgment** | AI can check rules and draft parts, but musical intent and taste belong to you | Guide the score, listen to playback, and refine the music |

---

## Current Verification Status

Verified live (190 commands), grouped by category:
- `NoteInput` 101 of 125: Pitches, durations, accidentals, articulations, chords, tuplets, and popovers.
- `Window` 26 of 29: Window modes, layout views, panels, toolbar, and zoom controls.
- `Play` 24 of 31: Transport commands, playhead placement, and mixer controls.
- `View` 13 of 16: Viewport scrolling and zoom operations.
- `NoteEdit` 8 of 8: Diatonic, chromatic, and octave transposition, plus enharmonic respelling.
- `Edit` 6 of 16: Selection, copy, delete, and undo operations.
- `EventEdit` 4 of 57: Selection navigation commands (`Navigate*`).
- `File` 4 of 11: Project save and MusicXML import/export filters.
- `UI` 3 of 7: Panel focusing commands.
- `Print` 1 of 7: Unattended PDF export (`Print.ExportCurrentLayoutAsPDF`).

The remaining 12 categories are currently untested.

### Testing & Environment
Empirical testing was conducted against **Dorico 6.2.30 Elements** on Windows 11. Dorico restricts commands depending on product tier (SE, Elements, Pro). Commands requiring Pro (such as Lua scripting) return `kUnknownCommand` and are classified as `unavailable` rather than `broken`.

Dorico Maestro exposes **29 tools** and one resource (`dorico://commands`) for complete command discovery.

### A note to Steinberg (and Dorico Pro users)

Dorico Maestro is an independent open-source project developed with a personal **Dorico Elements** license. Advanced features exclusive to **Dorico Pro** (such as Lua scripting via `Script.*`, full engraving options, and advanced dialog automation) are currently classified as `unavailable` or `untested` simply because they cannot be executed on Elements.

If anyone from Steinberg discovers this project and would like to support bringing first-class AI integration to Dorico: an NFR or developer license for **Dorico Pro** would be immensely appreciated to test, verify, and unlock the remaining Pro-specific commands for the entire community. Feel free to get in touch via GitHub or email at [mail@romanstark.de](mailto:mail@romanstark.de)!

---

## Also producing in Ableton Live?

If you also produce music in a DAW, check out **[Ableton Maestro](https://github.com/romanstark/ableton-maestro)**, an MCP server built with the same architecture for Ableton Live. Bridge your workflow between session sketching in Ableton and engraving parts in Dorico with the same AI assistant.

---

## Documentation & Developer Resources

For architectural details, wire protocols, and contributor information:

- [docs/architecture.md](docs/architecture.md) – Internal architecture and multi-layer design
- [docs/protocol.md](docs/protocol.md) – Dorico Remote Control WebSocket protocol specification
- [docs/dorico_command_catalog.md](docs/dorico_command_catalog.md) – Complete base key-command catalog (340 commands)
- [CONTRIBUTING.md](CONTRIBUTING.md) – Guidelines for contributing and command verification
- [LICENSE](LICENSE) – AGPL-3.0 License
