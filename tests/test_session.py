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
    """Ensure natural pitches emit an explicit kNatural accidental pre-step.

    NoteInput.Pitch is diatonic in the active key signature; an explicit natural
    ensures absolute pitch entry regardless of key.
    """
    assert pitch_commands("C4") == [
        "NoteInput.SetAccidental?Type=kNatural",
        "NoteInput.Pitch?Pitch=C&OctaveValue=4",
    ]


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
    # The leading Exit is the toggle guard: NoteInput.Enter switches note input
    # OFF when it is already on, so the session normalises the state first.
    assert client.sent == [
        "NoteInput.Exit",
        "NoteInput.Enter",
        "NoteInput.NoteValue?LogDuration=kCrotchet",
        "NoteInput.SetAccidental?Type=kSharp",
        "NoteInput.Pitch?Pitch=F&OctaveValue=5",
        "NoteInput.RestMode",
        "NoteInput.Pitch?Pitch=X",
        "NoteInput.RestMode",
        "NoteInput.Exit",
    ]


async def test_session_exits_on_error() -> None:
    client = FakeClient()
    with pytest.raises(RuntimeError):
        async with NoteInputSession(client):
            raise RuntimeError("boom")
    # Exit is guaranteed even when the body raises.
    assert client.sent == ["NoteInput.Exit", "NoteInput.Enter", "NoteInput.Exit"]


class ActiveCaretClient(FakeClient):
    """A client whose pushed status says note input is already running."""

    def __init__(self, active: bool) -> None:
        super().__init__()
        self.active = active

    async def status(self, wait: float = 2.0) -> dict[str, bool]:
        return {"noteInputActive": self.active}


async def test_running_note_input_keeps_the_caret() -> None:
    # goto_bar leaves note input on at the bar it reached. Exit then Enter would
    # throw the caret back to the selection start, so nothing is sent around it.
    client = ActiveCaretClient(active=True)
    async with NoteInputSession(client) as session:
        await session.pitch("B4")
    assert client.sent == [
        "NoteInput.SetAccidental?Type=kNatural",
        "NoteInput.Pitch?Pitch=B&OctaveValue=4",
    ]


async def test_keep_caret_false_always_reenters() -> None:
    client = ActiveCaretClient(active=True)
    async with NoteInputSession(client, keep_caret=False):
        pass
    assert client.sent == ["NoteInput.Exit", "NoteInput.Enter", "NoteInput.Exit"]


class RestModeClient(ActiveCaretClient):
    """Note input running and rest mode already switched on."""

    async def status(self, wait: float = 2.0) -> dict[str, bool]:
        return {"noteInputActive": True, "restMode": True}


async def test_rest_leaves_rest_mode_on_when_it_was_on() -> None:
    # The comma key only toggles rest input; the rest itself is the Y key. When
    # rest mode is already on, toggling it would turn it off and Y would repeat
    # the previous pitch instead.
    client = RestModeClient(active=True)
    async with NoteInputSession(client) as session:
        await session.rest()
    assert client.sent == ["NoteInput.Pitch?Pitch=X"]


async def test_fresh_status_is_preferred_over_pushed() -> None:
    class Stale(ActiveCaretClient):
        async def fresh_status(self, wait: float = 2.0) -> dict[str, bool]:
            return {"noteInputActive": True, "restMode": False}

        async def status(self, wait: float = 2.0) -> dict[str, bool]:
            return {"noteInputActive": True, "restMode": True}  # pushed, stale

    client = Stale(active=True)
    async with NoteInputSession(client) as session:
        await session.rest()
    assert client.sent == ["NoteInput.RestMode", "NoteInput.Pitch?Pitch=X", "NoteInput.RestMode"]
