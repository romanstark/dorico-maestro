"""Answer music-theory questions with :mod:`music21`.

Pure musical analysis. This module never touches Dorico's transport. It answers
three common composition questions:

* :func:`analyze_chord`: analyze root, quality, and symbol for a stack of pitches.
* :func:`suggest_progression`: provide a diatonic chord progression in a key.
* :func:`note_in_range`: test whether an instrument can play a given pitch.

It also offers deeper analysis: key detection (:func:`detect_key`),
roman-numeral / functional harmony (:func:`roman_numeral_analysis`),
parallel-perfect and voice-leading checks (:func:`find_parallels`,
:func:`check_voice_leading`), cadence and next-chord suggestions
(:func:`suggest_cadence`, :func:`suggest_next_chord`), instrument range checking
(:func:`check_ranges`) and first-species counterpoint
(:func:`check_species_counterpoint`). Every function is pure and returns plain,
JSON-friendly dicts/lists.

Everything here works headless: no ``.show()``, no external tools. Per the
package import graph this module imports :mod:`music21` and
:mod:`dorico_maestro.music.score` (for the ``ScoreSpec`` type and its music21
bridge), never the Dorico client, ``render`` or ``musicxml``.

Examples
--------
>>> analyze_chord(["C4", "E4", "G4"])["symbol"]
'C'
>>> suggest_progression("C major", 4)
['I', 'IV', 'V', 'I']
>>> note_in_range("violin", "A3")
True
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from music21 import chord, harmony, instrument, interval, note, roman, stream
from music21 import key as key_mod
from music21 import pitch as pitch_mod

from dorico_maestro.music.score import ScoreSpec, score_to_music21

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dorico_maestro.models import InstrumentSpec

# Sentinel returned by music21 when a chord cannot be named.
_UNIDENTIFIED = "Chord Symbol Cannot Be Identified"

# Roman-numeral progression templates, indexed by length, per mode. Curated to be
# musically idiomatic (functional motion, cadence to the tonic where the length
# allows). Lengths outside this table fall back to :func:`_loop_progression`.
_MAJOR_TEMPLATES: dict[int, list[str]] = {
    1: ["I"],
    2: ["I", "V"],
    3: ["I", "IV", "V"],
    4: ["I", "IV", "V", "I"],
    5: ["I", "vi", "IV", "V", "I"],
    6: ["I", "V", "vi", "IV", "V", "I"],
    7: ["I", "iii", "vi", "IV", "ii", "V", "I"],
    8: ["I", "V", "vi", "iii", "IV", "I", "IV", "V"],  # Pachelbel's canon
}
_MINOR_TEMPLATES: dict[int, list[str]] = {
    1: ["i"],
    2: ["i", "V"],
    3: ["i", "iv", "V"],
    4: ["i", "iv", "V", "i"],
    5: ["i", "VI", "iv", "V", "i"],
    6: ["i", "V", "VI", "iv", "V", "i"],
    7: ["i", "VI", "III", "iv", "ii°", "V", "i"],
    8: ["i", "V", "VI", "III", "iv", "i", "iv", "V"],
}


def parse_key(spec: str) -> key_mod.Key:
    """Parse a human key name into a :class:`music21.key.Key`.

    Accepts forms like ``"C"``, ``"c"``, ``"C major"``, ``"a minor"``,
    ``"Eb"``, ``"F# minor"`` and ``"Cm"``. When no mode word is present, an
    upper-case tonic is read as major and a lower-case tonic as minor (the usual
    convention). Raises :class:`ValueError` for anything music21 cannot parse.
    """
    text = str(spec).strip()
    if not text:
        raise ValueError("empty key specification")

    mode: str | None = None
    lowered = text.lower()
    for token, resolved in (
        ("minor", "minor"),
        ("aeolian", "minor"),
        ("min", "minor"),
        ("major", "major"),
        ("ionian", "major"),
        ("maj", "major"),
    ):
        idx = lowered.find(token)
        if idx != -1:
            mode = resolved
            text = (text[:idx] + text[idx + len(token) :]).strip()
            break

    token = text.strip()
    if mode is None and token.endswith(("m", "M")) and len(token) >= 2:
        mode = "minor" if token[-1] == "m" else "major"
        token = token[:-1].strip()
    if not token:
        raise ValueError(f"no tonic found in key {spec!r}")
    if mode is None:
        mode = "minor" if token[0].islower() else "major"

    tonic = token[0].upper() + _normalise_accidentals(token[1:])
    try:
        return key_mod.Key(tonic, mode)
    except Exception as exc:  # music21 raises assorted exception types
        raise ValueError(f"could not parse key {spec!r}: {exc}") from exc


def analyze_chord(pitches: list[str]) -> dict[str, Any]:
    """Describe a chord given its pitches.

    ``pitches`` is a list of scientific pitch names (e.g. ``["C4", "E4", "G4"]``
    or ``["C", "Eb", "G", "Bb"]``). Returns a dict with the root, bass, quality,
    music21's common name, an inferred chord *symbol* (``None`` when the chord
    cannot be named), the inversion and the note count.

    >>> analyze_chord(["C", "Eb", "G", "Bb"])["symbol"]
    'Cm7'
    """
    if not pitches:
        raise ValueError("analyze_chord requires at least one pitch")
    try:
        chd = chord.Chord([str(p).strip() for p in pitches])
    except Exception as exc:
        raise ValueError(f"could not parse pitches {pitches!r}: {exc}") from exc

    figure = _safe(lambda: harmony.chordSymbolFigureFromChord(chd))
    symbol = None if (figure is None or figure == _UNIDENTIFIED) else figure

    return {
        "pitches": [p.nameWithOctave for p in chd.pitches],
        "pitch_classes": [p.name for p in chd.pitches],
        "root": _safe(lambda: chd.root().name),
        "bass": _safe(lambda: chd.bass().name),
        "quality": _safe(lambda: chd.quality),
        "common_name": _safe(lambda: chd.commonName),
        "symbol": symbol,
        "inversion": _safe(chd.inversion),
        "cardinality": len(chd.pitches),
    }


def suggest_progression(key: str, length: int = 4) -> list[str]:
    """Suggest a diatonic chord progression as Roman numerals.

    ``key`` is any string :func:`parse_key` understands. Its *mode* selects
    upper-case (major) or lower-case (minor) numerals. ``length`` is the number
    of chords (must be >= 1). Short, common lengths use curated idiomatic
    templates. Longer requests loop a functional cell and cadence to the tonic.

    >>> suggest_progression("a minor", 4)
    ['i', 'iv', 'V', 'i']
    """
    length = int(length)
    if length < 1:
        raise ValueError("length must be >= 1")
    parsed = parse_key(key)
    minor = parsed.mode == "minor"
    templates = _MINOR_TEMPLATES if minor else _MAJOR_TEMPLATES
    if length in templates:
        return list(templates[length])
    return _loop_progression(length, minor)


def note_in_range(instrument: str, pitch: str) -> bool:
    """Return whether ``pitch`` lies within ``instrument``'s playable range.

    Uses a built-in table of common concert-instrument and voice ranges (with
    aliases like ``"french horn"`` -> horn, ``"cello"`` -> violoncello), falling
    back to music21's instrument metadata. Ranges are sounding pitch and
    deliberately practical rather than record-setting extremes.

    Raises :class:`ValueError` for an unknown instrument or an unparseable pitch.

    >>> note_in_range("cello", "C2")
    True
    >>> note_in_range("piccolo", "C4")
    False
    """
    low, high = instrument_bounds(instrument)
    try:
        target = pitch_mod.Pitch(str(pitch).strip())
    except Exception as exc:
        raise ValueError(f"invalid pitch {pitch!r}: {exc}") from exc
    return low.ps <= target.ps <= high.ps


# --------------------------------------------------------------------------- #
# Key detection & harmonic analysis
# --------------------------------------------------------------------------- #


def detect_key(source: list[str] | ScoreSpec, *, top_n: int = 3) -> dict[str, Any]:
    """Estimate the key of a melody or score (Krumhansl-Schmuckler).

    ``source`` is either a flat list of scientific pitch names or a
    :class:`~dorico_maestro.music.score.ScoreSpec` (all its sounding pitches are
    pooled). Returns ``{key, tonic, mode, confidence, alternatives}`` where
    ``alternatives`` lists up to ``top_n`` runner-up ``{key, confidence}``
    interpretations. ``confidence`` is music21's correlation coefficient (higher
    is a stronger fit). An empty source yields all-``None`` fields.

    Uses the Krumhansl-Kessler weightings explicitly (``analyze("key.krumhansl")``)
    for stable major/minor discrimination on music21 10.5.0 (the plain default
    profile reads a bare major scale as its relative minor).
    """
    names = _collect_pitch_names(source)
    result: dict[str, Any] = {
        "key": None,
        "tonic": None,
        "mode": None,
        "confidence": None,
        "alternatives": [],
    }
    if not names:
        return result

    detected = _safe(lambda: _notes_stream(names).analyze("key.krumhansl"))
    if detected is None:
        return result

    result["key"] = str(detected.name)
    result["tonic"] = str(detected.tonic.name)
    result["mode"] = str(detected.mode)
    result["confidence"] = _round_corr(_safe(lambda: detected.correlationCoefficient))

    alternatives: list[dict[str, Any]] = []
    for alt in (_safe(lambda: detected.alternateInterpretations) or [])[: max(0, top_n)]:
        alternatives.append(
            {
                "key": _safe(lambda a=alt: str(a.name)),
                "confidence": _round_corr(_safe(lambda a=alt: a.correlationCoefficient)),
            }
        )
    result["alternatives"] = alternatives
    return result


def roman_numeral_analysis(
    source: ScoreSpec | list[list[str]], key: str
) -> list[dict[str, Any]]:
    """Label every vertical sonority with a roman numeral in ``key``.

    ``source`` is either a :class:`~dorico_maestro.music.score.ScoreSpec` (its
    sounding verticals are recovered by chordifying) or a list of chords, each a
    list of scientific pitch names. Each result item is
    ``{offset, pitches, roman, figured_bass, function}`` where ``roman`` is
    music21's figure (e.g. ``"I"``, ``"V7"``, ``"ii65"``), ``figured_bass`` is the
    inversion figure (``""``/``None`` in root position) and ``function`` is one of
    ``tonic|subdominant|dominant|other``. Unnameable sonorities keep ``roman``
    ``None`` and never raise. An unparseable ``key`` (e.g. the German ``"H minor"``)
    is tolerated too: the verticals are still returned with ``roman`` ``None``
    rather than propagating a :class:`ValueError` up through the MCP tool.
    """
    parsed = _safe(lambda: parse_key(key))
    items: list[dict[str, Any]] = []
    for offset, pitches in _verticals(source):
        item: dict[str, Any] = {
            "offset": offset,
            "pitches": list(pitches),
            "roman": None,
            "figured_bass": None,
            "function": "other",
        }
        if pitches and parsed is not None:
            rn = _safe(lambda p=pitches: roman.romanNumeralFromChord(_chord(p), parsed))
            if rn is not None:
                item["roman"] = str(rn.figure)
                item["figured_bass"] = str(rn.figuresWritten or "") or None
                item["function"] = _function_for_degree(rn.scaleDegree)
        items.append(item)
    return items


# --------------------------------------------------------------------------- #
# Voice leading
# --------------------------------------------------------------------------- #


def find_parallels(
    upper: list[str], lower: list[str], *, interval_type: str = "both"
) -> list[dict[str, Any]]:
    """Find parallel perfect intervals between two equal-length lines.

    Consecutive vertical intervals that are both perfect fifths (or both
    octaves/unisons) and reached by similar motion are flagged. ``upper`` and
    ``lower`` are lists of scientific pitch names, aligned position-by-position.
    ``interval_type`` selects ``"fifth"``, ``"octave"`` (also catches unisons) or
    ``"both"``. Each item is ``{index, type, from, to}`` where ``type`` is
    ``fifth|octave|unison``, ``index`` is the position of the second interval and
    ``from``/``to`` are ``[upper, lower]`` pitch pairs.
    """
    wanted = _PARALLEL_TARGETS.get(interval_type)
    if wanted is None:
        raise ValueError(
            f"unknown interval_type {interval_type!r} (fifth|octave|both)"
        )

    results: list[dict[str, Any]] = []
    n = min(len(upper), len(lower))
    for i in range(1, n):
        prev = _perfect_class(upper[i - 1], lower[i - 1])
        curr = _perfect_class(upper[i], lower[i])
        if prev is None or curr is None or prev != curr or curr not in wanted:
            continue
        d_upper = _ps(upper[i]) - _ps(upper[i - 1])
        d_lower = _ps(lower[i]) - _ps(lower[i - 1])
        if d_upper == 0 or d_lower == 0:
            continue  # a held voice is oblique motion, not a true parallel
        if (d_upper > 0 and d_lower < 0) or (d_upper < 0 and d_lower > 0):
            continue  # contrary motion cannot form a true parallel
        results.append(
            {
                "index": i,
                "type": curr,
                "from": [upper[i - 1], lower[i - 1]],
                "to": [upper[i], lower[i]],
            }
        )
    return results


def check_voice_leading(spec: ScoreSpec, *, key: str | None = None) -> list[dict[str, Any]]:
    """Check every adjacent voice pair of ``spec`` for voice-leading problems.

    Each staff-voice becomes a melodic line (a chord contributes its top note);
    lines are ordered top to bottom and adjacent pairs are compared. Reports
    parallel perfect fifths/octaves and hidden/direct perfects (``error``),
    voice crossing and overlap (``warning``), oversized melodic leaps
    (``error`` beyond an octave, ``warning`` for a seventh) and (when ``key`` is
    given) an unresolved leading tone (``warning``). Each item is
    ``{severity, rule, message, location}`` with
    ``location = {part, staff, voice, index}``. ``[]`` means clean. Never raises
    on well-formed pitches.
    """
    lines = _extract_lines(spec)
    parsed_key = _safe(lambda: parse_key(key)) if key else None

    issues: list[dict[str, Any]] = []
    for line in lines:
        issues.extend(_melodic_issues(line))
        if parsed_key is not None:
            issues.extend(_leading_tone_issues(line, parsed_key))
    for upper, lower in pairwise(lines):
        issues.extend(_pair_issues(upper, lower))
    return issues


# --------------------------------------------------------------------------- #
# Cadence & progression suggestions
# --------------------------------------------------------------------------- #


def suggest_cadence(key: str, kind: str = "authentic") -> list[str]:
    """Return the roman numerals of a cadence in ``key``.

    ``kind`` is ``authentic`` (``V-I``), ``plagal`` (``IV-I``), ``half``
    (``IV-V``, ending on the dominant) or ``deceptive`` (``V-vi``). Numerals are
    spelled for the key's mode (minor uses lower-case tonic/subdominant and a
    major-quality submediant ``VI``). An unknown ``kind`` raises
    :class:`ValueError`.

    >>> suggest_cadence("C major", "authentic")
    ['V', 'I']
    >>> suggest_cadence("C major", "plagal")
    ['IV', 'I']
    """
    parsed = parse_key(key)
    table = _CADENCES_MINOR if parsed.mode == "minor" else _CADENCES_MAJOR
    normalised = str(kind).strip().lower()
    if normalised not in table:
        raise ValueError(
            f"unknown cadence kind {kind!r} (authentic|plagal|half|deceptive)"
        )
    return list(table[normalised])


def suggest_next_chord(
    key: str, progression: list[str], *, n: int = 3
) -> list[dict[str, Any]]:
    """Rank plausible continuations of a roman-numeral progression in ``key``.

    Given the prior roman numerals, the function of the last chord drives the
    ranking (functional motion plus the cadence patterns of
    :func:`suggest_cadence`). Each item is ``{roman, reason, cadential}`` where
    ``cadential`` marks a suggestion that would complete a cadence. An empty
    ``progression`` suggests a tonic-establishing opening. At most ``n`` items.
    """
    parsed = parse_key(key)
    numerals = _ModeNumerals(parsed.mode == "minor")

    if not progression:
        candidates = [
            (numerals.tonic, "establish the tonic", False),
            (numerals.subdominant, "move to a predominant", False),
            (numerals.dominant, "set up the dominant", False),
        ]
    else:
        function = _function_of_roman(progression[-1], parsed)
        candidates = _successors(function, numerals)

    return [
        {"roman": rn, "reason": reason, "cadential": cadential}
        for rn, reason, cadential in candidates[: max(0, n)]
    ]


# --------------------------------------------------------------------------- #
# Instrument range & counterpoint
# --------------------------------------------------------------------------- #


def check_ranges(
    spec: ScoreSpec, *, instruments: Mapping[str, InstrumentSpec] | None = None
) -> list[dict[str, Any]]:
    """Report notes that fall outside their part's playable range.

    Each part's range is resolved from its ``instrument`` name (via the built-in
    :data:`_RANGES` table / music21 metadata). An optional ``instruments`` map,
    keyed by instrument name, supplies :class:`~dorico_maestro.models.InstrumentSpec`
    overrides whose ``lowest``/``highest`` win. Parts with no instrument or an
    unresolvable range are skipped. Each item is
    ``{part, instrument, pitch, direction, location}`` with ``direction`` either
    ``"above"`` or ``"below"`` and ``location = {part, staff, voice, index}``.
    ``[]`` means every note is playable.
    """
    issues: list[dict[str, Any]] = []
    for pindex, part in enumerate(spec.parts):
        name = part.instrument
        if not name:
            continue
        override = instruments.get(name) if instruments else None
        bounds = _resolve_range(name, override)
        if bounds is None:
            continue
        low_ps, high_ps = bounds
        for sindex, staff in enumerate(part.staves):
            for voice in staff.voices:
                for eindex, event in enumerate(voice.events):
                    for name_of_pitch in event.pitches:
                        pobj = _safe(lambda p=name_of_pitch: pitch_mod.Pitch(str(p).strip()))
                        if pobj is None:
                            continue
                        if pobj.ps < low_ps:
                            direction = "below"
                        elif pobj.ps > high_ps:
                            direction = "above"
                        else:
                            continue
                        issues.append(
                            {
                                "part": part.name,
                                "instrument": name,
                                "pitch": name_of_pitch,
                                "direction": direction,
                                "location": {
                                    "part": pindex,
                                    "staff": sindex,
                                    "voice": voice.index,
                                    "index": eindex,
                                },
                            }
                        )
    return issues


def check_species_counterpoint(
    cantus_firmus: list[str],
    counterpoint: list[str],
    *,
    species: int = 1,
    key: str | None = None,
) -> list[dict[str, Any]]:
    """Check a first-species counterpoint against a cantus firmus.

    Note-against-note (first species only; any other ``species`` raises
    :class:`NotImplementedError`). Enforces the classic rules: begin and end on a
    perfect consonance, only consonant verticals, no parallel/consecutive perfect
    fifths, octaves or unisons (via :func:`find_parallels`), no voice crossing and
    a single melodic climax in the counterpoint. Each item is
    ``{index, rule, message}`` (``index`` is the beat, ``-1`` for whole-line
    issues). ``[]`` means the exercise passes. ``key`` is accepted for API
    symmetry and is currently unused.
    """
    if species != 1:
        raise NotImplementedError(
            f"only first species is implemented (got species={species})"
        )
    _ = key  # reserved for later species (modal degree rules)

    cf = [str(p).strip() for p in cantus_firmus]
    cp = [str(p).strip() for p in counterpoint]
    issues: list[dict[str, Any]] = []
    if len(cf) != len(cp):
        issues.append(
            {
                "index": -1,
                "rule": "length",
                "message": f"lines differ in length ({len(cf)} vs {len(cp)})",
            }
        )
    n = min(len(cf), len(cp))
    if n == 0:
        return issues

    upper, lower = _designate_voices(cf[:n], cp[:n])

    if _perfect_class(cf[0], cp[0]) is None:
        issues.append(
            {
                "index": 0,
                "rule": "opening",
                "message": "must begin on a perfect consonance (unison, fifth or octave)",
            }
        )
    if _perfect_class(cf[n - 1], cp[n - 1]) is None:
        issues.append(
            {
                "index": n - 1,
                "rule": "closing",
                "message": "must end on a perfect consonance (unison, fifth or octave)",
            }
        )

    for i in range(n):
        if not _is_consonant(cf[i], cp[i]):
            issues.append(
                {
                    "index": i,
                    "rule": "dissonance",
                    "message": f"dissonant interval on beat {i + 1}",
                }
            )
        if _ps(upper[i]) < _ps(lower[i]):
            issues.append(
                {
                    "index": i,
                    "rule": "voice-crossing",
                    "message": f"the voices cross on beat {i + 1}",
                }
            )

    for parallel in find_parallels(upper, lower, interval_type="both"):
        kind = parallel["type"]
        issues.append(
            {
                "index": parallel["index"],
                "rule": f"parallel-{kind}",
                "message": f"parallel {kind}s between the voices",
            }
        )

    cp_ps = [_ps(p) for p in cp[:n]]
    peak = max(cp_ps)
    if cp_ps.count(peak) > 1:
        last_peak = len(cp_ps) - 1 - cp_ps[::-1].index(peak)
        issues.append(
            {
                "index": last_peak,
                "rule": "single-climax",
                "message": "the counterpoint should have a single high point",
            }
        )

    issues.sort(key=lambda item: item["index"] if item["index"] >= 0 else 1_000_000)
    return issues


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #

# Sounding ranges (low, high) as scientific pitch names, keyed by canonical name.
_RANGES: dict[str, tuple[str, str]] = {
    "piano": ("A0", "C8"),
    "harpsichord": ("F1", "F6"),
    "organ": ("C2", "C7"),
    "celesta": ("C4", "C8"),
    "harp": ("C1", "G7"),
    "guitar": ("E2", "E6"),
    "bass guitar": ("E1", "G4"),
    "violin": ("G3", "A7"),
    "viola": ("C3", "E6"),
    "violoncello": ("C2", "C6"),
    "double bass": ("E1", "C5"),
    "flute": ("C4", "D7"),
    "piccolo": ("D5", "C8"),
    "oboe": ("Bb3", "A6"),
    "english horn": ("E3", "A5"),
    "clarinet": ("D3", "Bb6"),
    "bass clarinet": ("Bb1", "Eb5"),
    "bassoon": ("Bb1", "Eb5"),
    "contrabassoon": ("Bb0", "Bb3"),
    "soprano saxophone": ("Ab3", "E6"),
    "alto saxophone": ("Db3", "Ab5"),
    "tenor saxophone": ("Ab2", "E5"),
    "baritone saxophone": ("Db2", "Ab4"),
    "trumpet": ("E3", "C6"),
    "horn": ("B1", "F5"),
    "trombone": ("E2", "Bb4"),
    "bass trombone": ("Bb1", "Bb4"),
    "tuba": ("D1", "F4"),
    "timpani": ("D2", "C4"),
    "soprano": ("C4", "A5"),
    "mezzo-soprano": ("A3", "A5"),
    "alto": ("F3", "F5"),
    "tenor": ("C3", "A4"),
    "baritone": ("G2", "G4"),
    "bass": ("E2", "E4"),
}

# Aliases (lower-case) -> canonical key in _RANGES.
_ALIASES: dict[str, str] = {
    "grand piano": "piano",
    "pianoforte": "piano",
    "keyboard": "piano",
    "cello": "violoncello",
    "contrabass": "double bass",
    "string bass": "double bass",
    "upright bass": "double bass",
    "doublebass": "double bass",
    "electric bass": "bass guitar",
    "electric guitar": "guitar",
    "acoustic guitar": "guitar",
    "classical guitar": "guitar",
    "french horn": "horn",
    "f horn": "horn",
    "cor anglais": "english horn",
    "bb clarinet": "clarinet",
    "b-flat clarinet": "clarinet",
    "clarinet in bb": "clarinet",
    "double bassoon": "contrabassoon",
    "saxophone": "alto saxophone",
    "sax": "alto saxophone",
    "alto sax": "alto saxophone",
    "tenor sax": "tenor saxophone",
    "soprano sax": "soprano saxophone",
    "baritone sax": "baritone saxophone",
    "bari sax": "baritone saxophone",
    "bb trumpet": "trumpet",
    "trumpet in bb": "trumpet",
    "kettledrums": "timpani",
    "timpano": "timpani",
    "soprano voice": "soprano",
    "alto voice": "alto",
    "tenor voice": "tenor",
    "bass voice": "bass",
}


def _normalise_accidentals(text: str) -> str:
    """Convert a pitch tail's accidentals to music21 form (flat -> ``-``)."""
    out: list[str] = []
    for ch in text:
        if ch in ("#", "♯"):
            out.append("#")
        elif ch in ("b", "-", "♭"):
            out.append("-")
        # any other character (whitespace, stray letters) is dropped
    return "".join(out)


