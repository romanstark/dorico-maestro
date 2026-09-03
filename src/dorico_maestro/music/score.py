"""The typed score-spec model and its :mod:`music21` bridge.

``ScoreSpec`` is the single typed contract object the "music brain" works on: the
LLM emits JSON, :func:`score_from_dict` normalises + validates it into a
``ScoreSpec``, and everything else (analysis, MusicXML export, the live caret
render planner) reads that one model. This module is the four-way conversion:

* :func:`score_from_dict`: JSON/dict -> ``ScoreSpec`` (normalise + validate).
* :func:`score_to_dict`: ``ScoreSpec`` -> canonical nested JSON dict.
* :func:`score_to_music21`: ``ScoreSpec`` -> :class:`music21.stream.Score`.
* :func:`music21_to_score`: :class:`music21.stream.Score` -> ``ScoreSpec``.

The canonical shape is nested ``ScoreSpec -> parts -> staves -> voices ->
events``. A flat authoring shortcut (``part.events`` whose events carry
``staff``/``voice``) is accepted and normalised to the nested form;
:func:`score_to_dict` always emits the nested form and round-trips
(``score_from_dict(score_to_dict(spec)) == spec``).

Pitch grammar (``"C4"``/``"F#5"``/``"Bb3"``) is delegated to
:func:`dorico_maestro.session.parse_pitch` so it never drifts from the caret
path. Per the package import graph this module imports only ``models``,
``session.parse_pitch`` and :mod:`music21` (never ``theory``, ``musicxml``,
``render`` or the Dorico client).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from music21 import (
    articulations,
    chord,
    clef,
    duration,
    dynamics,
    instrument,
    layout,
    metadata,
    meter,
    note,
    spanner,
    stream,
    tempo,
    tie,
)
from music21 import key as key_mod

from dorico_maestro.models import (
    ARTICULATION_TO_MUSIC21,
    CLEF_TO_MUSIC21,
    DURATION_FROM_MUSIC21,
    DURATION_QUARTER_LENGTH,
    DURATION_TO_MUSIC21,
    Articulation,
    Clef,
    Dynamic,
    NoteDuration,
    TimeSignature,
    dotted_multiplier,
)
from dorico_maestro.session import parse_pitch

# Tokens (case-insensitive) that mean "this event is a rest".
_REST_TOKENS = {"", "rest", "r"}
# Legal ``slur`` values.
_SLUR_VALUES = {"start", "stop"}
# The only schema major version this loader accepts.
_SCHEMA_MAJOR = "1"

# Reverse maps for reading music21 back into the model.
_ARTICULATION_FROM_MUSIC21: dict[str, Articulation] = {
    v: k for k, v in ARTICULATION_TO_MUSIC21.items()
}
_CLEF_FROM_MUSIC21: dict[str, Clef] = {v: k for k, v in CLEF_TO_MUSIC21.items()}
# Dorico accidental enum -> music21 accidental character (for building pitches).
_ACCIDENTAL_CHAR: dict[str, str] = {"kSharp": "#", "kFlat": "-"}


class ScoreSpecError(ValueError):
    """Invalid score-spec dict/JSON (message is tagged with the offending path)."""


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Event:
    """A single note, chord or rest, discriminated by the ``pitches`` count.

    ``pitches`` empty (or omitted) is a rest, one pitch is a note, two or more is
    a chord. ``staff`` (0-based) and ``voice`` (1-based) are populated by the
    loader so the renderer can read them off any event.
    """

    pitches: list[str] = field(default_factory=list)
    duration: NoteDuration = NoteDuration.QUARTER
    dots: int = 0
    tie: bool = False
    articulations: list[Articulation] = field(default_factory=list)
    dynamic: Dynamic | None = None
    slur: str | None = None
    lyric: str | None = None
    staff: int = 0
    voice: int = 1

    @property
    def is_rest(self) -> bool:
        """True when this event carries no pitches."""
        return not self.pitches

    @property
    def is_chord(self) -> bool:
        """True when this event stacks two or more pitches."""
        return len(self.pitches) >= 2

    @property
    def quarter_length(self) -> float:
        """Length in quarter notes, including rhythmic dots."""
        return DURATION_QUARTER_LENGTH[self.duration] * dotted_multiplier(self.dots)


@dataclass(slots=True)
class Voice:
    """One rhythmic layer of a staff: an ordered list of events."""

    events: list[Event] = field(default_factory=list)
    index: int = 1

    @property
    def quarter_length(self) -> float:
        """Total length of the voice in quarter notes."""
        return sum((e.quarter_length for e in self.events), 0.0)


@dataclass(slots=True)
class Staff:
    """One staff of a part: an optional clef and one or more voices."""

    clef: Clef | None = None
    voices: list[Voice] = field(default_factory=list)
    index: int = 0


@dataclass(slots=True)
class Part:
    """A named instrument/part carrying one or more staves."""

    name: str
    instrument: str | None = None
    abbreviation: str | None = None
    staves: list[Staff] = field(default_factory=list)


@dataclass(slots=True)
class ScoreSpec:
    """A whole flow: parts plus optional global metadata and defaults."""

    parts: list[Part] = field(default_factory=list)
    schema_version: str = "1.0"
    title: str | None = None
    composer: str | None = None
    lyricist: str | None = None
    key: str | None = None
    time: str | None = None
    tempo: float | None = None


# --------------------------------------------------------------------------- #
# dict -> model
# --------------------------------------------------------------------------- #

# Keys allowed at each level of a score-spec dict. Unknown keys are rejected so a
# misplaced value (e.g. ``notes`` instead of ``events``) fails loudly instead of
# silently producing an empty part.
_PART_KEYS = frozenset({"name", "instrument", "abbreviation", "staves", "events"})
_EVENT_KEYS = frozenset(
    {"pitch", "pitches", "kind", "duration", "dots", "tie",
     "articulations", "dynamic", "slur", "lyric", "staff", "voice"}
)
_KEY_HINTS = {
    "notes": "events", "note": "events",
    "pitchs": "pitches", "pitchess": "pitches",
    "dur": "duration", "length": "duration",
    "articulation": "articulations", "dynamics": "dynamic",
    "instr": "instrument", "staffs": "staves", "stave": "staves",
}


def _reject_unknown_keys(raw: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    """Raise if ``raw`` carries keys outside ``allowed`` (with a 'did you mean' hint)."""
    unknown = [k for k in raw if k not in allowed]
    if not unknown:
        return
    named = [
        f"'{k}' (did you mean '{_KEY_HINTS[k]}'?)" if k in _KEY_HINTS else f"'{k}'"
        for k in unknown
    ]
    raise ScoreSpecError(
        f"{path}: unknown key(s) {', '.join(named)}; allowed: {', '.join(sorted(allowed))}"
    )


def score_from_dict(data: Mapping[str, Any]) -> ScoreSpec:
    """Parse, normalise and validate a score-spec dict into a :class:`ScoreSpec`.

    Applies the normalisation rules of the interface contract: ``pitch`` sugar is
    folded into ``pitches``, rest tokens become empty pitch lists, the flat
    authoring shortcut is bucketed into ``staves``/``voices``, and every enum,
    pitch and structural constraint is validated loudly. Any problem raises
    :class:`ScoreSpecError` with a path-tagged message and produces no partial
    model. Bar-sum legality is *not* enforced here (see :func:`validate`).
    """
    if not isinstance(data, Mapping):
        raise ScoreSpecError(f"score spec must be a mapping, got {type(data).__name__}")

    version = data.get("schema_version", "1.0")
    _check_schema_version(version)

    parts_raw = data.get("parts")
    if not isinstance(parts_raw, list) or not parts_raw:
        raise ScoreSpecError("parts: at least one part is required")
    parts = [_part_from_dict(raw, i) for i, raw in enumerate(parts_raw)]

    tempo_raw = data.get("tempo")
    tempo_val = None if tempo_raw is None else _coerce_number("tempo", tempo_raw)

    return ScoreSpec(
        parts=parts,
        schema_version=str(version),
        title=_opt_str("title", data.get("title")),
        composer=_opt_str("composer", data.get("composer")),
        lyricist=_opt_str("lyricist", data.get("lyricist")),
        key=_opt_str("key", data.get("key")),
        time=_opt_str("time", data.get("time")),
        tempo=tempo_val,
    )


def total_events(spec: ScoreSpec) -> int:
    """Count every event across all parts, staves and voices of ``spec``."""
    return sum(len(v.events) for p in spec.parts for s in p.staves for v in s.voices)


def spec_schema() -> dict[str, Any]:
    """The ScoreSpec input contract as data: examples, fields, enums and rules.

    Returned by the ``score_schema`` MCP tool so a caller can see the exact shape
    ``write_score`` / ``render_to_dorico`` expect without reading this module.
    """
    return {
        "summary": (
            "A ScoreSpec is {parts:[...]} plus optional title/composer/key/time/tempo. "
            "Each part uses EITHER the flat 'events' shortcut OR nested 'staves'. "
            "An event with empty 'pitches' is a rest, one pitch is a note, two or more "
            "is a chord."
        ),
        "minimal_flat": {
            "title": "Sketch", "key": "C major", "time": "4/4", "tempo": 96,
            "parts": [
                {"name": "Piano", "instrument": "Piano", "events": [
                    {"pitches": ["C4"], "duration": "quarter"},
                    {"pitches": ["E4", "G4"], "duration": "quarter"},
                    {"pitches": [], "duration": "half"},
                ]},
            ],
        },
        "minimal_nested": {
            "parts": [
                {"name": "Piano", "instrument": "Piano", "staves": [
                    {"clef": "treble", "voices": [
                        {"index": 1, "events": [{"pitches": ["C5"], "duration": "quarter"}]},
                    ]},
                    {"clef": "bass", "voices": [
                        {"index": 1, "events": [{"pitches": ["C3"], "duration": "half"}]},
                    ]},
                ]},
            ],
        },
        "event_fields": {
            "pitches": "list of scientific pitch names (['C4']); [] or omit = rest; 2+ = chord",
            "pitch": "sugar for a single pitch",
            "duration": "one of the duration enum (default 'quarter')",
            "dots": "rhythmic dots 0-2 (default 0)",
            "tie": "bool: tie into the next event",
            "articulations": "list of the articulation enum",
            "dynamic": "one of the dynamic enum (MusicXML path only)",
            "slur": "'start' or 'stop'",
            "lyric": "string",
            "staff": "0-based staff index (flat form)",
            "voice": "1-based voice index (flat form)",
        },
        "enums": {
            "duration": [d.value for d in NoteDuration],
            "articulation": [a.value for a in Articulation],
            "dynamic": [d.value for d in Dynamic],
            "clef": [c.value for c in Clef],
        },
        "rules": [
            "A part uses EITHER 'events' (flat) OR 'staves' (nested), never both.",
            "staff is 0-based; voice is 1-based.",
            "Unknown keys are rejected (e.g. 'notes' -> use 'events').",
            (
                "The live caret enters pitches/durations/dots/ties/articulations/rests/"
                "chords; key/time/clef/named-dynamics/tempo are MusicXML-path only."
            ),
        ],
    }


def _check_schema_version(version: Any) -> None:
    """Reject any schema whose *major* version is not 1."""
    text = str(version)
    major = text.split(".")[0]
    if major != _SCHEMA_MAJOR:
        raise ScoreSpecError(
            f"schema_version: unsupported major version {text!r} (expected 1.x)"
        )


def _part_from_dict(raw: Any, index: int) -> Part:
    """Build one :class:`Part`, choosing the canonical or flat form."""
    path = f"parts[{index}]"
    if not isinstance(raw, Mapping):
        raise ScoreSpecError(f"{path}: part must be a mapping")
    _reject_unknown_keys(raw, _PART_KEYS, path)
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ScoreSpecError(f"{path}: part 'name' is required")

    inst = _opt_str(f"{path}.instrument", raw.get("instrument"))
    abbreviation = _opt_str(f"{path}.abbreviation", raw.get("abbreviation"))

    has_staves = raw.get("staves") is not None
    has_events = raw.get("events") is not None
    if has_staves and has_events:
        raise ScoreSpecError(
            f"{path}: provide either 'staves' (canonical) or 'events' (flat), not both"
        )
    if has_staves:
        staves = _staves_from_list(raw["staves"], path)
    elif has_events:
        staves = _staves_from_flat(raw["events"], path)
    else:
        staves = []

    return Part(name=name, instrument=inst, abbreviation=abbreviation, staves=staves)


def _staves_from_list(raw_list: Any, ppath: str) -> list[Staff]:
    """Parse the canonical ``staves`` list. Reconcile per-event staff/voice."""
    if not isinstance(raw_list, list):
        raise ScoreSpecError(f"{ppath}.staves: must be a list")
    staves: list[Staff] = []
    for g, sraw in enumerate(raw_list):
        spath = f"{ppath}.staves[{g}]"
        if not isinstance(sraw, Mapping):
            raise ScoreSpecError(f"{spath}: staff must be a mapping")
        clef_val = _coerce_clef(sraw.get("clef"), spath)

        voices_raw = sraw.get("voices")
        if voices_raw is None:
            voices_raw = []
        if not isinstance(voices_raw, list):
            raise ScoreSpecError(f"{spath}.voices: must be a list")

        voices: list[Voice] = []
        for j, vraw in enumerate(voices_raw):
            vpath = f"{spath}.voices[{j}]"
            if not isinstance(vraw, Mapping):
                raise ScoreSpecError(f"{vpath}: voice must be a mapping")
            vindex = _coerce_int(f"{vpath}.index", vraw.get("index", j + 1), minimum=1)

            events_raw = vraw.get("events")
            if events_raw is None:
                events_raw = []
            if not isinstance(events_raw, list):
                raise ScoreSpecError(f"{vpath}.events: must be a list")

            events: list[Event] = []
            for k, eraw in enumerate(events_raw):
                epath = f"{vpath}.events[{k}]"
                ev = _event_from_dict(eraw, epath)
                if isinstance(eraw, Mapping):
                    if "staff" in eraw:
                        got = _coerce_int(f"{epath}.staff", eraw["staff"], minimum=0)
                        if got != g:
                            raise ScoreSpecError(
                                f"{epath}: staff {got} disagrees with nesting staff {g}"
                            )
                    if "voice" in eraw:
                        got = _coerce_int(f"{epath}.voice", eraw["voice"], minimum=1)
                        if got != vindex:
                            raise ScoreSpecError(
                                f"{epath}: voice {got} disagrees with nesting voice {vindex}"
                            )
                ev.staff = g
                ev.voice = vindex
                events.append(ev)
            voices.append(Voice(events=events, index=vindex))
        staves.append(Staff(clef=clef_val, voices=voices, index=g))
    return staves


def _staves_from_flat(events_raw: Any, ppath: str) -> list[Staff]:
    """Bucket a flat ``events`` list into staves/voices by their staff/voice keys."""
    if not isinstance(events_raw, list):
        raise ScoreSpecError(f"{ppath}.events: must be a list")

    parsed: list[Event] = []
    max_staff = 0
    for k, eraw in enumerate(events_raw):
        ev = _event_from_dict(eraw, f"{ppath}.events[{k}]")
        max_staff = max(max_staff, ev.staff)
        parsed.append(ev)

    max_voice: dict[int, int] = {}
    for ev in parsed:
        max_voice[ev.staff] = max(max_voice.get(ev.staff, 1), ev.voice)

    staves: list[Staff] = []
    for g in range(max_staff + 1):
        n_voices = max_voice.get(g, 1)
        voices = [Voice(events=[], index=v + 1) for v in range(n_voices)]
        staves.append(Staff(clef=None, voices=voices, index=g))

    for ev in parsed:
        staves[ev.staff].voices[ev.voice - 1].events.append(ev)
    return staves


def _event_from_dict(raw: Any, path: str) -> Event:
    """Build one :class:`Event`, validating every field loudly."""
    if not isinstance(raw, Mapping):
        raise ScoreSpecError(f"{path}: event must be a mapping")
    _reject_unknown_keys(raw, _EVENT_KEYS, path)

    pitches = _event_pitches(raw, path)
    kind = raw.get("kind")
    if kind is not None:
        _check_kind(kind, pitches, path)

    dots = _coerce_int(f"{path}.dots", raw.get("dots", 0), minimum=0)
    if dots > 2:
        raise ScoreSpecError(f"{path}.dots: must be between 0 and 2, got {dots}")

    return Event(
        pitches=pitches,
        duration=_coerce_duration(raw.get("duration", "quarter"), path),
        dots=dots,
        tie=_coerce_bool(f"{path}.tie", raw.get("tie", False)),
        articulations=_coerce_articulations(raw.get("articulations"), path),
        dynamic=_coerce_dynamic(raw.get("dynamic"), path),
        slur=_coerce_slur(raw.get("slur"), path),
        lyric=_opt_str(f"{path}.lyric", raw.get("lyric")),
        staff=_coerce_int(f"{path}.staff", raw.get("staff", 0), minimum=0),
        voice=_coerce_int(f"{path}.voice", raw.get("voice", 1), minimum=1),
    )


def _event_pitches(raw: Mapping[str, Any], path: str) -> list[str]:
    """Resolve the ``pitch``/``pitches`` fields (rest tokens -> empty list)."""
    has_pitch = "pitch" in raw
    has_pitches = "pitches" in raw
    if has_pitch and has_pitches:
        raise ScoreSpecError(f"{path}: provide either 'pitch' or 'pitches', not both")

    names: list[str]
    if has_pitch:
        value = raw.get("pitch")
        if value is None or (isinstance(value, str) and value.strip().lower() in _REST_TOKENS):
            names = []
        elif isinstance(value, str):
            names = [value.strip()]
        else:
            raise ScoreSpecError(f"{path}.pitch: must be a pitch string or rest token")
    elif has_pitches:
        value = raw.get("pitches")
        if value is None:
            names = []
        elif isinstance(value, str):
            names = [] if value.strip().lower() in _REST_TOKENS else [value.strip()]
        elif isinstance(value, list):
            names = [str(x).strip() for x in value]
        else:
            raise ScoreSpecError(f"{path}.pitches: must be a list of pitch strings")
    else:
        names = []

    for name in names:
        try:
            parse_pitch(name)
        except ValueError as exc:
            raise ScoreSpecError(f"{path}: {exc}") from exc
    return names


def _check_kind(kind: Any, pitches: list[str], path: str) -> None:
    """Verify an explicit ``kind`` agrees with the pitch count."""
    if kind not in ("note", "chord", "rest"):
        raise ScoreSpecError(f"{path}.kind: unknown kind {kind!r} (note|chord|rest)")
    n = len(pitches)
    if kind == "rest" and n != 0:
        raise ScoreSpecError(f"{path}: kind 'rest' but {n} pitch(es) given")
    if kind == "note" and n != 1:
        raise ScoreSpecError(f"{path}: kind 'note' needs exactly 1 pitch, got {n}")
    if kind == "chord" and n < 2:
        raise ScoreSpecError(f"{path}: chord needs >=2 pitches")


def _coerce_duration(value: Any, path: str) -> NoteDuration:
    """Coerce a duration name into a :class:`NoteDuration`."""
    if isinstance(value, NoteDuration):
        return value
    if not isinstance(value, str):
        raise ScoreSpecError(f"{path}.duration: must be a string, got {value!r}")
    try:
        return NoteDuration(value)
    except ValueError as exc:
        valid = ", ".join(d.value for d in NoteDuration)
        raise ScoreSpecError(
            f"{path}.duration: unknown duration {value!r} (expected one of {valid})"
        ) from exc


def _coerce_int(path: str, value: Any, *, minimum: int | None = None) -> int:
    """Coerce to a plain int (rejecting bool), enforcing an optional minimum."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoreSpecError(f"{path}: must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ScoreSpecError(f"{path}: must be >= {minimum}, got {value}")
    return value


