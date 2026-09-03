"""Musical logic for Dorico Maestro using music21 without transport coupling.

This package contains MusicXML processing (`musicxml`) and music theory analysis
(`theory`). It maintains strict decoupling from network transport and execution
components.
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
