"""
Session_Snapshot 管理器
负责初始化、更新和渲染会话状态快照。
需求：25.1、25.2、25.3、25.4、25.8、25.9
"""
from __future__ import annotations

import copy
import logging
from typing import Optional

from .models import ModelParticipant, Session, SessionSnapshot

logger = logging.getLogger(__name__)

# 立场摘要最大字符数（中文字符计数）
MAX_SUMMARY_CHARS = 100


class SnapshotManager:
    """
    管理 Session_Snapshot 的生命周期：
    - init_snapshot：初始化快照，所有参与者立场摘要为空字符串
    - update：根据最新发言内容更新对应参与者的立场摘要（截断到 ≤100 字）
    - render_snapshot：将快照渲染为字符串，附加在 Context_Anchor 之后传递给模型

    更新失败时（如 content 为 None）保留上一次成功快照，记录 logging.warning。
    """

    def init_snapshot(self, session: Session) -> SessionSnapshot:
        """
        初始化 Session_Snapshot，所有参与者立场摘要为空字符串。
        需求：25.1
        """
        participant_summaries = {
            p.custom_id: "" for p in session.participants
        }
        snapshot = SessionSnapshot(
            topic=session.topic,
            mode=session.mode,
            participant_summaries=participant_summaries,
            consensus_list=[],
            key_events=[],
        )
        session.snapshot = snapshot
        return snapshot

    def update(
        self,
        session: Session,
        participant: ModelParticipant,
        content: Optional[str],
    ) -> None:
        """
        根据最新发言内容更新对应参与者的立场摘要（截断到 ≤100 字）。
        更新失败时（如 content 为 None）保留上一次成功快照，记录 logging.warning。
        需求：25.3、25.9
        """
        if content is None:
            logger.warning(
                "SnapshotManager.update: content 为 None，参与者 %s 的立场摘要保持不变",
                participant.custom_id,
            )
            return

        try:
            # 截断到 ≤100 个字符（中文字符计数）
            summary = content[:MAX_SUMMARY_CHARS]
            # 保存旧快照以便回滚
            old_summaries = copy.copy(session.snapshot.participant_summaries)
            session.snapshot.participant_summaries[participant.custom_id] = summary
        except Exception as exc:
            logger.warning(
                "SnapshotManager.update: 更新参与者 %s 立场摘要失败，保留上一次快照。错误：%s",
                participant.custom_id,
                exc,
            )
            # 回滚到旧快照
            session.snapshot.participant_summaries = old_summaries

    def add_key_event(self, session: Session, event: str) -> None:
        """
        将关键事件（如用户插入）添加到快照的关键事件列表。
        需求：25.4
        """
        session.snapshot.key_events.append(event)

    def add_consensus(self, session: Session, consensus: str) -> None:
        """
        将已达成的共识添加到快照的共识列表。
        需求：25.1
        """
        session.snapshot.consensus_list.append(consensus)

    def render_snapshot(
        self,
        snapshot: SessionSnapshot,
        include_topic: bool = True,
    ) -> str:
        """
        将 Session_Snapshot 渲染为字符串，附加在 Context_Anchor 之后传递给模型。
        需求：25.2

        渲染格式：
            [会话快照]
            话题：{topic}
            模式：{mode}
            参与者状态：
              {custom_id_1}：{立场摘要}
              {custom_id_2}：{立场摘要}
            共识：{consensus_1}；{consensus_2}
            关键事件：{event_1}；{event_2}
            [快照结束]
        """
        lines = ["[会话快照]"]
        if include_topic:
            lines.append(f"话题：{snapshot.topic}")

        # mode 可能是枚举或字符串
        mode_str = snapshot.mode.value if hasattr(snapshot.mode, "value") else str(snapshot.mode)
        lines.append(f"模式：{mode_str}")

        lines.append("参与者状态：")
        for custom_id, summary in snapshot.participant_summaries.items():
            lines.append(f"  {custom_id}：{summary}")

        consensus_str = "；".join(snapshot.consensus_list) if snapshot.consensus_list else ""
        lines.append(f"共识：{consensus_str}")

        events_str = "；".join(snapshot.key_events) if snapshot.key_events else ""
        lines.append(f"关键事件：{events_str}")

        lines.append("[快照结束]")
        return "\n".join(lines)