def _coerce_bool(path: str, value: Any) -> bool:
    """Coerce to a strict bool."""
    if not isinstance(value, bool):
        raise ScoreSpecError(f"{path}: must be a boolean, got {value!r}")
    return value


def _coerce_number(field_name: str, value: Any) -> float:
    """Coerce to a float (rejecting bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoreSpecError(f"{field_name}: must be a number, got {value!r}")
    return float(value)


def _coerce_articulations(value: Any, path: str) -> list[Articulation]:
    """Coerce a list of articulation names into :class:`Articulation` values."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScoreSpecError(f"{path}.articulations: must be a list")
    out: list[Articulation] = []
    for i, item in enumerate(value):
        if isinstance(item, Articulation):
            out.append(item)
            continue
        try:
            out.append(Articulation(item))
        except (ValueError, TypeError) as exc:
            valid = ", ".join(a.value for a in Articulation)
            raise ScoreSpecError(
                f"{path}.articulations[{i}]: unknown articulation {item!r} "
                f"(expected one of {valid})"
            ) from exc
    return out


def _coerce_dynamic(value: Any, path: str) -> Dynamic | None:
    """Coerce a dynamic name into a :class:`Dynamic` (or ``None``)."""
    if value is None:
        return None
    if isinstance(value, Dynamic):
        return value
    try:
        return Dynamic(value)
    except (ValueError, TypeError) as exc:
        valid = ", ".join(d.value for d in Dynamic)
        raise ScoreSpecError(
            f"{path}.dynamic: unknown dynamic {value!r} (expected one of {valid})"
        ) from exc


