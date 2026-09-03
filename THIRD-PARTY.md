# Third-party notices

Dorico Maestro is licensed under **AGPL-3.0-or-later** (see [LICENSE](LICENSE)). It builds on the following third-party works. None of them restrict Dorico Maestro's own licensing or commercial dual-licensing. This file documents them for good practice and attribution.

## Runtime dependencies

### music21
- **License:** BSD 3-Clause ("New"/"Revised"), compatible with AGPL-3.0.
- **Copyright:** © Michael Scott Asato Cuthbert and the music21 Project (cuthbertLab).
- **Project:** https://github.com/cuthbertLab/music21
- **Use here:** score generation, MusicXML read/write, and music-theory analysis.
- **Note:** music21 is used as an installed dependency. We do not vendor its source, and we do not ship or distribute the music21 corpus.

### Model Context Protocol SDK (`mcp`), `websockets`, `pyyaml`
- Permissive open-source licenses (MIT, BSD, Apache-2.0).
- These libraries form the runtime dependencies declared in `pyproject.toml`. Transitive dependencies (such as `pydantic`) carry permissive open-source licenses.
- Development dependencies (`pytest`, `pytest-asyncio`, `ruff`) are used solely for testing and linting.

## File formats

### MusicXML
- **Format:** Open, standardized notation interchange format developed by the W3C Music Notation Community Group and published royalty-free under the W3C Community Final Specification Agreement.
- **Usage:** Reading and writing `.musicxml` files via music21.

## Steinberg Dorico
Dorico is a commercial product of Steinberg Media Technologies GmbH. Dorico Maestro is an independent project that interfaces with Dorico's Remote Control API. It is not affiliated with or endorsed by Steinberg. "Dorico" and "Steinberg" are trademarks of their respective owners and are used here only descriptively.
