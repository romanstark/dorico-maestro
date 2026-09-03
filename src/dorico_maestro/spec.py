"""Command specifications and generic command string builder.

A CommandSpec describes a Dorico command as structured data: identifier,
accepted parameters, and execution flags. build() serializes a spec and argument
mapping into the wire format expected by Dorico (Namespace.Command?Key=Value).
validate() validates arguments against declared parameter constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

from dorico_maestro.models import CmdStatus


@dataclass
class ParamSpec:
    """One parameter of a command.

    ``dorico`` is the query key sent to Dorico. When it is ``None`` the
    parameter is *not* serialised into the command string by :func:`build`
    (it is handled specially by a higher layer, e.g. an accidental pre-step).
    """

    name: str
    dorico: str | None = None
    kind: str = "str"  # one of "str" | "int" | "enum"
    enum: list[str] | None = None
    required: bool = False


@dataclass
class CommandSpec:
    """A single Dorico command described as data."""

    id: str
    category: str
    params: list[ParamSpec] = field(default_factory=list)
    status: CmdStatus = CmdStatus.UNTESTED
    requires_note_input: bool = False
    destructive: bool = False
    verify: str | None = None
    doc: str = ""


def validate(spec: CommandSpec, **args: object) -> None:
    """Check ``args`` against ``spec``.

    Raises :class:`ValueError` when an argument is not named by the spec, a
    required parameter is missing, a parameter that must be handled by a higher
    layer (``dorico is None``) is supplied to the generic path, an ``enum`` value
    is not one of the allowed choices, or an ``int`` parameter cannot be cast to
    ``int``. Rejecting unknown/unserialisable arguments is deliberate: it turns a
    silently-dropped value into a loud error (see docs/architecture.md).
    """
    declared = {p.name for p in spec.params}
    unknown = sorted(k for k in args if k not in declared)
    if unknown:
        valid = sorted(declared)
        raise ValueError(
            f"{spec.id}: unknown argument(s) {unknown}; "
            f"valid parameter(s): {valid if valid else 'none'}"
        )
    for p in spec.params:
        if p.name not in args or args[p.name] is None:
            if p.required:
                raise ValueError(f"{spec.id}: missing required parameter {p.name!r}")
            continue
        if p.dorico is None:
            raise ValueError(
                f"{spec.id}: parameter {p.name!r} cannot be sent via build()/run_command "
                "(it is handled by a higher layer, e.g. a note-input pre-step)"
            )
        value = args[p.name]
        if p.kind == "enum" and p.enum is not None and str(value) not in p.enum:
            raise ValueError(
                f"{spec.id}: {p.name}={value!r} is not a valid choice; expected one of {p.enum}"
            )
        if p.kind == "int":
            try:
                int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"{spec.id}: {p.name}={value!r} is not an integer"
                ) from e


def build(spec: CommandSpec, **args: object) -> str:
    """Assemble the Dorico command string for ``spec`` with ``args``.

    Validates first, then serialises every parameter that has a ``dorico`` key
    and was actually supplied, URL-encoding each value. Returns the bare command
    id when there is no query to append.
    """
    validate(spec, **args)
    parts: list[str] = []
    for p in spec.params:
        if p.dorico is None or p.name not in args or args[p.name] is None:
            continue
        parts.append(f"{p.dorico}={quote(str(args[p.name]), safe='')}")
    return f"{spec.id}?{'&'.join(parts)}" if parts else spec.id