def _coerce_slur(value: Any, path: str) -> str | None:
    """Validate a slur marker (``"start"``/``"stop"``/``None``)."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in _SLUR_VALUES:
        raise ScoreSpecError(f"{path}.slur: must be 'start', 'stop' or null, got {value!r}")
    return value


def _coerce_clef(value: Any, path: str) -> Clef | None:
    """Coerce a clef name into a :class:`Clef` (or ``None``)."""
    if value is None:
        return None
    if isinstance(value, Clef):
        return value
    try:
        return Clef(value)
    except (ValueError, TypeError) as exc:
        valid = ", ".join(c.value for c in Clef)
        raise ScoreSpecError(
            f"{path}.clef: unknown clef {value!r} (expected one of {valid})"
        ) from exc


def _opt_str(path: str, value: Any) -> str | None:
    """Accept a string or ``None``. Anything else is a loud error."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScoreSpecError(f"{path}: must be a string or null, got {value!r}")
    return value


# --------------------------------------------------------------------------- #
# model -> dict
# --------------------------------------------------------------------------- #


def score_to_dict(spec: ScoreSpec) -> dict[str, Any]:
    """Serialise a :class:`ScoreSpec` to the canonical nested JSON dict.

    Round-trips: ``score_from_dict(score_to_dict(spec)) == spec``. The canonical
    form is always fully nested and never carries the flat ``staff``/``voice``
    per-event shortcut.
    """
    return {
        "schema_version": spec.schema_version,
        "title": spec.title,
        "composer": spec.composer,
        "lyricist": spec.lyricist,
        "key": spec.key,
        "time": spec.time,
        "tempo": spec.tempo,
        "parts": [_part_to_dict(p) for p in spec.parts],
    }


