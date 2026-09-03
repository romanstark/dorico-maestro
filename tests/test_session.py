"""Tests for note-input helpers: pitch parsing + the caret lifecycle."""

from __future__ import annotations

import pytest

from dorico_maestro.models import NoteDuration, Response
from dorico_maestro.session import NoteInputSession, parse_pitch, pitch_commands


class FakeClient:
    """Records commands sent. No socket."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, command: str, timeout: float | None = None) -> Response:
        self.sent.append(command)
        return Response(ok=True, code="kOK")


# --------------------------------------------------------------- parse_pitch
def test_parse_pitch_natural() -> None:
    assert parse_pitch("C4") == ("C", 4, None)


def test_parse_pitch_sharp_uses_k_form() -> None:
    assert parse_pitch("F#5") == ("F", 5, "kSharp")


def test_parse_pitch_flat_uses_k_form() -> None:
    assert parse_pitch("Bb3") == ("B", 3, "kFlat")


def test_parse_pitch_lowercase_letter() -> None:
    assert parse_pitch("g2") == ("G", 2, None)


def test_parse_pitch_multi_digit_octave() -> None:
    # Multi-digit octaves must parse fully, not just the final digit.
    assert parse_pitch("C10") == ("C", 10, None)


def test_parse_pitch_negative_octave() -> None:
    assert parse_pitch("C-1") == ("C", -1, None)


@pytest.mark.parametrize("bad", ["", "C", "H4", "C#", "Cx4", "C##4", "4C", "C-", "Cb"])
def test_parse_pitch_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_pitch(bad)


# ------------------------------------------------------------- pitch_commands
def test_pitch_commands_natural() -> None:
    assert pitch_commands("C4") == ["NoteInput.Pitch?Pitch=C&OctaveValue=4"]


def test_pitch_commands_sharp_prestep() -> None:
    assert pitch_commands("F#5") == [
        "NoteInput.SetAccidental?Type=kSharp",
        "NoteInput.Pitch?Pitch=F&OctaveValue=5",
    ]


# ------------------------------------------------------------ NoteInputSession
async def test_session_lifecycle_order() -> None:
    client = FakeClient()
    async with NoteInputSession(client) as session:
        await session.set_duration(NoteDuration.QUARTER)
        await session.pitch("F#5")
        await session.rest()
    assert client.sent == [
        "NoteInput.Enter",
        "NoteInput.NoteValue?LogDuration=kCrotchet",
        "NoteInput.SetAccidental?Type=kSharp",
        "NoteInput.Pitch?Pitch=F&OctaveValue=5",
        "NoteInput.RestMode",
        "NoteInput.Exit",
    ]


async def test_session_exits_on_error() -> None:
    client = FakeClient()
    with pytest.raises(RuntimeError):
        async with NoteInputSession(client):
            raise RuntimeError("boom")
    # Exit is guaranteed even when the body raises.
    assert client.sent == ["NoteInput.Enter", "NoteInput.Exit"]
