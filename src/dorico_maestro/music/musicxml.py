"""Generate and inspect MusicXML via :mod:`music21`.

The Dorico Remote Control API can only *read* the current selection (see
``docs/protocol.md``), so whole-score understanding goes through MusicXML +
music21 instead. This module is the round-trip:

* :func:`generate_musicxml`: build a score from a compact note structure and
  write it to disk (melody, or melody + bass, with optional title, tempo, time
  signature, key and chord symbols).
* :func:`parse_musicxml`: read a MusicXML file back into a plain-dict summary
  (parts, measures, key, time signature, tempo, note count, ambitus per part).

It is also the ``ScoreSpec`` bridge (full-fidelity, carrying key/time/clef/
dynamics/tempo the live caret path cannot enter):

* :func:`score_to_musicxml`: ``ScoreSpec`` -> a MusicXML file on disk.
* :func:`musicxml_to_score`: a MusicXML file -> ``ScoreSpec``.

Everything is headless: files in, files out, no ``.show()``.

Importing a generated file into Dorico is a separate step handled by
``render.import_musicxml`` (``File.Open?File=…&FilterID=MusicXMLImportFilter``; see
``docs/protocol.md`` §8), not by this module.

Note structure accepted by :func:`generate_musicxml`
----------------------------------------------------
``notes`` is either a sequence of note specs (one part, named ``"Melody"``) or a
mapping of part name -> sequence of note specs (multiple parts). Each note spec
is one of:

* ``"C4"``: a single pitch (uses ``default_duration``).
* ``"rest"`` / ``"r"`` / ``""`` / ``None``: a rest.
* ``("C4", "quarter")``: a ``(pitch, duration)`` pair. The pitch may be a list
  (``(["C4", "E4", "G4"], "half")`` -> a chord) or a rest token.
* ``{"pitch": "C4", "duration": 1.0, "lyric": "la"}`` or
  ``{"pitches": ["C4", "E4"], "duration": "half"}``: a mapping form.

A *duration* is a number (quarter-lengths) or a name such as ``"quarter"``,
``"eighth"``, ``"16th"``/``"sixteenth"``, optionally dotted (``"dotted quarter"``
or ``"quarter."``).

Example
-------
>>> generate_musicxml(
...     {"Melody": ["C4", "D4", ("E4", "half")], "Bass": [("C3", "whole")]},
...     "out.musicxml",
...     title="Sketch",
...     tempo_bpm=96,
...     time_signature="4/4",
...     key="C major",
...     chord_symbols=[("C", 0.0), ("G", 2.0)],
... )  # doctest: +SKIP
'out.musicxml'
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from music21 import (
    chord,
    converter,
    duration,
    harmony,
    instrument,
    metadata,
    meter,
    note,
    stream,
    tempo,
)
from music21 import key as key_mod

from dorico_maestro.music.score import ScoreSpec, music21_to_score, score_to_music21
from dorico_maestro.music.theory import parse_key

_REST_TOKENS = {"", "rest", "r"}

# Friendly / British duration names -> music21 duration type.
_DURATION_TYPES: dict[str, str] = {
    "whole": "whole",
    "semibreve": "whole",
    "half": "half",
    "minim": "half",
    "quarter": "quarter",
    "crotchet": "quarter",
    "eighth": "eighth",
    "8th": "eighth",
    "quaver": "eighth",
    "16th": "16th",
    "sixteenth": "16th",
    "semiquaver": "16th",
    "32nd": "32nd",
    "thirty-second": "32nd",
    "demisemiquaver": "32nd",
    "64th": "64th",
    "sixty-fourth": "64th",
    "hemidemisemiquaver": "64th",
}


def generate_musicxml(
    notes: Sequence[Any] | Mapping[str, Sequence[Any]],
    out_path: str | Path,
    *,
    title: str | None = None,
    composer: str | None = None,
    tempo_bpm: float | None = None,
    time_signature: str | None = None,
    key: str | None = None,
    default_duration: str | float = "quarter",
    chord_symbols: Sequence[Any] | None = None,
    instruments: str | Mapping[str, str] | Sequence[str] | None = None,
) -> str:
    """Build a music21 score from ``notes`` and write it to ``out_path``.

    See the module docstring for the shape of ``notes`` and the note specs.
    ``time_signature`` is a string like ``"3/4"``. ``key`` is anything
    :func:`~dorico_maestro.music.theory.parse_key` accepts or an integer number
    of sharps/flats. ``chord_symbols`` is a sequence of ``(figure, offset)``
    pairs (offset in quarter-lengths) applied to the first part. Returns the path
    actually written (as a string).
    """
    part_specs = _normalize_parts(notes)
    if not part_specs:
        raise ValueError("generate_musicxml: no parts/notes provided")

    score = stream.Score()
    # Always attach metadata, ensuring composer defaults to an empty string when
    # unspecified. music21 otherwise writes <creator type="composer">Music21</creator>
    # into the file, which Dorico imports into project info. An explicitly empty
    # composer string suppresses the tag.
    md = metadata.Metadata()
    if title:
        md.title = title
    md.composer = composer or ""
    score.metadata = md

    for index, (part_name, specs) in enumerate(part_specs):
        part = stream.Part()
        part.partName = part_name

        inst = _resolve_instrument(part_name, index, instruments)
        if inst is not None:
            part.insert(0, inst)
        if time_signature:
            part.insert(0, meter.TimeSignature(time_signature))
        if key is not None:
            part.insert(0, _coerce_key(key))
        if index == 0 and tempo_bpm:
            part.insert(0, tempo.MetronomeMark(number=float(tempo_bpm)))

        for i, spec in enumerate(specs):
            try:
                part.append(_build_note_object(spec, default_duration))
            except Exception as exc:
                raise ValueError(f"part {part_name!r} note {i} ({spec!r}): {exc}") from exc

        if index == 0 and chord_symbols:
            _add_chord_symbols(part, chord_symbols)

        score.insert(0, part)

    path = Path(out_path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    written = score.write("musicxml", fp=str(path))
    return str(written)


def parse_musicxml(path: str | Path) -> dict[str, Any]:
    """Parse a MusicXML file into a plain-dict summary.

    The summary has top-level ``path``, ``title``, ``composer``, ``key``,
    ``time_signature``, ``tempo_bpm``, ``part_count``, ``measure_count`` and
    ``note_count``, plus a ``parts`` list where each entry carries ``name``,
    ``measures``, ``notes`` and ``ambitus`` (``{lowest, highest,
    range_semitones}`` or ``None``). Chord symbols are excluded from note counts
    and ambitus. Raises :class:`FileNotFoundError` when the file is missing.
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"MusicXML file not found: {src}")

    score = converter.parse(str(src))

    parts_info: list[dict[str, Any]] = []
    total_notes = 0
    measure_counts: list[int] = []
    for index, part in enumerate(score.parts):
        measures = list(part.getElementsByClass(stream.Measure))
        real_notes = [n for n in part.flatten().notes if not isinstance(n, harmony.Harmony)]
        total_notes += len(real_notes)
        measure_counts.append(len(measures))
        parts_info.append(
            {
                "name": _part_name(part, index),
                "measures": len(measures),
                "notes": len(real_notes),
                "ambitus": _ambitus(part),
            }
        )

    md = score.metadata
    return {
        "path": str(src),
        "title": _safe(lambda: md.bestTitle) if md else None,
        "composer": _safe(lambda: md.composer) if md else None,
        "key": _key_name(score),
        "time_signature": _time_signature(score),
        "tempo_bpm": _tempo_bpm(score),
        "part_count": len(parts_info),
        "measure_count": max(measure_counts) if measure_counts else 0,
        "note_count": total_notes,
        "parts": parts_info,
    }