def _part_to_dict(part: Part) -> dict[str, Any]:
    return {
        "name": part.name,
        "instrument": part.instrument,
        "abbreviation": part.abbreviation,
        "staves": [_staff_to_dict(s) for s in part.staves],
    }


def _staff_to_dict(staff: Staff) -> dict[str, Any]:
    return {
        "clef": staff.clef.value if staff.clef is not None else None,
        "voices": [_voice_to_dict(v) for v in staff.voices],
    }


def _voice_to_dict(voice: Voice) -> dict[str, Any]:
    return {
        "index": voice.index,
        "events": [_event_to_dict(e) for e in voice.events],
    }


def _event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "pitches": list(event.pitches),
        "duration": event.duration.value,
        "dots": event.dots,
        "tie": event.tie,
        "articulations": [a.value for a in event.articulations],
        "dynamic": event.dynamic.value if event.dynamic is not None else None,
        "slur": event.slur,
        "lyric": event.lyric,
    }


# --------------------------------------------------------------------------- #
# Non-raising validation
# --------------------------------------------------------------------------- #


def validate(spec: ScoreSpec) -> list[str]:
    """Return a list of human-readable problems ([] means clean).

    A non-raising companion to :func:`score_from_dict` for the analyze/preflight
    tools. Reports an unsupported schema major, empty parts, out-of-range dots,
    unparseable pitches and (as warnings, never fatal) voices whose total
    length is not a whole number of ``spec.time`` bars.
    """
    problems: list[str] = []

    if str(spec.schema_version).split(".")[0] != _SCHEMA_MAJOR:
        problems.append(
            f"schema_version {spec.schema_version!r} has an unsupported major version"
        )
    if not spec.parts:
        problems.append("parts: at least one part is required")

    bar_ql: float | None = None
    if spec.time:
        try:
            bar_ql = TimeSignature.parse(spec.time).bar_quarter_length
        except ValueError:
            problems.append(f"time: invalid time signature {spec.time!r}")

    for pi, part in enumerate(spec.parts):
        for si, staff in enumerate(part.staves):
            for vi, voice in enumerate(staff.voices):
                base = f"parts[{pi}].staves[{si}].voices[{vi}]"
                for ei, ev in enumerate(voice.events):
                    loc = f"{base}.events[{ei}]"
                    if ev.dots < 0 or ev.dots > 2:
                        problems.append(f"{loc}: dots {ev.dots} out of range 0..2")
                    for name in ev.pitches:
                        try:
                            parse_pitch(name)
                        except ValueError as exc:
                            problems.append(f"{loc}: {exc}")
                if bar_ql:
                    total = voice.quarter_length
                    remainder = total % bar_ql
                    if total > 0 and remainder > 1e-6 and abs(remainder - bar_ql) > 1e-6:
                        problems.append(
                            f"{base}: total length {total} is not a whole number "
                            f"of {spec.time} bars"
                        )
    return problems


