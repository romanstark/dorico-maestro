# Third-party notices

Dorico Maestro is licensed under **AGPL-3.0-or-later** (see [LICENSE](LICENSE)). It
builds on the following third-party works. None of them restrict Dorico Maestro's
own licensing or commercial dual-licensing; this file documents them for good
practice and attribution.

## Runtime dependencies

### music21
- **License:** BSD 3-Clause ("New"/"Revised") — a permissive license, compatible
  with AGPL-3.0.
- **Copyright:** © Michael Scott Asato Cuthbert and the music21 Project (cuthbertLab).
- **Project:** https://github.com/cuthbertLab/music21
- **Use here:** score generation, MusicXML read/write, and music-theory analysis.
- **Note:** we depend on music21 as an installed package (via `pip`); we do **not**
  vendor or redistribute its source, and we do **not** ship or use the music21
  **corpus** (its bundled example scores, which carry their own separate,
  work-by-work licensing). If you ever redistribute music21 itself, retain its BSD
  copyright notice.

### Model Context Protocol SDK (`mcp`) and `websockets`, `pyyaml`, `pydantic`
- Permissive open-source licenses (MIT / BSD / Apache-2.0 family), used as installed
  packages. See each project's own license for details.

## File formats

### MusicXML
- **What:** an open, standardized music-notation interchange format, developed by
  the **W3C Music Notation Community Group** and published royalty-free under the
  W3C Community Final Specification Agreement.
- **Obligations:** none for reading or writing `.musicxml` files. Dorico Maestro does
  not embed or redistribute the MusicXML DTD/XSD schema (music21 handles
  serialization); using the format requires no license or fee.

## Steinberg Dorico
Dorico is a commercial product of Steinberg Media Technologies GmbH. Dorico Maestro
is an independent project that talks to Dorico's Remote Control API; it is not
affiliated with or endorsed by Steinberg. "Dorico" and "Steinberg" are trademarks of
their respective owner and are used here only descriptively.