def read_score(path: str | Path, bars: str | None = None) -> dict[str, Any]:
    """Read a MusicXML file into a per-measure, per-part listing of its notes.

    Unlike :func:`parse_musicxml` (a summary), this returns the actual musical
    content bar by bar, so a caller can inspect the whole piece or a single bar.
    ``bars`` optionally restricts the output to selected measure numbers: a single
    bar (``"8"``), an inclusive range (``"8-12"``) or a comma list (``"8,10,12"``).
    ``None`` returns every bar.

    Returns ``{path, title, composer, key, time_signature, tempo_bpm, part_count,
    measure_count, bars, parts}``. Each part is ``{name, measures}`` and each
    measure is ``{number, events}``. An event carries ``{beat, duration, dots, ql}``
    plus either ``pitches`` (scientific pitch names, one for a note, several for a
    chord) or ``rest: True``, and ``tie`` when the note is tied. Raises
    :class:`FileNotFoundError` when the file is missing and :class:`ValueError` on a
    malformed ``bars`` selector.
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"MusicXML file not found: {src}")

    score = converter.parse(str(src))
    wanted = _parse_bar_spec(bars)

    parts_out: list[dict[str, Any]] = []
    measure_total = 0
    for index, part in enumerate(score.parts):
        measures = list(part.getElementsByClass(stream.Measure))
        measure_total = max(measure_total, len(measures))
        measures_out = [
            {"number": m.number, "events": _measure_events(m)}
            for m in measures
            if wanted is None or m.number in wanted
        ]
        parts_out.append({"name": _part_name(part, index), "measures": measures_out})

    md = score.metadata
    return {
        "path": str(src),
        "title": _safe(lambda: md.bestTitle) if md else None,
        "composer": _safe(lambda: md.composer) if md else None,
        "key": _key_name(score),
        "time_signature": _time_signature(score),
        "tempo_bpm": _tempo_bpm(score),
        "part_count": len(parts_out),
        "measure_count": measure_total,
        "bars": bars or "all",
        "parts": parts_out,
    }


def _parse_bar_spec(bars: str | None) -> set[int] | None:
    """Turn a bar selector into a set of measure numbers.

    Accepts ``"8"``, ``"8-12"`` or ``"8,10,12"``. Returns ``None`` for all bars.
    """
    if bars is None:
        return None
    wanted: set[int] = set()
    for token in str(bars).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, hi = token.split("-", 1)
            wanted.update(range(int(lo), int(hi) + 1))
        else:
            wanted.add(int(token))
    return wanted or None


def _measure_events(measure: stream.Measure) -> list[dict[str, Any]]:
    """List a measure's notes, chords and rests with pitch, duration and beat.

    Chord symbols (:class:`~music21.harmony.Harmony`) are skipped so they never
    masquerade as sounding chords.
    """
    events: list[dict[str, Any]] = []
    for el in measure.notesAndRests:
        if isinstance(el, harmony.Harmony):
            continue
        ev: dict[str, Any] = {
            "beat": _safe(lambda el=el: round(float(el.beat), 3)),
            "duration": el.duration.type,
            "dots": el.duration.dots,
            "ql": round(float(el.duration.quarterLength), 4),
        }
        if isinstance(el, note.Rest):
            ev["rest"] = True
        elif isinstance(el, chord.Chord):
            ev["pitches"] = [p.nameWithOctave for p in el.pitches]
        else:
            ev["pitches"] = [el.nameWithOctave]
        if getattr(el, "tie", None) is not None:
            ev["tie"] = el.tie.type
        events.append(ev)
    return events


# --------------------------------------------------------------------------- #
# ScoreSpec bridge (full-fidelity round-trip)
# --------------------------------------------------------------------------- #


def _blank_composer_unless_set(score: stream.Score) -> None:
    """Ensure score does not emit default library composer credit.

    Scores exported without a composer emit <creator type="composer">Music21</creator>
    unless composer metadata is explicitly set to an empty string. Dorico imports
    this tag into flow metadata.
    """
    md = score.metadata
    if md is None:
        md = metadata.Metadata()
        score.metadata = md
    if not getattr(md, "composer", None):
        md.composer = ""


def score_to_musicxml(spec: ScoreSpec, out_path: str | Path) -> str:
    """Write a :class:`ScoreSpec` to a MusicXML file.

    Full fidelity: delegates to
    :func:`~dorico_maestro.music.score.score_to_music21` and writes the result,
    so it carries the key, time signature, clefs, named dynamics and tempo that
    the live caret path cannot enter. Parent directories are created as needed.
    Returns the path actually written (as a string).
    """
    score = score_to_music21(spec)
    _blank_composer_unless_set(score)
    path = Path(out_path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    written = score.write("musicxml", fp=str(path))
    return str(written)


def musicxml_to_score(path: str | Path) -> ScoreSpec:
    """Read a MusicXML file back into a :class:`ScoreSpec` (full fidelity).

    Parses the file with :mod:`music21` and hands the stream to
    :func:`~dorico_maestro.music.score.music21_to_score`. Raises
    :class:`FileNotFoundError` when the file is missing.
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"MusicXML file not found: {src}")
    score = converter.parse(str(src))
    return music21_to_score(score)