# --------------------------------------------------------------------------- #
# model -> music21
# --------------------------------------------------------------------------- #


def score_to_music21(spec: ScoreSpec) -> stream.Score:
    """Convert a :class:`ScoreSpec` into a :class:`music21.stream.Score`.

    One music21 ``Part`` per single-staff part. Multi-staff parts use
    :class:`music21.stream.PartStaff` plus a braced
    :class:`music21.layout.StaffGroup` so a MusicXML import yields a grand staff.
    Global metadata, key, time and tempo are applied, along with per-staff clefs
    and per-event dots, ties (linked across events), slurs, articulations,
    dynamics and chords. ``makeMeasures``/``makeTies`` are called so bar lines
    and barline-overflow ties are explicit.
    """
    score = stream.Score()

    md = metadata.Metadata()
    has_md = False
    if spec.title:
        md.title = spec.title
        has_md = True
    if spec.composer:
        md.composer = spec.composer
        has_md = True
    if spec.lyricist:
        md.lyricist = spec.lyricist
        has_md = True
    if has_md:
        score.metadata = md

    key_fields = _parse_key(spec.key) if spec.key else None

    for pindex, part in enumerate(spec.parts):
        staves = part.staves or [Staff(index=0)]
        multi = len(staves) > 1
        staff_streams: list[stream.Stream] = []
        for sidx, staff in enumerate(staves):
            pstream: stream.Stream = stream.PartStaff() if multi else stream.Part()
            if sidx == 0:
                pstream.partName = part.name
                if part.abbreviation:
                    pstream.partAbbreviation = part.abbreviation
                inst = _resolve_instrument(part.instrument)
                if inst is not None:
                    pstream.insert(0, inst)
            if staff.clef is not None:
                pstream.insert(0, _make_clef(staff.clef))
            if spec.time:
                pstream.insert(0, meter.TimeSignature(spec.time))
            if key_fields is not None:
                pstream.insert(0, key_mod.Key(*key_fields))
            if pindex == 0 and sidx == 0 and spec.tempo:
                pstream.insert(0, tempo.MetronomeMark(number=float(spec.tempo)))

            _fill_staff(pstream, staff)
            pstream.makeMeasures(inPlace=True)
            pstream.makeTies(inPlace=True)

            score.insert(0, pstream)
            staff_streams.append(pstream)

        if multi:
            score.insert(0, layout.StaffGroup(staff_streams, symbol="brace"))

    return score


