from __future__ import annotations

import json
from typing import Iterable, Iterator, List

from .models import StreamEvent


def iter_sse_events(lines: Iterable[str]) -> Iterator[StreamEvent]:
    event_name = ""
    data_lines: List[str] = []

    def flush() -> Iterator[StreamEvent]:
        nonlocal event_name, data_lines
        if not event_name:
            data_lines = []
            return iter(())
        payload_text = "\n".join(data_lines).strip()
        payload = json.loads(payload_text) if payload_text else {}
        event = StreamEvent(event=event_name, payload=payload)
        event_name = ""
        data_lines = []
        return iter((event,))

    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            yield from flush()
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    yield from flush()