# --------------------------------------------------------------------------- #
# Building internals
# --------------------------------------------------------------------------- #


def _normalize_parts(
    notes: Sequence[Any] | Mapping[str, Sequence[Any]],
) -> list[tuple[str, list[Any]]]:
    """Normalise ``notes`` into an ordered list of ``(part_name, specs)``."""
    if isinstance(notes, Mapping):
        return [(str(name), list(seq)) for name, seq in notes.items()]
    if isinstance(notes, (str, bytes)):
        raise TypeError("notes must be a sequence of note specs or a mapping of parts")
    return [("Melody", list(notes))]


def _split_spec(spec: Any, default_dur: str | float) -> tuple[list[str], Any, str | None]:
    """Split a note spec into ``(pitches, duration, lyric)``.

    An empty ``pitches`` list means a rest.
    """
    if spec is None:
        return [], default_dur, None
    if isinstance(spec, str):
        return ([] if spec.strip().lower() in _REST_TOKENS else [spec.strip()]), default_dur, None
    if isinstance(spec, Mapping):
        dur = spec.get("duration", spec.get("ql", spec.get("quarterLength", default_dur)))
        lyric = spec.get("lyric")
        if "pitches" in spec:
            raw = spec["pitches"] or []
            pitches = [str(x).strip() for x in raw]
        else:
            pitches = _coerce_pitch_field(spec.get("pitch"))
        return pitches, dur, lyric
    if isinstance(spec, (tuple, list)):
        if len(spec) != 2:
            raise ValueError(f"tuple note spec must be (pitch, duration), got {spec!r}")
        return _coerce_pitch_field(spec[0]), spec[1], None
    raise TypeError(f"unsupported note spec: {spec!r}")