def _fill_staff(pstream: stream.Stream, staff: Staff) -> None:
    """Append a staff's events to ``pstream`` (voice streams when >1 voice)."""
    voices = staff.voices
    if len(voices) <= 1:
        events = voices[0].events if voices else []
        _append_events(pstream, events)
    else:
        for v in voices:
            m21voice = stream.Voice(id=str(v.index))
            _append_events(m21voice, v.events)
            pstream.insert(0, m21voice)


def _append_events(target: stream.Stream, events: list[Event]) -> None:
    """Append notes/rests to ``target`` and apply ties, dynamics and slurs."""
    built: list[tuple[Event, note.GeneralNote, float]] = []
    for ev in events:
        offset = float(target.highestTime)
        obj = _build_general_note(ev)
        target.append(obj)
        built.append((ev, obj, offset))

    _apply_ties(built)

    for ev, _obj, offset in built:
        if ev.dynamic is not None and not ev.is_rest:
            target.insert(offset, dynamics.Dynamic(ev.dynamic.value))

    _apply_slurs(target, built)


def _build_general_note(ev: Event) -> note.GeneralNote:
    """Create a music21 :class:`Note`, :class:`Chord` or :class:`Rest`."""
    dur = duration.Duration(type=DURATION_TO_MUSIC21[ev.duration])
    if ev.dots:
        dur.dots = ev.dots

    obj: note.GeneralNote
    if ev.is_rest:
        obj = note.Rest()
        obj.duration = dur
        return obj

    m21_pitches = [_to_music21_pitch(p) for p in ev.pitches]
    if len(m21_pitches) == 1:
        obj = note.Note(m21_pitches[0])
    else:
        obj = chord.Chord(m21_pitches)
    obj.duration = dur

    for art in ev.articulations:
        cls = getattr(articulations, ARTICULATION_TO_MUSIC21[art], None)
        if cls is not None:
            obj.articulations.append(cls())
    if ev.lyric:
        obj.lyric = ev.lyric
    return obj


