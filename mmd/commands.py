from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class ParsedInputKind(str, Enum):
    MESSAGE = "message"
    COMMAND = "command"


@dataclass(frozen=True)
class ParsedInput:
    kind: ParsedInputKind
    text: str = ""
    command: str = ""
    args: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ParticipantSpec:
    alias: str
    model_ref: str
    role_desc: str | None = None


def parse_shell_input(line: str) -> ParsedInput:
    text = str(line or "").strip()
    if not text:
        return ParsedInput(kind=ParsedInputKind.MESSAGE, text="")

    if text.startswith("/"):
        command_line = text[1:].strip()
        if not command_line:
            return ParsedInput(kind=ParsedInputKind.COMMAND, command="")
        try:
            tokens = shlex.split(command_line)
        except ValueError as exc:
            raise ValueError(f"Invalid command line: {exc}") from exc
        command = tokens[0].strip() if tokens else ""
        args = tuple(token.strip() for token in tokens[1:] if token.strip())
        return ParsedInput(kind=ParsedInputKind.COMMAND, command=command, args=args)

    return ParsedInput(kind=ParsedInputKind.MESSAGE, text=text)


def parse_participant_spec(spec: str) -> ParticipantSpec:
    text = str(spec or "").strip()
    if not text:
        raise ValueError("participant spec is required")

    tokens = shlex.split(text)
    if not tokens:
        raise ValueError("participant spec is required")

    if len(tokens) == 1:
        alias, separator, model_ref = tokens[0].partition("=")
        if not separator:
            raise ValueError("participant spec must look like alias=model or alias model")
        alias = alias.strip()
        model_ref = model_ref.strip()
        role_desc = None
    else:
        alias = tokens[0].strip()
        model_ref = tokens[1].strip()
        role_desc = " ".join(tokens[2:]).strip() or None

    if not alias or not model_ref:
        raise ValueError("participant spec must include both alias and model")

    return ParticipantSpec(alias=alias, model_ref=model_ref, role_desc=role_desc)