def _loop_progression(length: int, minor: bool) -> list[str]:
    """Fallback generator: loop a functional cell, cadence to the tonic."""
    cell = ["i", "VI", "iv", "V"] if minor else ["I", "vi", "IV", "V"]
    tonic = "i" if minor else "I"
    prog = [cell[i % len(cell)] for i in range(length)]
    prog[-1] = tonic
    if length >= 2:
        prog[-2] = "V"
    return prog


def instrument_bounds(name: str) -> tuple[pitch_mod.Pitch, pitch_mod.Pitch]:
    """Resolve an instrument name to a ``(low, high)`` pair of pitches."""
    raw = str(name).strip()
    canon = _ALIASES.get(raw.lower(), raw.lower())
    if canon in _RANGES:
        low, high = _RANGES[canon]
        return pitch_mod.Pitch(low), pitch_mod.Pitch(high)

    inst = _safe(lambda: instrument.fromString(raw))
    if inst is not None and inst.lowestNote is not None and inst.highestNote is not None:
        return inst.lowestNote, inst.highestNote

    raise ValueError(f"unknown instrument {name!r}; no range available")


def _safe(fn: Any, default: Any = None) -> Any:
    """Call ``fn`` and swallow any exception, returning ``default`` instead."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - best-effort music21 lookups
        return default


# --------------------------------------------------------------------------- #
# Analysis internals: lookup tables
# --------------------------------------------------------------------------- #

# interval_type argument -> the set of perfect classes :func:`find_parallels`
# reports. "octave" also catches unisons (both are perfect and octave-equivalent).
_PARALLEL_TARGETS: dict[str, set[str]] = {
    "fifth": {"fifth"},
    "octave": {"octave", "unison"},
    "both": {"fifth", "octave", "unison"},
}

# Scale degree (1-7) -> functional category.
_FUNCTION_BY_DEGREE: dict[int, str] = {
    1: "tonic",
    3: "tonic",
    6: "tonic",
    2: "subdominant",
    4: "subdominant",
    5: "dominant",
    7: "dominant",
}

# Consonant simple-interval names. ``simpleName`` folds the octave onto ``P1``, so
# both unison and octave read as "P1" here; the perfect fourth is treated as a
# dissonance (two-part strict counterpoint).
_CONSONANT_SIMPLE: frozenset[str] = frozenset({"P1", "P5", "m3", "M3", "m6", "M6"})

# Cadence templates as roman numerals, per mode.
_CADENCES_MAJOR: dict[str, list[str]] = {
    "authentic": ["V", "I"],
    "plagal": ["IV", "I"],
    "half": ["IV", "V"],
    "deceptive": ["V", "vi"],
}
_CADENCES_MINOR: dict[str, list[str]] = {
    "authentic": ["V", "i"],
    "plagal": ["iv", "i"],
    "half": ["iv", "V"],
    "deceptive": ["V", "VI"],
}


@dataclass(slots=True, frozen=True)
class _ModeNumerals:
    """Roman-numeral spellings of the primary triads for a mode."""

    minor: bool

    @property
    def tonic(self) -> str:
        return "i" if self.minor else "I"

    @property
    def supertonic(self) -> str:
        return "ii°" if self.minor else "ii"

    @property
    def subdominant(self) -> str:
        return "iv" if self.minor else "IV"

    @property
    def dominant(self) -> str:
        return "V"

    @property
    def submediant(self) -> str:
        return "VI" if self.minor else "vi"

    @property
    def leading_tone(self) -> str:
        return "vii°"


@dataclass(slots=True)
class _Line:
    """One melodic line lifted from a staff-voice for voice-leading checks."""

    location: dict[str, int]
    notes: list[tuple[int, str]]  # (event index, top pitch name)
    average: float


# --------------------------------------------------------------------------- #
# Analysis internals: pitch / interval helpers
# --------------------------------------------------------------------------- #


def _collect_pitch_names(source: list[str] | ScoreSpec) -> list[str]:
    """Pool every sounding pitch name from a list or a ``ScoreSpec``."""
    if isinstance(source, ScoreSpec):
        names: list[str] = []
        for part in source.parts:
            for staff in part.staves:
                for voice in staff.voices:
                    for event in voice.events:
                        names.extend(event.pitches)
        return names
    return [str(p).strip() for p in source]


def _notes_stream(names: list[str]) -> stream.Stream:
    """Build a flat music21 stream of notes from scientific pitch names."""
    s = stream.Stream()
    for name in names:
        parsed = _safe(lambda n=name: note.Note(pitch_mod.Pitch(str(n).strip())))
        if parsed is not None:
            s.append(parsed)
    return s


def _round_corr(value: Any) -> float | None:
    """Round a correlation coefficient to 4 dp (``None`` passes through)."""
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _verticals(source: ScoreSpec | list[list[str]]) -> list[tuple[float, list[str]]]:
    """Yield ``(offset, pitch names)`` for each vertical sonority of ``source``."""
    if isinstance(source, ScoreSpec):
        chordified = _safe(lambda: score_to_music21(source).chordify())
        if chordified is None:
            return []
        return [
            (round(float(chd.offset), 4), [_sci(p) for p in chd.pitches])
            for chd in chordified.flatten().getElementsByClass(chord.Chord)
        ]

    verticals: list[tuple[float, list[str]]] = []
    for index, group in enumerate(source):
        pitches = [group.strip()] if isinstance(group, str) else [str(p).strip() for p in group]
        verticals.append((float(index), pitches))
    return verticals


def _chord(pitches: list[str]) -> chord.Chord:
    """Build a music21 chord from scientific pitch names."""
    return chord.Chord([pitch_mod.Pitch(str(p).strip()) for p in pitches])


def _sci(p: Any) -> str:
    """music21 pitch -> scientific name with ``b`` for flats (``"B-3"`` -> ``"Bb3"``)."""
    return str(p.nameWithOctave).replace("-", "b")


def _ps(name: str) -> float:
    """Absolute pitch value in semitones of a scientific pitch name."""
    return float(pitch_mod.Pitch(str(name).strip()).ps)


def _perfect_class(a: str, b: str) -> str | None:
    """Classify the vertical interval of two notes as a perfect consonance.

    Returns ``"unison"``, ``"octave"``, ``"fifth"`` or ``None``. The order of the
    two pitch names does not matter (they are sorted low-to-high first).
    """
    pair = _safe(lambda: (pitch_mod.Pitch(str(a).strip()), pitch_mod.Pitch(str(b).strip())))
    if pair is None:
        return None
    low, high = sorted(pair, key=lambda p: p.ps)
    itv = _safe(lambda: interval.Interval(noteStart=note.Note(low), noteEnd=note.Note(high)))
    if itv is None:
        return None
    semitones = round(itv.semitones)
    if semitones == 0:
        return "unison"
    if semitones % 12 == 0:
        return "octave"
    if itv.simpleName == "P5":
        return "fifth"
    return None


def _is_consonant(a: str, b: str) -> bool:
    """Whether two notes form a consonance (unison/octave, P5, 3rds, 6ths)."""
    pair = _safe(lambda: (pitch_mod.Pitch(str(a).strip()), pitch_mod.Pitch(str(b).strip())))
    if pair is None:
        return False
    low, high = sorted(pair, key=lambda p: p.ps)
    itv = _safe(lambda: interval.Interval(noteStart=note.Note(low), noteEnd=note.Note(high)))
    if itv is None:
        return False
    return itv.simpleName in _CONSONANT_SIMPLE


# --------------------------------------------------------------------------- #
# Analysis internals: harmony helpers
# --------------------------------------------------------------------------- #


def _function_for_degree(degree: Any) -> str:
    """Map a scale degree (1-7) to its harmonic function."""
    if degree is None:
        return "other"
    try:
        return _FUNCTION_BY_DEGREE.get(int(degree), "other")
    except (TypeError, ValueError):
        return "other"


def _function_of_roman(figure: str, parsed_key: key_mod.Key) -> str:
    """Harmonic function of a roman-numeral figure in ``parsed_key``."""
    degree = _safe(lambda: roman.RomanNumeral(str(figure).strip(), parsed_key).scaleDegree)
    return _function_for_degree(degree)


def _successors(function: str, numerals: _ModeNumerals) -> list[tuple[str, str, bool]]:
    """Ranked ``(roman, reason, cadential)`` continuations for a function."""
    if function == "dominant":
        return [
            (numerals.tonic, "authentic cadence (V-I)", True),
            (numerals.submediant, "deceptive cadence (V-vi)", True),
            (numerals.subdominant, "prolong with a subdominant", False),
        ]
    if function == "subdominant":
        return [
            (numerals.dominant, "predominant to dominant", False),
            (numerals.leading_tone, "predominant to a dominant-function chord", False),
            (numerals.tonic, "resolve back to the tonic", True),
        ]
    if function == "tonic":
        return [
            (numerals.subdominant, "tonic to predominant", False),
            (numerals.supertonic, "tonic to predominant", False),
            (numerals.dominant, "tonic to dominant", False),
        ]
    return [
        (numerals.dominant, "approach the dominant", False),
        (numerals.tonic, "return to the tonic", False),
        (numerals.submediant, "a colourful diversion", False),
    ]


# --------------------------------------------------------------------------- #
# Analysis internals: voice-leading & counterpoint helpers
# --------------------------------------------------------------------------- #


def _designate_voices(cf: list[str], cp: list[str]) -> tuple[list[str], list[str]]:
    """Return ``(upper, lower)`` by average pitch (a tie puts counterpoint on top)."""
    cf_avg = sum(_ps(p) for p in cf) / len(cf)
    cp_avg = sum(_ps(p) for p in cp) / len(cp)
    return (cp, cf) if cp_avg >= cf_avg else (cf, cp)


def _extract_lines(spec: ScoreSpec) -> list[_Line]:
    """Lift every staff-voice into a melodic line, ordered top to bottom.

    A chord contributes only its top note (each voice is treated as monophonic);
    rests are omitted from the line.
    """
    lines: list[_Line] = []
    for pindex, part in enumerate(spec.parts):
        for sindex, staff in enumerate(part.staves):
            for voice in staff.voices:
                notes = [
                    (eindex, max(event.pitches, key=_ps))
                    for eindex, event in enumerate(voice.events)
                    if event.pitches
                ]
                if notes:
                    average = sum(_ps(n) for _, n in notes) / len(notes)
                    lines.append(
                        _Line(
                            location={"part": pindex, "staff": sindex, "voice": voice.index},
                            notes=notes,
                            average=average,
                        )
                    )
    lines.sort(key=lambda ln: ln.average, reverse=True)
    return lines


def _melodic_issues(line: _Line) -> list[dict[str, Any]]:
    """Oversized melodic leaps within a single line."""
    issues: list[dict[str, Any]] = []
    for j in range(1, len(line.notes)):
        prev_name = line.notes[j - 1][1]
        index, name = line.notes[j]
        leap = abs(_ps(name) - _ps(prev_name))
        if leap > 12:
            issues.append(
                _vl_issue(
                    "error",
                    "oversized-leap",
                    f"melodic leap of {int(leap)} semitones exceeds an octave",
                    line.location,
                    index,
                )
            )
        elif leap in (10, 11):
            issues.append(
                _vl_issue(
                    "warning",
                    "large-leap",
                    "melodic leap of a seventh",
                    line.location,
                    index,
                )
            )
    return issues


def _leading_tone_issues(line: _Line, parsed_key: key_mod.Key) -> list[dict[str, Any]]:
    """Flag a leading tone that fails to resolve up to the tonic."""
    tonic_pc = parsed_key.tonic.pitchClass
    leading_pc = (tonic_pc - 1) % 12
    issues: list[dict[str, Any]] = []
    for j in range(len(line.notes) - 1):
        if pitch_mod.Pitch(line.notes[j][1]).pitchClass != leading_pc:
            continue
        next_index, next_name = line.notes[j + 1]
        if pitch_mod.Pitch(next_name).pitchClass != tonic_pc:
            issues.append(
                _vl_issue(
                    "warning",
                    "unresolved-leading-tone",
                    "leading tone does not resolve up to the tonic",
                    line.location,
                    next_index,
                )
            )
    return issues


def _pair_issues(upper: _Line, lower: _Line) -> list[dict[str, Any]]:
    """Parallels, hidden perfects, crossing and overlap between two lines."""
    m = min(len(upper.notes), len(lower.notes))
    issues: list[dict[str, Any]] = []
    if m == 0:
        return issues
    upper_names = [name for _, name in upper.notes[:m]]
    lower_names = [name for _, name in lower.notes[:m]]
    upper_indices = [index for index, _ in upper.notes[:m]]
    lower_label = _loc_str(lower.location)

    for parallel in find_parallels(upper_names, lower_names, interval_type="both"):
        kind = parallel["type"]
        issues.append(
            _vl_issue(
                "error",
                f"parallel-{kind}",
                f"parallel {kind}s with {lower_label}",
                upper.location,
                upper_indices[parallel["index"]],
            )
        )

    for k in range(m):
        if _ps(lower_names[k]) > _ps(upper_names[k]):
            issues.append(
                _vl_issue(
                    "warning",
                    "voice-crossing",
                    f"the lower voice rises above {_loc_str(upper.location)}",
                    upper.location,
                    upper_indices[k],
                )
            )
        if k > 0 and (
            _ps(lower_names[k]) > _ps(upper_names[k - 1])
            or _ps(upper_names[k]) < _ps(lower_names[k - 1])
        ):
            issues.append(
                _vl_issue(
                    "warning",
                    "voice-overlap",
                    f"the voices overlap with {lower_label}",
                    upper.location,
                    upper_indices[k],
                )
            )

    issues.extend(
        _hidden_perfect_issues(upper_names, lower_names, upper_indices, upper.location, lower_label)
    )
    return issues


def _hidden_perfect_issues(
    upper_names: list[str],
    lower_names: list[str],
    upper_indices: list[int],
    location: dict[str, int],
    lower_label: str,
) -> list[dict[str, Any]]:
    """Perfect fifth/octave reached by similar motion in both voices."""
    issues: list[dict[str, Any]] = []
    for i in range(1, len(upper_names)):
        curr = _perfect_class(upper_names[i], lower_names[i])
        if curr not in ("fifth", "octave"):
            continue
        if _perfect_class(upper_names[i - 1], lower_names[i - 1]) == curr:
            continue  # already a true parallel (reported by find_parallels)
        d_upper = _ps(upper_names[i]) - _ps(upper_names[i - 1])
        d_lower = _ps(lower_names[i]) - _ps(lower_names[i - 1])
        if d_upper == 0 or d_lower == 0:
            continue  # oblique motion is fine
        if (d_upper > 0) != (d_lower > 0):
            continue  # contrary motion is fine
        issues.append(
            _vl_issue(
                "warning",
                f"hidden-{curr}",
                f"{curr} approached by similar motion with {lower_label}",
                location,
                upper_indices[i],
            )
        )
    return issues


def _vl_issue(
    severity: str, rule: str, message: str, location: dict[str, int], index: int
) -> dict[str, Any]:
    """Build a voice-leading issue dict.

    The location is ``{part, staff, voice, index}``.
    """
    return {
        "severity": severity,
        "rule": rule,
        "message": message,
        "location": {
            "part": location["part"],
            "staff": location["staff"],
            "voice": location["voice"],
            "index": index,
        },
    }


def _loc_str(location: dict[str, int]) -> str:
    """Human-readable label for a voice location."""
    return f"voice {location['voice']} (part {location['part']}, staff {location['staff']})"


def _resolve_range(name: str, override: InstrumentSpec | None) -> tuple[float, float] | None:
    """Resolve an instrument's ``(low_ps, high_ps)``; ``override`` bounds win.

    Returns ``None`` when neither the built-in table nor the override yields a
    complete low/high pair.
    """
    base = _safe(lambda: instrument_bounds(name))
    low_ps = base[0].ps if base is not None else None
    high_ps = base[1].ps if base is not None else None
    if override is not None:
        if override.lowest:
            low_ps = _safe(lambda: pitch_mod.Pitch(override.lowest).ps)
        if override.highest:
            high_ps = _safe(lambda: pitch_mod.Pitch(override.highest).ps)
    if low_ps is None or high_ps is None:
        return None
    return float(low_ps), float(high_ps)
