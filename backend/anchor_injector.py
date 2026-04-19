"""
Context_Anchor 注入器
构建并注入结构化锚点信息块，确保模型始终清楚自己的身份、当前话题和其他参与者。
需求：21.1、21.2、21.3、21.4、21.5
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .enums import CollaborationMode
from .models import ModelParticipant, Session


# 游戏模式集合（这些模式中参与者拥有 private_info）
GAME_MODES = {
    CollaborationMode.WEREWOLF,
    CollaborationMode.MURDER_MYSTERY,
    CollaborationMode.UNDERCOVER,
    CollaborationMode.NEGOTIATION,
}


@dataclass
class AnchorFields:
    """锚点字段，用于渲染前的中间表示"""
    topic: str
    mode: str
    custom_id: str
    role_desc: str
    others: List[str] = field(default_factory=list)
    private_info: Optional[str] = None


def _count_tokens(text: str) -> int:
    """简单 token 估算：len(text) // 4（保守估算，中英文通用）"""
    return len(text) // 4


class AnchorInjector:
    """
    构建 Context_Anchor 字符串，注入到每次模型调度的 prompt 开头。

    锚点格式：
        [系统锚点]
        话题：{Topic}
        协作模式：{Collaboration_Mode}
        你的身份：{Custom_ID}（{角色描述}）
        其他参与者：{Custom_ID_1}、{Custom_ID_2}、...
        [锚点结束]

    截断优先级（token 超出 512 时）：
        1. 先截断其他参与者列表（保留前 3 个 + "..."）
        2. 再截断角色描述（截断到 50 字 + "..."）
        3. 最后移除 Collaboration_Mode 字段
        始终保留 Topic 和 Custom_ID
    """

    MAX_ANCHOR_TOKENS = 512

    def build_anchor(
        self,
        participant: ModelParticipant,
        session: Session,
        include_topic: bool = True,
    ) -> str:
        """
        为指定参与者构建 Context_Anchor 字符串。

        游戏模式中仅向对应参与者注入其专属 private_info，
        不向其他参与者暴露（需求 21.5）。
        """
        # 仅游戏模式且该参与者有 private_info 时注入
        private_info: Optional[str] = None
        if session.mode in GAME_MODES:
            private_info = participant.private_info

        fields = AnchorFields(
            topic=session.topic if include_topic else "",
            mode=session.mode.value,
            custom_id=participant.custom_id,
            role_desc=participant.role_desc or "",
            others=[
                p.custom_id
                for p in session.participants
                if p.id != participant.id
            ],
            private_info=private_info,
        )

        anchor = self._render_anchor(fields)

        # 检查 token 数，超出则按优先级截断（需求 21.3）
        if _count_tokens(anchor) > self.MAX_ANCHOR_TOKENS:
            # 第一步：截断其他参与者列表（保留前 3 个 + "..."）
            if len(fields.others) > 3:
                fields.others = fields.others[:3] + ["..."]
            anchor = self._render_anchor(fields)

        if _count_tokens(anchor) > self.MAX_ANCHOR_TOKENS:
            # 第二步：截断角色描述（截断到 50 字 + "..."）
            if len(fields.role_desc) > 50:
                fields.role_desc = fields.role_desc[:50] + "..."
            anchor = self._render_anchor(fields)

        if _count_tokens(anchor) > self.MAX_ANCHOR_TOKENS:
            # 第三步：移除 Collaboration_Mode 字段
            fields.mode = ""
            anchor = self._render_anchor(fields)

        return anchor

    def _render_anchor(self, f: AnchorFields) -> str:
        """
        按固定格式渲染锚点块（需求 21.2）。
        """
        lines = ["[系统锚点]"]

        if f.topic:
            lines.append(f"话题：{f.topic}")

        if f.mode:
            lines.append(f"协作模式：{f.mode}")

        if f.role_desc:
            lines.append(f"你的身份：{f.custom_id}（{f.role_desc}）")
        else:
            lines.append(f"你的身份：{f.custom_id}")

        if f.others:
            lines.append(f"其他参与者：{'、'.join(f.others)}")

        if f.private_info:
            lines.append(f"[私有信息]\n{f.private_info}\n[私有信息结束]")

        lines.append("[锚点结束]")
        return "\n".join(lines)
