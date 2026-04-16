"""
Game master helpers for role-based collaboration modes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .enums import CollaborationMode
from .models import ModelParticipant, Session

FILTERED_CONTENT_PLACEHOLDER = "[内容已过滤：违反角色设定]"


@dataclass
class GameRole:
    name: str
    faction: str
    description: str
    visible_info: str = ""


@dataclass
class VoteResult:
    eliminated_custom_id: Optional[str]
    tally: Dict[str, int] = field(default_factory=dict)
    is_tie: bool = False


class GameMaster:
    def assign_roles(
        self,
        participants: List[ModelParticipant],
        mode: CollaborationMode,
    ) -> Dict[str, GameRole]:
        if mode == CollaborationMode.WEREWOLF:
            return self._assign_werewolf_roles(participants)
        if mode == CollaborationMode.MURDER_MYSTERY:
            return self._assign_murder_mystery_roles(participants)
        if mode == CollaborationMode.UNDERCOVER:
            return self._assign_undercover_roles(participants)
        if mode == CollaborationMode.NEGOTIATION:
            return self._assign_negotiation_roles(participants)
        return {}

    def inject_private_info(self, participant: ModelParticipant, role: GameRole) -> str:
        return (
            f"角色：{role.name}\n"
            f"阵营：{role.faction}\n"
            f"设定：{role.description}\n"
            f"可见信息：{role.visible_info}"
        )

    def conduct_vote_round(self, session: Session) -> VoteResult:
        tally: Dict[str, int] = {}
        valid_targets = {participant.custom_id for participant in session.participants if participant.is_active}

        for summary in session.snapshot.participant_summaries.values():
            if "投票给" not in summary:
                continue
            target = summary.split("投票给", 1)[1].strip().split()[0]
            if target in valid_targets:
                tally[target] = tally.get(target, 0) + 1

        if not tally:
            return VoteResult(eliminated_custom_id=None, tally={}, is_tie=False)

        ordered = sorted(tally.items(), key=lambda item: item[1], reverse=True)
        top_target, top_votes = ordered[0]
        same_top = [target for target, votes in ordered if votes == top_votes]
        if len(same_top) > 1:
            return VoteResult(eliminated_custom_id=None, tally=tally, is_tie=True)

        for participant in session.participants:
            if participant.custom_id == top_target:
                participant.is_active = False
                break
        return VoteResult(eliminated_custom_id=top_target, tally=tally, is_tie=False)

    def check_win_condition(self, session: Session) -> Optional[str]:
        if session.mode == CollaborationMode.WEREWOLF:
            alive = [p for p in session.participants if p.is_active]
            wolves = [p for p in alive if (p.role_desc or "").startswith("狼人")]
            villagers = [p for p in alive if p not in wolves]
            if not wolves:
                return "villagers_win"
            if len(wolves) >= len(villagers):
                return "werewolves_win"
            return None

        if session.mode == CollaborationMode.UNDERCOVER:
            alive_undercover = [
                p for p in session.participants
                if p.is_active and (p.role_desc or "").startswith("卧底")
            ]
            alive_normals = [
                p for p in session.participants
                if p.is_active and not (p.role_desc or "").startswith("卧底")
            ]
            if not alive_undercover:
                return "civilians_win"
            if len(alive_undercover) >= len(alive_normals):
                return "undercover_win"
            return None

        return None

    def filter_private_info_leak(
        self,
        content: str,
        participant: ModelParticipant,
        all_participants: List[ModelParticipant],
    ) -> str:
        normalized_content = self._normalize_text(content)
        for other in all_participants:
            if other.id == participant.id or not other.private_info:
                continue
            for token in self._extract_keywords(other.private_info):
                normalized_token = self._normalize_text(token)
                if normalized_token and normalized_token in normalized_content:
                    return FILTERED_CONTENT_PLACEHOLDER
        return content

    def _assign_werewolf_roles(
        self,
        participants: List[ModelParticipant],
    ) -> Dict[str, GameRole]:
        roles: Dict[str, GameRole] = {}
        for index, participant in enumerate(participants):
            if index == 0:
                roles[participant.id] = GameRole("狼人", "werewolf", "夜间可以秘密行动。", "你知道自己是狼人")
            elif index == 1:
                roles[participant.id] = GameRole("预言家", "villager", "可以查验一名玩家身份。", "你不是狼人")
            elif index == 2:
                roles[participant.id] = GameRole("女巫", "villager", "拥有一次救人与一次毒杀能力。", "你不是狼人")
            else:
                roles[participant.id] = GameRole("村民", "villager", "通过讨论与投票找出狼人。", "你是普通村民")
        return roles

    def _assign_murder_mystery_roles(
        self,
        participants: List[ModelParticipant],
    ) -> Dict[str, GameRole]:
        roles: Dict[str, GameRole] = {}
        templates = [
            GameRole("侦探", "investigator", "主导调查并提出推理。", "掌握初始案件信息"),
            GameRole("嫌疑人", "suspect", "隐藏自己与案件的真实关联。", "你持有关键信息"),
            GameRole("证人", "witness", "提供线索但未必完整。", "你看到过案发现场部分细节"),
        ]
        for index, participant in enumerate(participants):
            roles[participant.id] = templates[index % len(templates)]
        return roles

    def _assign_undercover_roles(
        self,
        participants: List[ModelParticipant],
    ) -> Dict[str, GameRole]:
        roles: Dict[str, GameRole] = {}
        for index, participant in enumerate(participants):
            if index == len(participants) - 1:
                roles[participant.id] = GameRole("卧底", "undercover", "通过描述隐藏自己。", "你的词语与平民不同")
            else:
                roles[participant.id] = GameRole("平民", "civilian", "通过描述与投票识别卧底。", "你与多数玩家共享相近词语")
        return roles

    def _assign_negotiation_roles(
        self,
        participants: List[ModelParticipant],
    ) -> Dict[str, GameRole]:
        roles: Dict[str, GameRole] = {}
        for index, participant in enumerate(participants):
            side = "甲方" if index % 2 == 0 else "乙方"
            roles[participant.id] = GameRole(side, side, "代表本方利益进行谈判。", f"你代表{side}提出条件")
        return roles

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        cleaned = text.replace("\n", " ")
        tokens = []
        for token in cleaned.split():
            normalized = token.strip("，。；;,.!?\n ")
            if normalized:
                tokens.append(normalized)

        phrases = [segment.strip() for segment in re.split(r"[\n,，。；;!?]+", cleaned) if segment.strip()]
        return list(dict.fromkeys(tokens + phrases))

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[\W_]+", "", text).lower()
