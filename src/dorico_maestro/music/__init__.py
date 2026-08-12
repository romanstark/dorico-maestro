"""Musical logic for Dorico Maestro — pure :mod:`music21`, no transport.

This package is the "music layer": MusicXML round-trip (:mod:`musicxml`) and
theory helpers (:mod:`theory`). It never imports the Dorico client or executor;
per ``docs/architecture.md`` the transport, command semantics and musical logic
stay strictly separate.
"""

from __future__ import annotations

from dorico_maestro.music.musicxml import (
    generate_musicxml,
    musicxml_to_score,
    parse_musicxml,
    score_to_musicxml,
)
from dorico_maestro.music.theory import (
    analyze_chord,
    check_ranges,
    check_species_counterpoint,
    check_voice_leading,
    detect_key,
    find_parallels,
    note_in_range,
    parse_key,
    roman_numeral_analysis,
    suggest_cadence,
    suggest_next_chord,
    suggest_progression,
)

__all__ = [
    "analyze_chord",
    "check_ranges",
    "check_species_counterpoint",
    "check_voice_leading",
    "detect_key",
    "find_parallels",
    "generate_musicxml",
    "musicxml_to_score",
    "note_in_range",
    "parse_key",
    "parse_musicxml",
    "roman_numeral_analysis",
    "score_to_musicxml",
    "suggest_cadence",
    "suggest_next_chord",
    "suggest_progression",
]