def _apply_ties(built: list[tuple[Event, note.GeneralNote, float]]) -> None:
    """Link notes whose event has ``tie=True`` into the following note."""
    for i, (ev, obj, _offset) in enumerate(built):
        if not ev.tie or ev.is_rest:
            continue
        _set_tie(obj, "start")
        if i + 1 < len(built):
            next_ev, next_obj, _ = built[i + 1]
            if not next_ev.is_rest:
                _set_tie(next_obj, "stop")


def _set_tie(obj: note.GeneralNote, kind: str) -> None:
    """Set/merge a tie on ``obj`` (start over an existing stop -> continue)."""
    existing = obj.tie.type if obj.tie is not None else None
    if existing is None:
        obj.tie = tie.Tie(kind)
    elif {existing, kind} == {"start", "stop"}:
        obj.tie = tie.Tie("continue")


def _apply_slurs(target: stream.Stream, built: list[tuple[Event, note.GeneralNote, float]]) -> None:
    """Insert a :class:`music21.spanner.Slur` for each start/stop pair."""
    open_note: note.GeneralNote | None = None
    for ev, obj, _offset in built:
        if ev.slur == "start":
            open_note = obj
        elif ev.slur == "stop" and open_note is not None:
            target.insert(0, spanner.Slur(open_note, obj))
            open_note = None


def _to_music21_pitch(name: str) -> str:
    """Translate a scientific pitch (``"Bb3"``) into music21 form (``"B-3"``)."""
    letter, octave, accidental = parse_pitch(name)
    acc = _ACCIDENTAL_CHAR.get(accidental or "", "")
    return f"{letter}{acc}{octave}"


def _resolve_instrument(name: str | None) -> instrument.Instrument | None:
    """Best-effort music21 instrument for a name (``None`` when unresolved)."""
    if not name:
        return None
    try:
        return instrument.fromString(name)
    except Exception:  # noqa: BLE001 - unknown names are simply not applied
        return None


def _make_clef(clef_value: Clef) -> clef.Clef:
    """Instantiate the music21 clef class for a :class:`Clef` value."""
    cls = getattr(clef, CLEF_TO_MUSIC21[clef_value], None)
    return cls() if cls is not None else clef.TrebleClef()


def _parse_key(text: str) -> tuple[str, str]:
    """Parse a key name into ``(music21 tonic, mode)`` (mirrors ``theory.parse_key``).

    Kept local so this module never imports ``theory`` (import-graph rule).
    """
    raw = str(text).strip()
    if not raw:
        raise ScoreSpecError("key: empty key specification")

    mode: str | None = None
    lowered = raw.lower()
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
            raw = (raw[:idx] + raw[idx + len(token):]).strip()
            break

    token = raw.strip()
    if mode is None and len(token) >= 2 and token[-1] in ("m", "M"):
        mode = "minor" if token[-1] == "m" else "major"
        token = token[:-1].strip()
    if not token:
        raise ScoreSpecError(f"key: no tonic found in {text!r}")
    if mode is None:
        mode = "minor" if token[0].islower() else "major"

    tonic = token[0].upper() + _norm_accidentals(token[1:])
    try:
        parsed = key_mod.Key(tonic, mode)
    except Exception as exc:
        raise ScoreSpecError(f"key: could not parse {text!r}: {exc}") from exc
    return parsed.tonic.name, parsed.mode


def _norm_accidentals(text: str) -> str:
    """Convert a pitch tail's accidentals to music21 form (flat -> ``-``)."""
    out: list[str] = []
    for ch in text:
        if ch in ("#", "♯"):
            out.append("#")
        elif ch in ("b", "-", "♭"):
            out.append("-")
    return "".join(out)


# --------------------------------------------------------------------------- #
# music21 -> model
# --------------------------------------------------------------------------- #


