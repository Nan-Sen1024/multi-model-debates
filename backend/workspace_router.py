from __future__ import annotations

import re
from typing import Iterable, List

from .models import ModelParticipant

_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_.-]+)")


def extract_workspace_mentions(text: str) -> List[str]:
    if not text:
        return []

    mentions: List[str] = []
    seen = set()
    for match in _MENTION_PATTERN.findall(text):
        alias = match.strip()
        if not alias:
            continue
        alias_key = alias.lower()
        if alias_key in seen:
            continue
        seen.add(alias_key)
        mentions.append(alias)
    return mentions


def resolve_workspace_targets(
    text: str,
    participants: Iterable[ModelParticipant],
) -> List[ModelParticipant]:
    active_participants = [participant for participant in participants if participant.is_active]
    mentions = extract_workspace_mentions(text)
    if not mentions:
        return active_participants

    mention_map = {participant.custom_id.lower(): participant for participant in active_participants}
    selected: List[ModelParticipant] = []
    seen = set()

    for alias in mentions:
        if alias.lower() == "all":
            return active_participants
        participant = mention_map.get(alias.lower())
        if participant is None:
            continue
        if participant.custom_id.lower() in seen:
            continue
        seen.add(participant.custom_id.lower())
        selected.append(participant)

    return selected or active_participants
