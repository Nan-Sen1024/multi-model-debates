"""
MessageStore：消息持久化与归属标注
需求：9.1、22.1、22.2、22.3
"""
from __future__ import annotations

import time
import uuid
from typing import List, Optional

import aiosqlite

from .database import DB_PATH, init_db
from .models import CollaborationMessage, ModelParticipant
from .enums import MessageType


class MessageStore:
    """消息存储与格式化组件"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    # ------------------------------------------------------------------
    # 3.1 消息持久化
    # ------------------------------------------------------------------

    async def store_message(self, message: CollaborationMessage) -> None:
        """
        将消息持久化到 collaboration_messages 表。
        FTS5 索引通过触发器自动同步（database.py 中已定义触发器）。

        存储字段：sender_id、message_type、content、is_masked、
                  is_compressed、drift_score、round_number、created_at
        """
        await init_db(self.db_path)

        created_at_ts = int(message.created_at.timestamp())

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute(
                """
                INSERT INTO collaboration_messages
                    (id, session_id, sender_id, message_type, content,
                     is_masked, is_compressed, drift_score, round_number, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.sender_id,
                    message.message_type.value
                    if isinstance(message.message_type, MessageType)
                    else message.message_type,
                    message.content,
                    1 if message.is_masked else 0,
                    1 if message.is_compressed else 0,
                    message.drift_score,
                    message.round_number,
                    created_at_ts,
                ),
            )
            await db.commit()

    async def load_messages(self, session_id: str) -> List[CollaborationMessage]:
        """
        按 round_number、created_at 顺序加载指定会话的所有消息。
        """
        await init_db(self.db_path)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, session_id, sender_id, message_type, content,
                       is_masked, is_compressed, drift_score, round_number, created_at
                FROM collaboration_messages
                WHERE session_id = ?
                ORDER BY round_number ASC, created_at ASC
                """,
                (session_id,),
            ) as cursor:
                rows = await cursor.fetchall()

        messages: List[CollaborationMessage] = []
        for row in rows:
            from datetime import datetime, timezone

            created_at = datetime.fromtimestamp(row["created_at"], tz=timezone.utc).replace(tzinfo=None)
            msg = CollaborationMessage(
                id=row["id"],
                session_id=row["session_id"],
                sender_id=row["sender_id"],
                message_type=MessageType(row["message_type"]),
                content=row["content"],
                is_masked=bool(row["is_masked"]),
                is_compressed=bool(row["is_compressed"]),
                drift_score=row["drift_score"],
                round_number=row["round_number"] or 0,
                created_at=created_at,
            )
            messages.append(msg)

        return messages

    async def update_drift_score(self, message_id: str, drift_score: float) -> None:
        """更新指定消息的漂移分数。"""
        await init_db(self.db_path)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE collaboration_messages SET drift_score = ? WHERE id = ?",
                (drift_score, message_id),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # 3.3 消息归属标注格式化
    # ------------------------------------------------------------------

    def build_message_history(
        self,
        messages: List[CollaborationMessage],
        participant: Optional[ModelParticipant] = None,
    ) -> str:
        """
        构建传递给模型的消息历史字符串。

        格式规则：
        - 普通消息：[{sender_id}|{message_type}]: {content}
        - is_masked=True：[{sender_id}|{message_type}]: [工具输出已遮蔽]
        - is_compressed=True：[历史摘要]: {content}（不带 sender_id）

        返回所有消息拼接的字符串（每条消息换行分隔）。
        """
        lines: List[str] = []
        for msg in messages:
            msg_type = (
                msg.message_type.value
                if isinstance(msg.message_type, MessageType)
                else msg.message_type
            )
            if msg.is_compressed:
                lines.append(f"[历史摘要]: {msg.content}")
            elif msg.is_masked:
                lines.append(f"[{msg.sender_id}|{msg_type}]: [工具输出已遮蔽]")
            else:
                lines.append(f"[{msg.sender_id}|{msg_type}]: {msg.content}")

        return "\n".join(lines)