def _coerce_pitch_field(value: Any) -> list[str]:
    """Turn a pitch field (str / rest token / iterable of pitches) into a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [] if value.strip().lower() in _REST_TOKENS else [value.strip()]
    return [str(x).strip() for x in value]


def _build_note_object(spec: Any, default_dur: str | float) -> note.GeneralNote:
    """Create a music21 note object from a spec.

    Returns a :class:`Note`, a :class:`Chord` or a :class:`Rest`.
    """
    pitches, dur, lyric = _split_spec(spec, default_dur)
    obj: note.GeneralNote
    if not pitches:
        obj = note.Rest()
    elif len(pitches) == 1:
        obj = note.Note(pitches[0])
    else:
        obj = chord.Chord(pitches)
    obj.duration = _coerce_duration(dur)
    if lyric and pitches:
        obj.lyric = str(lyric)
    return obj


def _coerce_duration(value: Any) -> duration.Duration:
    """Coerce a number or duration name (optionally dotted) to a Duration."""
    if isinstance(value, duration.Duration):
        return value
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        raise TypeError(f"invalid duration: {value!r}")
    if isinstance(value, (int, float)):
        return duration.Duration(quarterLength=float(value))
    if isinstance(value, str):
        text = value.strip().lower()
        dots = 0
        while text.endswith("."):
            dots += 1
            text = text[:-1].strip()
        if text.startswith("dotted "):
            dots = max(dots, 1)
            text = text[len("dotted ") :].strip()
        if text in _DURATION_TYPES:
            dur = duration.Duration(type=_DURATION_TYPES[text])
            if dots:
                dur.dots = dots
            return dur
        try:
            return duration.Duration(quarterLength=float(text))
        except ValueError as exc:
            raise ValueError(f"unknown duration: {value!r}") from exc
    raise ValueError(f"unknown duration: {value!r}")


def _coerce_key(value: str | int) -> key_mod.KeySignature:
    """Coerce a key spec to a key object.

    Accepts a key name or an integer number of sharps/flats.
    """
    if isinstance(value, key_mod.KeySignature):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return key_mod.KeySignature(value)
    text = str(value).strip()
    try:
        return key_mod.KeySignature(int(text))
    except ValueError:
        return parse_key(text)


def _resolve_instrument(
    part_name: str,
    index: int,
    instruments: str | Mapping[str, str] | Sequence[str] | None,
) -> instrument.Instrument | None:
    """Resolve the instrument for a part from the ``instruments`` argument."""
    name: str | None = None
    if instruments is None:
        return None
    if isinstance(instruments, Mapping):
        name = instruments.get(part_name)
    elif isinstance(instruments, str):
        name = instruments if index == 0 else None
    else:
        seq = list(instruments)
        name = seq[index] if index < len(seq) else None
    if not name:
        return None
    inst = _safe(lambda: instrument.fromString(name))
    if inst is not None:
        return inst
    generic = instrument.Instrument()
    generic.instrumentName = name
    return generic


def _add_chord_symbols(part: stream.Part, chord_symbols: Sequence[Any]) -> None:
    """Insert chord symbols into ``part`` at their quarter-length offsets."""
    for entry in chord_symbols:
        if isinstance(entry, Mapping):
            figure = entry.get("figure", entry.get("symbol"))
            offset = float(entry.get("offset", entry.get("ql", 0.0)))
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            figure, offset = entry[0], float(entry[1])
        else:
            figure, offset = entry, 0.0
        if not figure:
            continue
        part.insert(offset, harmony.ChordSymbol(str(figure)))


# --------------------------------------------------------------------------- #
# Parsing internals
# --------------------------------------------------------------------------- #


def _part_name(part: stream.Part, index: int) -> str:
    """Return a best-effort display name for a part."""
    if part.partName:
        return part.partName
    inst = _safe(lambda: part.getInstrument(returnDefault=False))
    if inst is not None and inst.instrumentName:
        return inst.instrumentName
    return f"Part {index + 1}"


def _ambitus(part: stream.Part) -> dict[str, Any] | None:
    """Return a part's lowest/highest sounding pitch (chord symbols excluded)."""
    pitches = []
    for n in part.flatten().notes:
        if isinstance(n, harmony.Harmony):
            continue
        pitches.extend(n.pitches)
    if not pitches:
        return None
    low = min(pitches, key=lambda p: p.ps)
    high = max(pitches, key=lambda p: p.ps)
    return {
        "lowest": low.nameWithOctave,
        "highest": high.nameWithOctave,
        "range_semitones": round(high.ps - low.ps),
    }


def _key_name(score: stream.Score) -> str | None:
    """Return the notated key, else music21's best analysis, else ``None``."""
    keys = list(score.recurse().getElementsByClass(key_mod.Key))
    if keys:
        return keys[0].name
    sigs = list(score.recurse().getElementsByClass(key_mod.KeySignature))
    if sigs:
        return _safe(lambda: sigs[0].asKey().name)
    return _safe(lambda: score.analyze("key").name)


def _time_signature(score: stream.Score) -> str | None:
    sigs = list(score.recurse().getElementsByClass(meter.TimeSignature))
    return sigs[0].ratioString if sigs else None


def _tempo_bpm(score: stream.Score) -> float | None:
    marks = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    if not marks:
        return None
    bpm = _safe(marks[0].getQuarterBPM)
    return round(float(bpm), 3) if bpm is not None else marks[0].number


def _safe(fn: Any, default: Any = None) -> Any:
    """Call ``fn`` and swallow any exception, returning ``default`` instead."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - best-effort probe of optional metadata
        return default