def music21_to_score(score: stream.Score) -> ScoreSpec:
    """Convert a :class:`music21.stream.Score` back into a :class:`ScoreSpec`.

    Best-effort import diagnostics: each music21 part maps to one :class:`Part`
    with a single staff/voice, recovering pitches, durations, dots, ties,
    articulations and (offset-matched) dynamics. Never raises on a parseable
    score.

    A note that overflows a barline is returned as split by music21 (two tied
    notes) rather than re-merged. Bar-overflow ties and composer-authored ties
    are indistinguishable once ``makeTies`` has run (both are simply two tied
    notes of equal pitch on a barline), so silently coalescing them would destroy
    genuine ties. The split form is kept as the honest, lossless representation.
    """
    parts: list[Part] = []
    for pindex, mpart in enumerate(score.parts):
        voice = Voice(events=_events_from_m21_part(mpart), index=1)
        staff = Staff(clef=_m21_clef(mpart), voices=[voice], index=0)
        parts.append(
            Part(
                name=_m21_part_name(mpart, pindex),
                instrument=_m21_instrument_name(mpart),
                staves=[staff],
            )
        )
    if not parts:
        parts = [Part(name="Part 1", staves=[Staff(voices=[Voice()])])]

    md = score.metadata
    return ScoreSpec(
        parts=parts,
        title=_safe(lambda: md.title) if md else None,
        composer=_safe(lambda: md.composer) if md else None,
        lyricist=_safe(lambda: md.lyricist) if md else None,
        key=_m21_key(score),
        time=_m21_time(score),
        tempo=_m21_tempo(score),
    )


def _events_from_m21_part(mpart: stream.Stream) -> list[Event]:
    """Recover events from a music21 part, matching dynamics by offset."""
    flat = mpart.flatten()
    dyn_by_offset: dict[float, str] = {}
    for dyn in flat.getElementsByClass(dynamics.Dynamic):
        if dyn.value:
            dyn_by_offset[round(float(dyn.offset), 6)] = dyn.value

    events: list[Event] = []
    for obj in flat.notesAndRests:
        ev = _event_from_m21(obj)
        offset = round(float(obj.offset), 6)
        if not ev.is_rest and offset in dyn_by_offset:
            try:
                ev.dynamic = Dynamic(dyn_by_offset[offset])
            except ValueError:
                ev.dynamic = None
        events.append(ev)
    return events


def _event_from_m21(obj: note.GeneralNote) -> Event:
    """Build one :class:`Event` from a music21 note/chord/rest."""
    nd = DURATION_FROM_MUSIC21.get(obj.duration.type, NoteDuration.QUARTER)
    dots = min(int(obj.duration.dots or 0), 2)

    if isinstance(obj, note.Rest):
        return Event(pitches=[], duration=nd, dots=dots)

    pitches = [_from_music21_pitch(p) for p in obj.pitches]
    arts: list[Articulation] = []
    for art in getattr(obj, "articulations", []):
        mapped = _ARTICULATION_FROM_MUSIC21.get(type(art).__name__)
        if mapped is not None:
            arts.append(mapped)
    tie_flag = bool(obj.tie is not None and obj.tie.type in ("start", "continue"))
    lyric = obj.lyric if getattr(obj, "lyric", None) else None
    return Event(
        pitches=pitches,
        duration=nd,
        dots=dots,
        tie=tie_flag,
        articulations=arts,
        lyric=lyric,
    )


def _from_music21_pitch(p: Any) -> str:
    """Translate a music21 pitch (``"B-3"``) into scientific form (``"Bb3"``)."""
    return str(p.nameWithOctave).replace("-", "b")


def _m21_clef(mpart: stream.Stream) -> Clef | None:
    """Recover the first clef of a part as a :class:`Clef` (or ``None``)."""
    clefs = list(mpart.recurse().getElementsByClass(clef.Clef))
    if not clefs:
        return None
    return _CLEF_FROM_MUSIC21.get(type(clefs[0]).__name__)


def _m21_part_name(mpart: stream.Stream, index: int) -> str:
    """Best-effort display name for a part."""
    if mpart.partName:
        return str(mpart.partName)
    inst = _safe(lambda: mpart.getInstrument(returnDefault=False))
    if inst is not None and inst.instrumentName:
        return str(inst.instrumentName)
    return f"Part {index + 1}"


def _m21_instrument_name(mpart: stream.Stream) -> str | None:
    """Recover a part's instrument name (``None`` when absent)."""
    inst = _safe(lambda: mpart.getInstrument(returnDefault=False))
    if inst is not None and inst.instrumentName:
        return str(inst.instrumentName)
    return None


def _m21_key(score: stream.Score) -> str | None:
    """Recover a notated key/key-signature name (no analysis fallback)."""
    keys = list(score.recurse().getElementsByClass(key_mod.Key))
    if keys:
        return str(keys[0].name)
    sigs = list(score.recurse().getElementsByClass(key_mod.KeySignature))
    if sigs:
        return _safe(lambda: sigs[0].asKey().name)
    return None


def _m21_time(score: stream.Score) -> str | None:
    """Recover the first time signature as a ``"n/d"`` string."""
    sigs = list(score.recurse().getElementsByClass(meter.TimeSignature))
    return str(sigs[0].ratioString) if sigs else None


def _m21_tempo(score: stream.Score) -> float | None:
    """Recover the first tempo in quarter-note BPM."""
    marks = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    if not marks:
        return None
    bpm = _safe(marks[0].getQuarterBPM)
    if bpm is not None:
        return round(float(bpm), 3)
    return float(marks[0].number) if marks[0].number is not None else None


def _safe(fn: Any, default: Any = None) -> Any:
    """Call ``fn`` and swallow any exception, returning ``default`` instead."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - best-effort probe of optional metadata
        return default
